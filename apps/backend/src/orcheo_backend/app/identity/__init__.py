"""Backend first-party identity service: config, service, deps, and router."""

from orcheo_backend.app.identity.config import (
    DEFAULT_FIRST_PARTY_ISSUER,
    IdentityConfig,
    resolve_passkey_relying_party,
)
from orcheo_backend.app.identity.dependencies import (
    IdentityServiceDep,
    PasskeyServiceDep,
    get_client_ip,
    get_identity_config,
    get_identity_repository,
    get_identity_service,
    get_passkey_service,
    reset_identity_state,
    set_identity_repository,
    set_identity_service,
    set_passkey_service,
)
from orcheo_backend.app.identity.passkey_router import router as passkey_router
from orcheo_backend.app.identity.passkeys import PasskeyService
from orcheo_backend.app.identity.router import router
from orcheo_backend.app.identity.service import (
    IdentityService,
    IssuedTokens,
    VerificationResult,
)


__all__ = [
    "DEFAULT_FIRST_PARTY_ISSUER",
    "IdentityConfig",
    "IdentityService",
    "IdentityServiceDep",
    "IssuedTokens",
    "PasskeyService",
    "PasskeyServiceDep",
    "VerificationResult",
    "get_client_ip",
    "get_identity_config",
    "get_identity_repository",
    "get_identity_service",
    "get_passkey_service",
    "passkey_router",
    "reset_identity_state",
    "resolve_passkey_relying_party",
    "router",
    "set_identity_repository",
    "set_identity_service",
    "set_passkey_service",
]
