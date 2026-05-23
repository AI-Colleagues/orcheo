"""Configure test environment for Orcheo."""

import os
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
import pytest

for key, value in (
    ("OPENAI_API_KEY", "sk-test-orcheo"),
    ("ORCHEO_POSTGRES_DSN", "postgresql://test:test@localhost/test"),
    ("ORCHEO_VAULT_ENCRYPTION_KEY", "test-vault-encryption-key"),
    # The backend refuses to start without a broker secret in non-test runs.
    # Tests supply a deterministic value so the FastAPI app builds at import.
    ("ORCHEO_CREDENTIAL_BROKER_SECRET", "test-broker-secret"),
    ("ORCHEO_SANDBOX_CONTROL_TOKEN", "test-sandbox-control-token"),
    ("ORCHEO_SANDBOX_REVOCATION_STORE", "memory"),
    # The sandbox bootstrap requires a runtime URL; tests inject their own
    # primitives and never actually hit this URL.
    ("ORCHEO_SANDBOX_RUNTIME_URL", "http://sandbox-runtime.test:9090"),
):
    os.environ.setdefault(key, value)

from orcheo.models import AesGcmCredentialCipher
from orcheo.graph.ingestion import ingest_langgraph_script
from orcheo.sandbox.broker import CredentialBroker
from orcheo.sandbox.launcher import SandboxedProcessLauncher
from orcheo.sandbox.workflow import (
    WorkflowRunResult,
    WorkflowRunSpec,
    WorkflowSandboxDispatcher,
)
from orcheo.vault import InMemoryCredentialVault
from orcheo.workspace import InMemoryWorkspaceRepository
from orcheo_backend.app import chatkit_runtime
from orcheo_backend.app import dependencies as backend_dependencies
from orcheo_backend.app import sandbox as backend_sandbox
from orcheo_backend.app.workspace import dependencies as workspace_dependencies


warnings.filterwarnings(
    "ignore",
    module="chatkit.widgets",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    module="chatkit.actions",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    module="typing_extensions",
    category=DeprecationWarning,
)

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import chatkit.actions  # noqa: F401
    import chatkit.types  # noqa: F401
    import chatkit.widgets  # noqa: F401


pytest_plugins = [
    "tests.backend.chatkit_router_helpers_support",
]


ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "apps" / "backend" / "src"
SDK_SRC = ROOT / "packages" / "sdk" / "src"
for path in (BACKEND_SRC, SDK_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(autouse=True)
def _ensure_openai_api_key(monkeypatch):
    """Provide a deterministic OpenAI API key for tests when missing."""

    if not os.environ.get("OPENAI_API_KEY"):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-orcheo")


@pytest.fixture(autouse=True)
def _ensure_postgres_dsn(monkeypatch):
    """Provide a deterministic PostgreSQL DSN for settings-based tests."""

    if not os.environ.get("ORCHEO_POSTGRES_DSN"):
        monkeypatch.setenv(
            "ORCHEO_POSTGRES_DSN", "postgresql://test:test@localhost/test"
        )


@pytest.fixture(autouse=True)
def _ensure_vault_encryption_key(monkeypatch):
    """Provide a deterministic vault encryption key for settings-based tests."""

    if not os.environ.get("ORCHEO_VAULT_ENCRYPTION_KEY"):
        monkeypatch.setenv("ORCHEO_VAULT_ENCRYPTION_KEY", "test-vault-encryption-key")


class _StubSandboxDispatcher:
    """Test dispatcher that never sandboxes and never touches the runtime.

    Real workflow execution in unit tests runs in-process: the goal is to
    verify the worker / executor wiring, not the gVisor stack. The shared
    bootstrap normally insists on a configured broker before exposing the
    dispatcher, so we install this stub so the production callers
    (``dispatcher.should_sandbox(...)`` / ``dispatcher.dispatch(...)``)
    keep working without standing up a real sandbox runtime.
    """

    def __init__(self) -> None:
        self.dispatched: list[WorkflowRunSpec] = []
        self.next_result: WorkflowRunResult | None = None

    def should_sandbox(self, spec: WorkflowRunSpec) -> bool:  # noqa: ARG002
        return False

    async def dispatch(self, spec: WorkflowRunSpec) -> WorkflowRunResult:
        self.dispatched.append(spec)
        return self.next_result or WorkflowRunResult(
            run_id=spec.run_id,
            status="succeeded",
            outputs={},
        )


class _StubSandboxLauncher:
    """Test launcher returned by ``get_sandbox_launcher`` during unit tests."""

    async def run(self, **_: object) -> object:  # pragma: no cover - defensive
        msg = "Stub sandbox launcher cannot execute commands in unit tests"
        raise RuntimeError(msg)


class _StubSandboxBootstrap:
    """Replacement bootstrap that yields the stub dispatcher / launcher."""

    def __init__(self) -> None:
        self._dispatcher = _StubSandboxDispatcher()
        self._launcher = _StubSandboxLauncher()
        self._broker: CredentialBroker | None = CredentialBroker(
            secret="test-broker-secret",
            resolver=lambda **_: "",
        )

    def configure(self, broker: CredentialBroker) -> None:
        self._broker = broker

    def launcher(self) -> SandboxedProcessLauncher:  # type: ignore[override]
        return self._launcher  # type: ignore[return-value]

    def dispatcher(self) -> WorkflowSandboxDispatcher:  # type: ignore[override]
        return self._dispatcher  # type: ignore[return-value]

    async def ingest_script(
        self,
        *,
        workspace_id: str,
        source: str,
        entrypoint: str | None,
        max_script_bytes: int | None,
        execution_timeout_seconds: float | None,
    ) -> dict[str, object]:
        """Emulate sandbox ingestion for unit tests without a runtime service."""
        del workspace_id
        return ingest_langgraph_script(
            source,
            entrypoint=entrypoint,
            max_script_bytes=max_script_bytes,
            execution_timeout_seconds=execution_timeout_seconds,
        )


@pytest.fixture(autouse=True)
def _install_stub_sandbox_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap the real sandbox bootstrap for a stub during every unit test."""
    monkeypatch.setattr(backend_sandbox, "_bootstrap", _StubSandboxBootstrap())


@pytest.fixture(autouse=True)
def _seed_in_memory_backend_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default backend singletons on in-memory implementations during tests."""

    monkeypatch.setitem(
        workspace_dependencies._workspace_repository_ref,
        "repository",
        InMemoryWorkspaceRepository(),
    )
    monkeypatch.setitem(
        workspace_dependencies._workspace_service_ref,
        "service",
        None,
    )
    monkeypatch.setitem(
        backend_dependencies._vault_ref,
        "vault",
        InMemoryCredentialVault(cipher=AesGcmCredentialCipher(key="test-key")),
    )
    monkeypatch.setitem(
        backend_dependencies._credential_service_ref,
        "service",
        None,
    )
    monkeypatch.setitem(
        backend_dependencies._listener_runtime_store_ref,
        "store",
        backend_dependencies.ListenerRuntimeStore(),
    )
    monkeypatch.setitem(
        chatkit_runtime._chatkit_server_ref,
        "server",
        SimpleNamespace(store=None),
    )
