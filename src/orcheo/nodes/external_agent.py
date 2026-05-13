"""Shared task node for CLI-backed external coding agents."""

from __future__ import annotations
import logging
from collections.abc import Mapping
from typing import Any, ClassVar
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.external_agents import (
    ExternalAgentRuntimeManager,
    RuntimeInstallError,
    RuntimeVerificationError,
    WorkingDirectoryValidationError,
    execute_process,
)
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.runtime.credentials import (
    CredentialReferenceNotFoundError,
    CredentialResolverUnavailableError,
)


logger = logging.getLogger(__name__)


class ExternalAgentNode(TaskNode):
    """Base task node for provider-managed external coding agents."""

    provider_name: ClassVar[str]
    optional_auth_fields: ClassVar[frozenset[str]] = frozenset()
    runtime_manager_class: ClassVar[type[ExternalAgentRuntimeManager]] = (
        ExternalAgentRuntimeManager
    )

    prompt: str | None = Field(
        default=None,
        description="Task prompt for the external coding agent.",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Optional system instructions prepended to the task.",
    )
    working_directory: str | None = Field(
        default=None,
        description="Git worktree path the agent should run inside.",
    )
    auto_init_git_worktree: bool = Field(
        default=True,
        description=(
            "Initialize a Git worktree in the working directory when the path is "
            "safe but not yet inside one."
        ),
    )
    timeout_seconds: int = Field(
        default=1800,
        ge=1,
        description="Maximum execution time before the agent process tree is stopped.",
    )

    def _resolve_prompt(self, state: State) -> str:
        """Resolve the task prompt from the node config or workflow inputs."""
        if self.prompt and self.prompt.strip():
            return self.prompt.strip()

        inputs = state.get("inputs", {})
        if isinstance(inputs, Mapping):
            for key in ("prompt", "query", "message", "input"):
                value = inputs.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        msg = (
            f"{self.__class__.__name__} requires a prompt or a workflow input named "
            "'prompt', 'query', 'message', or 'input'."
        )
        raise ValueError(msg)

    def _resolve_working_directory_input(self, state: State) -> str:
        """Resolve the requested working directory from node config or inputs."""
        if self.working_directory and self.working_directory.strip():
            return self.working_directory.strip()

        inputs = state.get("inputs", {})
        if isinstance(inputs, Mapping):
            for key in ("working_directory", "workspace", "repo_path", "path"):
                value = inputs.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        msg = (
            f"{self.__class__.__name__} requires a working_directory or an input named "
            "'working_directory', 'workspace', 'repo_path', or 'path'."
        )
        raise ValueError(msg)

    def _compute_run_updates(self, state: State) -> dict[str, Any]:
        """Resolve templates while tolerating missing optional auth placeholders."""
        updates: dict[str, Any] = {}
        for key, value in self.__dict__.items():
            try:
                decoded = self._decode_value(value, state)
            except (
                CredentialReferenceNotFoundError,
                CredentialResolverUnavailableError,
            ):
                if key not in self.optional_auth_fields:
                    raise
                decoded = None
            if decoded is not value:
                updates[key] = decoded
        return updates

    def auth_environment_overrides(self) -> dict[str, str]:
        """Return provider-specific auth values for the current node run."""
        return {}

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Resolve the provider runtime, validate setup, and run the CLI."""
        del config
        manager = self.runtime_manager_class()
        try:
            prompt = self._resolve_prompt(state)
            working_directory_input = self._resolve_working_directory_input(state)
            workspace_id = state.get("workspace_id")
            if not isinstance(workspace_id, str) or not workspace_id.strip():
                workspace_id = None
            working_directory = manager.validate_working_directory(
                working_directory_input,
                workspace_id=workspace_id,
                auto_init_git_worktree=self.auto_init_git_worktree,
            )
        except (ValueError, WorkingDirectoryValidationError) as exc:
            return {
                "status": "failed",
                "provider": self.provider_name,
                "resolved_version": None,
                "command": [],
                "command_path": None,
                "working_directory": None,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "reason": "invalid_configuration",
                "message": str(exc),
            }

        try:
            resolution = await manager.resolve_runtime(self.provider_name)
        except RuntimeInstallError as exc:
            return {
                "status": "failed",
                "provider": self.provider_name,
                "resolved_version": None,
                "command": exc.command,
                "command_path": exc.command[0] if exc.command else None,
                "working_directory": str(working_directory),
                "exit_code": None,
                "stdout": exc.stdout,
                "stderr": exc.stderr,
                "reason": "install_failed",
                "message": str(exc),
            }
        except RuntimeVerificationError as exc:
            return {
                "status": "failed",
                "provider": self.provider_name,
                "resolved_version": None,
                "command": [],
                "command_path": None,
                "working_directory": str(working_directory),
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "reason": "runtime_verification_failed",
                "message": str(exc),
            }

        provider = manager.get_provider(self.provider_name)
        runtime = resolution.runtime
        provider_environ = manager.environment_for_provider(
            self.provider_name,
            workspace_id=workspace_id,
        )
        provider_environ.update(self.auth_environment_overrides())
        auth_probe = provider.probe_auth(runtime, environ=provider_environ)
        if not auth_probe.authenticated:
            self._set_trace_metadata_for_run(
                {
                    "external_agent": {
                        "provider": self.provider_name,
                        "resolved_version": runtime.version,
                        "command_path": str(runtime.executable_path),
                        "maintenance_due": resolution.maintenance_due,
                    }
                }
            )
            return {
                "status": "setup_needed",
                "provider": self.provider_name,
                "resolved_version": runtime.version,
                "command": [],
                "command_path": str(runtime.executable_path),
                "working_directory": str(working_directory),
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "reason": "auth_required",
                "commands": provider.render_login_instructions(runtime),
                "message": auth_probe.message,
            }

        manager.mark_auth_success(self.provider_name)
        command = provider.build_command(
            runtime,
            prompt=prompt,
            system_prompt=self.system_prompt,
        )
        audit_metadata = provider.execution_audit_metadata(
            runtime,
            command=command,
            working_directory=working_directory,
        )
        if audit_metadata:
            logger.info(
                "External agent execution requested provider bypass flags.",
                extra={"node_name": self.name, **audit_metadata},
            )
        result = await execute_process(
            command,
            cwd=working_directory,
            env=provider.build_environment(provider_environ),
            timeout_seconds=self.timeout_seconds,
        )
        self._set_trace_metadata_for_run(
            {
                "external_agent": {
                    "provider": self.provider_name,
                    "resolved_version": runtime.version,
                    "command_path": str(runtime.executable_path),
                    "maintenance_due": resolution.maintenance_due,
                }
            }
        )

        status = "succeeded"
        reason: str | None = None
        message: str | None = None
        if result.timed_out:
            status = "failed"
            reason = "timeout"
            message = (
                f"{self.provider_name} timed out after {self.timeout_seconds} seconds."
            )
        elif result.exit_code not in (0, None):
            status = "failed"
            reason = "non_zero_exit"
            message = f"{self.provider_name} exited with code {result.exit_code}."

        return {
            "status": status,
            "provider": self.provider_name,
            "resolved_version": runtime.version,
            "command": command,
            "command_path": str(runtime.executable_path),
            "working_directory": str(working_directory),
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "reason": reason,
            "message": message,
            "maintenance_due": resolution.maintenance_due,
        }
