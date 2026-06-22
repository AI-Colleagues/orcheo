"""First-party token continuity for the invitation accept flow (Task 2.4).

Proves a token minted by the first-party identity service carries the
``email`` / ``email_verified`` claims the invitation accept path relies on, so
``_verified_email`` resolves a verified address on the fast path with no Auth0
``/userinfo`` fallback.
"""

from __future__ import annotations
from datetime import UTC, datetime
import jwt
from orcheo.identity.models import User
from orcheo_backend.app.authentication.jwt_helpers import claims_to_context
from orcheo_backend.app.identity.tokens import mint_access_token
from orcheo_backend.app.routers.workspaces import _verified_email

SECRET = "continuity-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.orcheo.test"


def test_first_party_token_yields_verified_email() -> None:
    user = User(email="invitee@example.com", email_verified=True, name="Invitee")
    token, _ = mint_access_token(
        user=user,
        secret=SECRET,
        issuer=ISSUER,
        audience=None,
        ttl_seconds=300,
        now=datetime.now(tz=UTC),
    )
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    context = claims_to_context(claims)

    email, verified = _verified_email(context)
    assert email == "invitee@example.com"
    assert verified is True


def test_unverified_first_party_token_is_not_trusted() -> None:
    user = User(email="pending@example.com", email_verified=False)
    token, _ = mint_access_token(
        user=user,
        secret=SECRET,
        issuer=ISSUER,
        audience=None,
        ttl_seconds=300,
        now=datetime.now(tz=UTC),
    )
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    context = claims_to_context(claims)

    # No access_token passed -> no /userinfo fallback; stays unverified.
    email, verified = _verified_email(context)
    assert email == "pending@example.com"
    assert verified is False
