"""Configure test environment for Orcheo."""

from functools import lru_cache
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
os.environ["ORCHEO_WORKFLOW_DEFINITION_MODE"] = "unrestricted"

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


from orcheo.config import loader as config_loader


config_loader._load_settings.cache_clear()


@lru_cache(maxsize=None)
def _requires_backend_state(test_file: str) -> bool:
    """Return whether a test module touches backend singletons."""

    path = Path(test_file)
    relative = path.relative_to(ROOT)
    if relative.parts[:2] in {
        ("tests", "backend"),
        ("tests", "workspace"),
        ("tests", "identity"),
        ("tests", "integration"),
    }:
        return True

    contents = path.read_text()
    return any(
        token in contents
        for token in (
            "orcheo_backend",
            "create_app(",
            "backend_dependencies",
            "workspace_dependencies",
            "chatkit_runtime",
            "set_repository(",
            "set_history_store(",
            "set_vault(",
            "set_workspace_repository(",
            "resolve_workspace_context",
        )
    )


@pytest.fixture(autouse=True)
def _seed_in_memory_backend_state(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Keep default backend singletons on in-memory implementations during tests."""

    if not _requires_backend_state(str(request.node.path)):
        return

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
