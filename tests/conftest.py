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
):
    os.environ.setdefault(key, value)

from orcheo.models import AesGcmCredentialCipher
from orcheo.vault import InMemoryCredentialVault
from orcheo.workspace import InMemoryWorkspaceRepository
from orcheo_backend.app import chatkit_runtime
from orcheo_backend.app import dependencies as backend_dependencies
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
