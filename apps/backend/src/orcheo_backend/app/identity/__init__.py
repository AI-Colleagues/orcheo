"""Backend first-party identity service: config, service, deps, and router."""

from orcheo_backend.app.identity.config import (
    DEFAULT_FIRST_PARTY_ISSUER,
    IdentityConfig,
)
from orcheo_backend.app.identity.dependencies import (
    IdentityServiceDep,
    get_client_ip,
    get_identity_config,
    get_identity_repository,
    get_identity_service,
    reset_identity_state,
    set_identity_repository,
    set_identity_service,
)
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
    "VerificationResult",
    "get_client_ip",
    "get_identity_config",
    "get_identity_repository",
    "get_identity_service",
    "reset_identity_state",
    "router",
    "set_identity_repository",
    "set_identity_service",
]
