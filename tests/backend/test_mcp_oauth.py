"""End-to-end OAuth sign-in for MCP clients, reusing the Studio login."""

from __future__ import annotations
import base64
import hashlib
import secrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI
from orcheo.identity import InMemoryIdentityRepository
from orcheo.identity.models import User
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.authentication import dependencies as auth_dependencies
from orcheo_backend.app.identity import (
    IdentityConfig,
    IdentityService,
    reset_identity_state,
    set_identity_service,
)
from orcheo_backend.app.identity.tokens import mint_access_token
from orcheo_backend.app.mcp_server import mcp_lifespan
from tests.backend.api.mcp_support import MCP_HEADERS
from tests.backend.authentication_test_utils import create_test_client


SECRET = "oauth-test-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.orcheo.test"
AUDIENCE = "orcheo-api"
# Issuers must be https except on localhost (RFC 8414).
ORIGIN = "http://localhost"
REDIRECT_URI = "http://127.0.0.1:33418/callback"


class _NullSender:
    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:  # noqa: D102
        return None


class OAuthHarness:
    """Drives the OAuth flow against the in-process app."""

    def __init__(
        self, client: httpx.AsyncClient, repo: InMemoryIdentityRepository
    ) -> None:
        self.client = client
        self.repo = repo
        self.user = repo.create_user(
            User(email="dana@example.com", email_verified=True)
        )

    def studio_token(self) -> str:
        token, _ = mint_access_token(
            user=self.user,
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            ttl_seconds=300,
            now=datetime.now(tz=UTC),
        )
        return token

    async def register(self, **overrides: object) -> dict:
        body = {
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "none",
            "client_name": "Claude Code",
            **overrides,
        }
        response = await self.client.post("/api/oauth/register", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    async def start(
        self, client_id: str, verifier: str, **params: str
    ) -> httpx.Response:
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        return await self.client.get(
            "/api/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "state-123",
                **params,
            },
        )

    async def consent(self, location: str, *, approve: bool = True) -> str:
        request_id = parse_qs(urlparse(location).query)["request"][0]
        headers = {"Authorization": f"Bearer {self.studio_token()}"}
        view = await self.client.get(
            f"/api/oauth/requests/{request_id}", headers=headers
        )
        assert view.status_code == 200, view.text
        assert view.json()["client_name"] == "Claude Code"
        assert view.json()["redirect_host"] == "127.0.0.1:33418"
        decision = await self.client.post(
            f"/api/oauth/requests/{request_id}/decision",
            json={"approve": approve},
            headers=headers,
        )
        assert decision.status_code == 200, decision.text
        return decision.json()["redirect_url"]

    async def authorize(self, client_id: str, verifier: str, **params: str) -> str:
        started = await self.start(
            client_id, verifier, resource=f"{ORIGIN}/api/mcp", **params
        )
        assert started.status_code == 302
        redirect = await self.consent(started.headers["location"])
        query = parse_qs(urlparse(redirect).query)
        assert query["state"] == ["state-123"]
        return query["code"][0]

    async def token(self, **form: str) -> httpx.Response:
        return await self.client.post("/api/oauth/token", data=form)

    async def grant(self, **params: str) -> dict:
        """Run the whole flow for a new client and return its tokens."""
        client = await self.register()
        verifier = _verifier()
        code = await self.authorize(client["client_id"], verifier, **params)
        issued = await self.token(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT_URI,
            client_id=client["client_id"],
            code_verifier=verifier,
        )
        assert issued.status_code == 200, issued.text
        return issued.json()


@pytest.fixture()
def oauth_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FastAPI, InMemoryIdentityRepository]]:
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    monkeypatch.delenv("ORCHEO_PUBLIC_URL", raising=False)
    reset_authentication_state()
    reset_identity_state()
    repo = InMemoryIdentityRepository()
    service = IdentityService(
        repo,
        email_sender=_NullSender(),
        config=IdentityConfig(jwt_secret=SECRET, issuer=ISSUER, audience=AUDIENCE),
    )
    set_identity_service(service)
    test_client = create_test_client()
    try:
        yield test_client.app, repo
    finally:
        set_identity_service(None)
        reset_identity_state()
        reset_authentication_state()


@asynccontextmanager
async def oauth_session(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> AsyncIterator[OAuthHarness]:
    """Run the MCP server and yield a harness bound to the app."""
    app, repo = oauth_env
    async with mcp_lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            yield OAuthHarness(client, repo)


def _verifier() -> str:
    return secrets.token_urlsafe(48)


async def _mcp(
    client: httpx.AsyncClient, method: str, params: dict, token: str | None
) -> httpx.Response:
    headers = dict(MCP_HEADERS)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers=headers,
    )


@pytest.mark.asyncio()
async def test_discovery_starts_from_the_mcp_challenge(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        challenge = await _mcp(oauth.client, "tools/list", {}, token=None)
        assert challenge.status_code == 401
        header = challenge.headers["www-authenticate"]
        metadata_url = f"{ORIGIN}/.well-known/oauth-protected-resource/api/mcp"
        assert header == f'Bearer resource_metadata="{metadata_url}"'

        resource = (await oauth.client.get(metadata_url)).json()
        assert resource["resource"] == f"{ORIGIN}/api/mcp"
        issuer = resource["authorization_servers"][0]
        assert issuer.rstrip("/") == f"{ORIGIN}/api/oauth"

        # RFC 8414 path-aware metadata, plus FastMCP's OpenID discovery alias.
        server = (
            await oauth.client.get("/.well-known/oauth-authorization-server/api/oauth")
        ).json()
        alias = (
            await oauth.client.get("/.well-known/openid-configuration/api/oauth")
        ).json()
        assert alias == server
        assert server["issuer"] == issuer
        assert server["authorization_endpoint"] == f"{ORIGIN}/api/oauth/authorize"
        assert server["token_endpoint"] == f"{ORIGIN}/api/oauth/token"
        assert server["registration_endpoint"] == f"{ORIGIN}/api/oauth/register"
        assert server["revocation_endpoint"] == f"{ORIGIN}/api/oauth/revoke"
        assert server["code_challenge_methods_supported"] == ["S256"]
        assert server["scopes_supported"][0] == "workflows:read"


@pytest.mark.asyncio()
async def test_full_flow_grants_a_token_the_mcp_server_accepts(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with oauth_session(oauth_env) as oauth:
        client = await oauth.register()
        assert "client_secret" not in client
        assert client["scope"].split() == [
            "workflows:read",
            "workflows:write",
            "workflows:execute",
            "vault:read",
            "vault:write",
            "apps:read",
            "apps:write",
            "apps:publish",
            "workspaces:read",
            "workspaces:write",
            "admin:tokens:read",
            "admin:tokens:write",
        ]
        verifier = _verifier()
        code = await oauth.authorize(client["client_id"], verifier)

        issued = await oauth.token(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT_URI,
            client_id=client["client_id"],
            code_verifier=verifier,
        )
        assert issued.status_code == 200, issued.text
        tokens = issued.json()
        assert tokens["token_type"] == "Bearer"
        assert tokens["refresh_token"]

        replay = await oauth.token(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT_URI,
            client_id=client["client_id"],
            code_verifier=verifier,
        )
        assert replay.status_code == 401
        assert replay.json()["error"] == "invalid_grant"

        # One token validation per MCP request, however many API calls the tool
        # makes in-process.
        validations: list[object] = []
        original = auth_dependencies.auth_telemetry.record_auth_success
        monkeypatch.setattr(
            auth_dependencies.auth_telemetry,
            "record_auth_success",
            lambda context, **kwargs: validations.append(context)
            or original(context, **kwargs),
        )
        listed = await _mcp(
            oauth.client,
            "tools/call",
            {"name": "list_workflows", "arguments": {}},
            token=tokens["access_token"],
        )
        assert listed.status_code == 200
        assert not listed.json()["result"].get("isError"), listed.text
        assert len(validations) == 1
        assert validations[0].subject == str(oauth.user.id)

        # OAuth-granted tokens can manage credentials but never read secrets.
        secret = await oauth.client.get(
            f"/api/credentials/{uuid4()}/secret",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert secret.status_code == 403


@pytest.mark.asyncio()
async def test_refresh_rotation_and_revocation(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        client = await oauth.register()
        verifier = _verifier()
        code = await oauth.authorize(client["client_id"], verifier)
        tokens = (
            await oauth.token(
                grant_type="authorization_code",
                code=code,
                redirect_uri=REDIRECT_URI,
                client_id=client["client_id"],
                code_verifier=verifier,
            )
        ).json()

        studio_refresh = await oauth.client.post(
            "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert studio_refresh.status_code == 401

        narrowed = await oauth.token(
            grant_type="refresh_token",
            refresh_token=tokens["refresh_token"],
            client_id=client["client_id"],
            scope="workflows:read",
        )
        assert narrowed.status_code == 200, narrowed.text
        rotated = narrowed.json()
        assert rotated["scope"] == "workflows:read"

        stale = await oauth.token(
            grant_type="refresh_token",
            refresh_token=tokens["refresh_token"],
            client_id=client["client_id"],
        )
        assert stale.status_code == 401

        revoked = await oauth.client.post(
            "/api/oauth/revoke",
            data={"token": rotated["refresh_token"], "client_id": client["client_id"]},
        )
        assert revoked.status_code == 200
        after_revoke = await oauth.token(
            grant_type="refresh_token",
            refresh_token=rotated["refresh_token"],
            client_id=client["client_id"],
        )
        assert after_revoke.status_code == 401


@pytest.mark.asyncio()
async def test_confidential_client_uses_derived_secret(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        client = await oauth.register(token_endpoint_auth_method="client_secret_post")
        assert client["client_secret"]
        verifier = _verifier()
        code = await oauth.authorize(client["client_id"], verifier)

        wrong = await oauth.token(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT_URI,
            client_id=client["client_id"],
            client_secret="not-the-secret",
            code_verifier=verifier,
        )
        right = await oauth.token(
            grant_type="authorization_code",
            code=code,
            redirect_uri=REDIRECT_URI,
            client_id=client["client_id"],
            client_secret=client["client_secret"],
            code_verifier=verifier,
        )

        assert wrong.status_code == 401
        assert right.status_code == 200, right.text


@pytest.mark.asyncio()
async def test_denied_consent_redirects_with_error(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        client = await oauth.register()
        started = await oauth.start(client["client_id"], _verifier())
        redirect = await oauth.consent(started.headers["location"], approve=False)
        query = parse_qs(urlparse(redirect).query)
        request_id = parse_qs(urlparse(started.headers["location"]).query)["request"][0]
        again = await oauth.client.post(
            f"/api/oauth/requests/{request_id}/decision",
            json={"approve": True},
            headers={"Authorization": f"Bearer {oauth.studio_token()}"},
        )
        missing = await oauth.client.get(
            "/api/oauth/requests/unknown",
            headers={"Authorization": f"Bearer {oauth.studio_token()}"},
        )

        assert query["error"] == ["access_denied"]
        assert query["state"] == ["state-123"]
        assert again.status_code == 404
        assert missing.status_code == 404


@pytest.mark.asyncio()
async def test_authorize_rejects_foreign_resources(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        client = await oauth.register()
        response = await oauth.start(
            client["client_id"], _verifier(), resource="https://elsewhere.test/api"
        )

        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["error"] == ["invalid_request"]


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"redirect_uris": ["http://evil.test/cb"]}, "must use https"),
        ({"redirect_uris": ["javascript://x"]}, "not allowed"),
        ({"redirect_uris": ["https://ok.test/cb#frag"]}, "fragment"),
        ({"token_endpoint_auth_method": "private_key_jwt"}, "not supported"),
        ({"grant_types": ["refresh_token"]}, "grant_types"),
        ({"response_types": ["token"]}, "response_types"),
        ({"scope": "workflows:read admin"}, "Unsupported scopes: admin"),
        ({"redirect_uris": []}, "redirect_uris"),
    ],
    ids=[
        "http-non-loopback",
        "javascript",
        "fragment",
        "auth-method",
        "grant-types",
        "response-types",
        "scope",
        "no-redirects",
    ],
)
async def test_registration_validates_metadata(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository], overrides: dict, message: str
) -> None:
    async with oauth_session(oauth_env) as oauth:
        body = {"redirect_uris": [REDIRECT_URI], **overrides}
        response = await oauth.client.post("/api/oauth/register", json=body)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client_metadata"
        assert message in response.json()["error_description"]


@pytest.mark.asyncio()
async def test_registration_accepts_native_app_schemes(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        client = await oauth.register(
            redirect_uris=[
                "cursor://anysphere.cursor-mcp/oauth/callback",
                "https://claude.ai/api/mcp/auth_callback",
            ],
            scope="workflows:read",
        )

        assert client["scope"] == "workflows:read"


@pytest.mark.asyncio()
async def test_registration_rejects_malformed_json(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        response = await oauth.client.post(
            "/api/oauth/register",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400


@pytest.mark.asyncio()
async def test_consent_requires_a_user_account(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        client = await oauth.register()
        started = await oauth.start(client["client_id"], _verifier())
        request_id = parse_qs(urlparse(started.headers["location"]).query)["request"][0]
        anonymous = await oauth.client.get(f"/api/oauth/requests/{request_id}")

        assert anonymous.status_code == 401


@pytest.mark.asyncio()
async def test_oauth_tokens_only_reach_the_mcp_endpoint(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        tokens = await oauth.grant(scope="workflows:read")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        # The grant can't mint an unrestricted service token...
        minted = await oauth.client.post(
            "/api/admin/service-tokens", json={"scopes": []}, headers=headers
        )
        # ...call the REST API directly...
        listed = await oauth.client.get("/api/workflows", headers=headers)
        # ...or approve its own request for wider scopes.
        client = await oauth.register()
        started = await oauth.start(client["client_id"], _verifier())
        request_id = parse_qs(urlparse(started.headers["location"]).query)["request"][0]
        self_approved = await oauth.client.post(
            f"/api/oauth/requests/{request_id}/decision",
            json={"approve": True},
            headers=headers,
        )
        via_mcp = await _mcp(
            oauth.client,
            "tools/call",
            {"name": "list_workflows", "arguments": {}},
            token=tokens["access_token"],
        )

        for response in (minted, listed, self_approved):
            assert response.status_code == 403
            assert response.json()["detail"]["code"] == "auth.oauth_client_token"
        assert not via_mcp.json()["result"].get("isError"), via_mcp.text


@pytest.mark.asyncio()
async def test_mcp_tools_follow_the_approved_scopes(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        read_only = (await oauth.grant(scope="workflows:read"))["access_token"]
        full = (await oauth.grant())["access_token"]

        listed = await _mcp(oauth.client, "tools/list", {}, token=read_only)
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        everything = await _mcp(oauth.client, "tools/list", {}, token=full)
        all_names = {tool["name"] for tool in everything.json()["result"]["tools"]}
        upload = await _mcp(
            oauth.client,
            "tools/call",
            {
                "name": "upload_workflow",
                "arguments": {"script": "x", "name": "Blocked"},
            },
            token=read_only,
        )
        unknown = await _mcp(
            oauth.client,
            "tools/call",
            {"name": "no_such_tool", "arguments": {}},
            token=read_only,
        )

        assert "no_such_tool" in unknown.text
        assert {"list_workflows", "get_server_info"} <= names
        assert names.isdisjoint({"upload_workflow", "run_workflow", "list_credentials"})
        assert "upload_workflow" in all_names
        result = upload.json()["result"]
        assert result["isError"]
        assert "workflows:write" in result["content"][0]["text"]


@pytest.mark.asyncio()
async def test_consent_names_custom_redirect_schemes(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    async with oauth_session(oauth_env) as oauth:
        native = "evilapp://claude.ai/callback"
        client = await oauth.register(redirect_uris=[native])
        started = await oauth.start(
            client["client_id"], _verifier(), redirect_uri=native
        )
        request_id = parse_qs(urlparse(started.headers["location"]).query)["request"][0]
        view = await oauth.client.get(
            f"/api/oauth/requests/{request_id}",
            headers={"Authorization": f"Bearer {oauth.studio_token()}"},
        )

        assert view.json()["redirect_host"] == "evilapp://claude.ai"


@pytest.mark.asyncio()
async def test_new_mcp_tools_require_their_approved_scopes(
    oauth_env: tuple[FastAPI, InMemoryIdentityRepository],
) -> None:
    """Workflow grants cannot mutate apps or use credential validation tools."""
    async with oauth_session(oauth_env) as oauth:
        read = (await oauth.grant(scope="workflows:read"))["access_token"]
        app_read = (await oauth.grant(scope="apps:read"))["access_token"]
        listed = await _mcp(oauth.client, "tools/list", {}, token=read)
        names = {tool["name"] for tool in listed.json()["result"]["tools"]}
        assert {
            "get_execution_history",
            "diff_workflow_versions",
            "get_webhook_config",
            "list_agentensor_checkpoints",
        } <= names
        assert names.isdisjoint(
            {
                "configure_webhook",
                "validate_workflow_credentials",
                "execute_node",
                "evaluate_workflow",
                "list_hosted_apps",
                "publish_hosted_app",
            }
        )
        listed_apps = await _mcp(oauth.client, "tools/list", {}, token=app_read)
        app_names = {tool["name"] for tool in listed_apps.json()["result"]["tools"]}
        assert {"list_hosted_apps", "list_hosted_app_deployments"} <= app_names
        assert app_names.isdisjoint(
            {"save_hosted_app_binding", "publish_hosted_app", "list_workflows"}
        )
        rejected = await _mcp(
            oauth.client,
            "tools/call",
            {
                "name": "publish_hosted_app",
                "arguments": {
                    "app_id": str(uuid4()),
                    "deployment_id": str(uuid4()),
                    "review": {"acknowledged_permission_revision": 1},
                },
            },
            token=app_read,
        )
        assert rejected.json()["result"]["isError"]
        assert "apps:publish" in rejected.text


@pytest.mark.asyncio()
async def test_administration_tools_require_explicit_oauth_scopes(oauth_env) -> None:
    async with oauth_session(oauth_env) as oauth:
        workflow = (await oauth.grant(scope="workflows:read"))["access_token"]
        admin_read = (await oauth.grant(scope="workspaces:read admin:tokens:read"))[
            "access_token"
        ]
        for token, expected, forbidden in (
            (
                workflow,
                {"list_workflows"},
                {
                    "list_workspaces",
                    "list_service_tokens",
                    "open_service_token_form",
                    "create_workspace",
                },
            ),
            (
                admin_read,
                {
                    "list_workspaces",
                    "get_workspace",
                    "list_workspace_members",
                    "list_service_tokens",
                    "get_service_token",
                },
                {
                    "create_workspace",
                    "delete_workspace",
                    "open_service_token_form",
                    "create_service_token",
                    "revoke_service_token",
                },
            ),
        ):
            listing = await _mcp(oauth.client, "tools/list", {}, token=token)
            names = {t["name"] for t in listing.json()["result"]["tools"]}
            assert expected <= names
            assert forbidden.isdisjoint(names)
            denied = await _mcp(
                oauth.client,
                "tools/call",
                {
                    "name": "create_workspace",
                    "arguments": {"slug": "denied", "name": "Denied"},
                },
                token=token,
            )
            assert denied.json()["result"]["isError"]
            assert "workspaces:write" in denied.text
        allowed = (await oauth.grant(scope="workspaces:read workspaces:write"))[
            "access_token"
        ]
        created = await _mcp(
            oauth.client,
            "tools/call",
            {
                "name": "create_workspace",
                "arguments": {"slug": "oauth-owned", "name": "OAuth Owned"},
            },
            token=allowed,
        )
        assert not created.json()["result"].get("isError"), created.text
        assert created.json()["result"]["structuredContent"]["slug"] == "oauth-owned"


@pytest.mark.asyncio()
async def test_service_token_form_binds_oauth_client_and_limits_minted_scopes(
    oauth_env,
    monkeypatch,
) -> None:
    import json
    from importlib import import_module
    from orcheo_backend.app.mcp_server.form_tokens import FORM_TOKEN_META_KEY
    from orcheo_backend.app.mcp_server.service_token_tools import (
        SERVICE_TOKEN_SECRET_META_KEY,
    )
    from orcheo_backend.app.authentication import ServiceTokenManager
    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    manager = ServiceTokenManager(InMemoryServiceTokenRepository())
    monkeypatch.setattr(
        import_module("orcheo_backend.app.service_token_endpoints"),
        "get_service_token_manager",
        lambda: manager,
    )

    async with oauth_session(oauth_env) as oauth:
        scope = "admin:tokens:read admin:tokens:write workflows:read"
        access = (await oauth.grant(scope=scope))["access_token"]
        other_client = (await oauth.grant(scope=scope))["access_token"]
        read_only = (await oauth.grant(scope="admin:tokens:read"))["access_token"]

        async def call(name, arguments, token=access):
            response = await _mcp(
                oauth.client,
                "tools/call",
                {"name": name, "arguments": arguments},
                token=token,
            )
            assert response.status_code == 200
            return response.json()["result"]

        opened = await call("open_service_token_form", {"scopes": ["workflows:read"]})
        assert not opened.get("isError"), opened
        form_token = opened["_meta"][FORM_TOKEN_META_KEY]
        arguments = {"form_token": form_token, "scopes": ["workflows:read"]}
        wrong_client = await call("create_service_token", arguments, other_client)
        assert wrong_client["isError"] and "different application" in json.dumps(
            wrong_client
        )
        denied = await call("create_service_token", arguments, read_only)
        assert denied["isError"] and "admin:tokens:write" in json.dumps(denied)
        escalation = await call(
            "create_service_token", {**arguments, "scopes": ["vault:write"]}
        )
        assert escalation["isError"] and "403" in json.dumps(escalation)
        created = await call("create_service_token", arguments)
        assert not created.get("isError"), created
        secret = created["_meta"][SERVICE_TOKEN_SECRET_META_KEY]
        assert secret not in json.dumps(
            {k: v for k, v in created.items() if k != "_meta"}
        )
        token_id = created["structuredContent"]["identifier"]
        read = await call("get_service_token", {"token_id": token_id}, read_only)
        assert not read.get("isError")
        assert secret not in json.dumps(read)
        assert "secret_preview" not in read["structuredContent"]
        # Even a grant for token management cannot bypass the private App over REST.
        direct = await oauth.client.post(
            "/api/admin/service-tokens",
            json={"scopes": []},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert direct.status_code == 403
