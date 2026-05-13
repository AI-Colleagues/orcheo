"""Shared runtime manager for external agent CLI providers."""

from __future__ import annotations
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta
from pathlib import Path
from orcheo.external_agents.manifest import RuntimeManifestStore, provider_lock
from orcheo.external_agents.models import (
    ResolvedRuntime,
    RuntimeInstallError,
    RuntimeManifest,
    RuntimeResolution,
    RuntimeVerificationError,
)
from orcheo.external_agents.paths import (
    ensure_runtime_root,
    provider_environment_path,
    provider_root,
    provider_runtimes_dir,
    provider_staging_dir,
    validate_working_directory,
    workspace_provider_environment_overrides,
)
from orcheo.external_agents.process import execute_process
from orcheo.external_agents.providers import DEFAULT_PROVIDERS, ExternalAgentProvider


DEFAULT_MAINTENANCE_INTERVAL = timedelta(days=7)
_ACTIVE_ENVIRONMENT_OVERRIDES: ContextVar[dict[str, str] | None] = ContextVar(
    "external_agent_environment_overrides",
    default=None,
)


@contextmanager
def scoped_external_agent_environment(
    overrides: Mapping[str, str],
) -> Iterator[None]:
    """Apply task-local environment overrides for external-agent runtime managers."""
    token: Token[dict[str, str] | None] = _ACTIVE_ENVIRONMENT_OVERRIDES.set(
        dict(overrides)
    )
    try:
        yield
    finally:
        _ACTIVE_ENVIRONMENT_OVERRIDES.reset(token)


class ExternalAgentRuntimeManager:
    """Resolve, install, and maintain provider runtimes."""

    def __init__(
        self,
        *,
        runtime_root: Path | None = None,
        providers: Mapping[str, ExternalAgentProvider] | None = None,
        environ: Mapping[str, str] | None = None,
        maintenance_interval: timedelta = DEFAULT_MAINTENANCE_INTERVAL,
    ) -> None:
        """Initialize the runtime manager with a managed runtime root."""
        self.runtime_root = ensure_runtime_root(runtime_root)
        self.providers = dict(providers or DEFAULT_PROVIDERS)
        self.environ = dict(os.environ)
        active_overrides = _ACTIVE_ENVIRONMENT_OVERRIDES.get()
        if active_overrides is not None:
            self.environ.update(active_overrides)
        if environ is not None:
            self.environ.update(environ)
        self.maintenance_interval = maintenance_interval
        self.manifest_store = RuntimeManifestStore(self.runtime_root)

    def get_provider(self, provider_name: str) -> ExternalAgentProvider:
        """Return the registered provider adapter for ``provider_name``."""
        try:
            return self.providers[provider_name]
        except KeyError as exc:
            msg = f"Unknown external agent provider '{provider_name}'."
            raise ValueError(msg) from exc

    def environment_for_provider(
        self,
        provider_name: str,
        workspace_id: str | None = None,
    ) -> dict[str, str]:
        """Return the effective environment for one provider."""
        merged = self._load_provider_environment(
            provider_name,
            workspace_id=workspace_id,
        )
        merged.update(self.environ)
        merged.update(
            workspace_provider_environment_overrides(
                provider_name,
                workspace_id=workspace_id,
            )
        )
        return merged

    def save_provider_environment(
        self,
        provider_name: str,
        updates: Mapping[str, str],
        workspace_id: str | None = None,
    ) -> dict[str, str]:
        """Persist provider-specific environment variables."""
        with provider_lock(self.runtime_root, provider_name):
            current = self._load_provider_environment(
                provider_name,
                workspace_id=workspace_id,
            )
            for key, value in updates.items():
                stripped = value.strip()
                if stripped:
                    current[key] = stripped
                    self.environ[key] = stripped
                else:
                    current.pop(key, None)
                    self.environ.pop(key, None)
            self._write_provider_environment(
                provider_name,
                current,
                workspace_id=workspace_id,
            )
            return current

    def maintenance_due(
        self,
        manifest: RuntimeManifest | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return whether maintenance is due for ``manifest``."""
        if manifest is None:
            return True
        reference_now = now or datetime.now(UTC)
        last_reference = manifest.last_checked_at or manifest.installed_at
        if last_reference is None:
            return True
        return reference_now - last_reference >= self.maintenance_interval

    def validate_working_directory(
        self,
        candidate: str | Path,
        *,
        workspace_id: str | None = None,
        workspace_root: str | Path | None = None,
        auto_init_git_worktree: bool = False,
    ) -> Path:
        """Validate a requested execution directory against runtime safety rules."""
        return validate_working_directory(
            candidate,
            runtime_root=self.runtime_root,
            workspace_id=workspace_id,
            workspace_root=workspace_root,
            auto_init_git_worktree=auto_init_git_worktree,
        )

    def inspect_runtime(
        self,
        provider_name: str,
    ) -> tuple[ResolvedRuntime | None, RuntimeManifest | None]:
        """Return the currently installed runtime and manifest without installing."""
        provider = self.get_provider(provider_name)
        manifest = self.manifest_store.load(provider_name)
        return self._runtime_from_manifest(provider, manifest), manifest

    async def resolve_runtime(self, provider_name: str) -> RuntimeResolution:
        """Resolve a pinned runtime, installing the provider if absent."""
        provider = self.get_provider(provider_name)
        with provider_lock(self.runtime_root, provider_name):
            manifest = self.manifest_store.load(provider_name)
            runtime = self._runtime_from_manifest(provider, manifest)
            if runtime is None:
                runtime, manifest = await self._install_latest_locked(
                    provider, manifest
                )
            assert manifest is not None
            return RuntimeResolution(
                runtime=runtime,
                manifest=manifest,
                maintenance_due=self.maintenance_due(manifest),
            )

    async def run_maintenance(self, provider_name: str) -> RuntimeResolution:
        """Check for a newer latest runtime and safely activate it if verified."""
        provider = self.get_provider(provider_name)
        with provider_lock(self.runtime_root, provider_name):
            manifest = self.manifest_store.load(provider_name)
            if manifest is None:
                runtime, manifest = await self._install_latest_locked(provider, None)
                manifest.last_checked_at = datetime.now(UTC)
                self.manifest_store.save(manifest)
                return RuntimeResolution(
                    runtime=runtime,
                    manifest=manifest,
                    maintenance_due=False,
                )

            current_runtime = self._runtime_from_manifest(provider, manifest)
            if current_runtime is None:
                runtime, manifest = await self._install_latest_locked(
                    provider, manifest
                )
                manifest.last_checked_at = datetime.now(UTC)
                self.manifest_store.save(manifest)
                return RuntimeResolution(
                    runtime=runtime,
                    manifest=manifest,
                    maintenance_due=False,
                )

            if not self.maintenance_due(manifest):
                return RuntimeResolution(
                    runtime=current_runtime,
                    manifest=manifest,
                    maintenance_due=False,
                )

            checked_at = datetime.now(UTC)
            try:
                runtime, manifest = await self._install_latest_locked(
                    provider, manifest
                )
            except Exception:
                manifest.last_checked_at = checked_at
                self.manifest_store.save(manifest)
                raise

            manifest.last_checked_at = checked_at
            self.manifest_store.save(manifest)
            self._cleanup_superseded_runtimes(provider_name, manifest)
            return RuntimeResolution(
                runtime=runtime,
                manifest=manifest,
                maintenance_due=False,
            )

    def mark_auth_success(self, provider_name: str) -> RuntimeManifest:
        """Persist a successful auth probe timestamp for ``provider_name``."""
        with provider_lock(self.runtime_root, provider_name):
            manifest = self.manifest_store.load(provider_name)
            if manifest is None:
                msg = (
                    "Cannot record auth success without a manifest for "
                    f"'{provider_name}'."
                )
                raise ValueError(msg)
            manifest.last_auth_ok_at = datetime.now(UTC)
            return self.manifest_store.save(manifest)

    def _runtime_from_manifest(
        self,
        provider: ExternalAgentProvider,
        manifest: RuntimeManifest | None,
    ) -> ResolvedRuntime | None:
        """Rebuild a resolved runtime from a manifest if the binary still exists."""
        if manifest is None:
            return None

        candidates: list[tuple[str | None, Path | None]] = [
            (manifest.current_version, manifest.current_runtime_path),
            (manifest.previous_version, manifest.previous_runtime_path),
        ]
        for version, runtime_path in candidates:
            if version is None or runtime_path is None:
                continue
            executable_path = Path(runtime_path) / "bin" / provider.executable_name
            if executable_path.exists():
                return ResolvedRuntime(
                    provider=provider.name,
                    version=version,
                    install_dir=Path(runtime_path),
                    executable_path=executable_path,
                    package_name=provider.package_name,
                    installed_at=manifest.installed_at,
                )
        return None

    async def _install_latest_locked(
        self,
        provider: ExternalAgentProvider,
        manifest: RuntimeManifest | None,
    ) -> tuple[ResolvedRuntime, RuntimeManifest]:
        """Install the latest provider runtime while the provider lock is held."""
        provider_name = provider.name
        provider_runtime_dir = provider_runtimes_dir(self.runtime_root, provider_name)
        provider_runtime_dir.mkdir(parents=True, exist_ok=True)

        staging_root = provider_staging_dir(self.runtime_root, provider_name)
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = staging_root / f"install-{uuid.uuid4().hex}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        install_command = provider.install_command(staging_dir)
        install_result = await execute_process(
            install_command,
            env=provider.build_environment(self.environ),
            timeout_seconds=600,
        )
        if install_result.exit_code != 0:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise RuntimeInstallError(
                provider_name,
                f"Failed to install latest runtime for '{provider_name}'.",
                command=install_command,
                stdout=install_result.stdout,
                stderr=install_result.stderr,
            )

        staged_executable = staging_dir / "bin" / provider.executable_name
        if not staged_executable.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
            msg = (
                f"Installed runtime for '{provider_name}' did not produce "
                f"'{staged_executable}'."
            )
            raise RuntimeVerificationError(msg)

        staged_runtime = ResolvedRuntime(
            provider=provider_name,
            version="unknown",
            install_dir=staging_dir,
            executable_path=staged_executable,
            package_name=provider.package_name,
        )
        version_result = await execute_process(
            provider.version_command(staged_runtime),
            env=provider.build_environment(self.environ),
            timeout_seconds=30,
        )
        if version_result.exit_code != 0:
            shutil.rmtree(staging_dir, ignore_errors=True)
            msg = f"Failed to verify installed runtime version for '{provider_name}'."
            raise RuntimeVerificationError(msg)

        version = provider.parse_version(version_result.stdout, version_result.stderr)
        final_dir = provider_runtime_dir / _safe_version_directory_name(version)
        if final_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        else:
            staging_dir.rename(final_dir)

        runtime = ResolvedRuntime(
            provider=provider_name,
            version=version,
            install_dir=final_dir,
            executable_path=final_dir / "bin" / provider.executable_name,
            package_name=provider.package_name,
            installed_at=datetime.now(UTC),
        )

        previous_version = manifest.current_version if manifest is not None else None
        previous_runtime_path = (
            manifest.current_runtime_path if manifest is not None else None
        )
        if manifest is not None and manifest.current_version == version:
            previous_version = manifest.previous_version
            previous_runtime_path = manifest.previous_runtime_path

        updated_manifest = RuntimeManifest(
            provider=provider_name,
            provider_root=self.runtime_root / provider_name,
            current_version=version,
            current_runtime_path=final_dir,
            previous_version=previous_version,
            previous_runtime_path=previous_runtime_path,
            installed_at=datetime.now(UTC),
            last_checked_at=manifest.last_checked_at if manifest is not None else None,
            last_auth_ok_at=manifest.last_auth_ok_at if manifest is not None else None,
        )
        self.manifest_store.save(updated_manifest)
        self._cleanup_superseded_runtimes(provider_name, updated_manifest)
        return runtime, updated_manifest

    def _cleanup_superseded_runtimes(
        self,
        provider_name: str,
        manifest: RuntimeManifest,
    ) -> None:
        """Retain the current and previous known-good runtime directories only."""
        runtimes_dir = provider_runtimes_dir(self.runtime_root, provider_name)
        if not runtimes_dir.exists():
            return
        retained_paths = {
            path
            for path in (manifest.current_runtime_path, manifest.previous_runtime_path)
            if path is not None
        }
        for child in runtimes_dir.iterdir():
            if child in retained_paths:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    def _load_provider_environment(
        self,
        provider_name: str,
        workspace_id: str | None = None,
    ) -> dict[str, str]:
        """Load persisted provider-specific environment variables."""
        env_path = provider_environment_path(
            self.runtime_root,
            provider_name,
            workspace_id=workspace_id,
        )
        if not env_path.exists():
            return {}
        payload = json.loads(env_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            msg = (
                f"Persisted environment for provider '{provider_name}' must be a JSON "
                "object."
            )
            raise ValueError(msg)
        return {
            str(key): str(value).strip()
            for key, value in payload.items()
            if str(value).strip()
        }

    def _write_provider_environment(
        self,
        provider_name: str,
        payload: Mapping[str, str],
        workspace_id: str | None = None,
    ) -> None:
        """Persist provider-specific environment variables atomically."""
        provider_dir = provider_root(self.runtime_root, provider_name)
        provider_dir.mkdir(parents=True, exist_ok=True)
        env_path = provider_environment_path(
            self.runtime_root,
            provider_name,
            workspace_id=workspace_id,
        )
        env_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=provider_dir,
            delete=False,
        ) as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)

        temp_path.chmod(0o600)
        os.replace(temp_path, env_path)
        env_path.chmod(0o600)


def _safe_version_directory_name(version: str) -> str:
    """Return a filesystem-safe version directory name."""
    return version.replace("/", "_").replace(" ", "_")
