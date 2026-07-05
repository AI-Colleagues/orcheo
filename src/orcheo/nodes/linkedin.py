"""LinkedIn node."""

import base64
import json
import logging
from collections.abc import Mapping
from typing import Any
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.data import HttpRequestNode
from orcheo.nodes.registry import NodeMetadata, registry
from orcheo.runtime.credentials import (
    get_active_credential_resolver,
)


logger = logging.getLogger(__name__)

_ORG_ACLS_URL = "https://api.linkedin.com/rest/organizationAcls?q=roleAssignee"
_POSTS_URL = "https://api.linkedin.com/rest/posts"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
_ALLOWED_VISIBILITY = frozenset({"PUBLIC", "CONNECTIONS", "LOGGED_IN"})


class _LinkedInAuthError(Exception):
    """Raised internally when a LinkedIn API call returns HTTP 401."""


@registry.register(
    NodeMetadata(
        name="LinkedInPostNode",
        description="Create a LinkedIn post using vault-backed access credentials.",
        category="linkedin",
    )
)
class LinkedInPostNode(TaskNode):
    """Create a LinkedIn post using vault-backed access credentials."""

    linkedin_access_token: str = Field(
        default="[[linkedin_access_token]]",
        description="LinkedIn access token stored in the Orcheo vault.",
    )
    linkedin_refresh_token: str = Field(
        default="[[linkedin_refresh_token]]",
        description="LinkedIn refresh token stored in the Orcheo vault.",
    )
    linkedin_id_token: str = Field(
        default="[[linkedin_id_token]]",
        description=(
            "Optional LinkedIn OIDC id token stored in the Orcheo vault. "
            "When present it is preferred over the userinfo endpoint for person posts."
        ),
    )
    linkedin_client_id: str = Field(
        default="[[linkedin_client_id]]",
        description=(
            "LinkedIn app client ID stored in the Orcheo vault. "
            "Required to refresh an expired access token."
        ),
    )
    linkedin_client_secret: str = Field(
        default="[[linkedin_client_secret]]",
        description=(
            "LinkedIn app client secret stored in the Orcheo vault. "
            "Required to refresh an expired access token."
        ),
    )
    timeout: float = Field(default=30.0, ge=0.0, description="HTTP timeout in seconds.")

    @staticmethod
    def read_configurable(config: RunnableConfig) -> dict[str, Any]:
        """Return configurable values with example defaults applied."""
        defaults: dict[str, Any] = {
            "commentary": "Hello from Orcheo.",
            "linkedin_version": "202604",
            "post_as": "person",
            "visibility": "PUBLIC",
        }
        if not isinstance(config, Mapping):
            return defaults
        configurable = config.get("configurable", {})
        if not isinstance(configurable, Mapping):
            return defaults
        return {**defaults, **dict(configurable)}

    @staticmethod
    def linkedin_headers(
        access_token: str, linkedin_version: str, *, json_content: bool = True
    ) -> dict[str, str]:
        """Return LinkedIn API headers for the current request."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Linkedin-Version": linkedin_version,
            "X-Restli-Protocol-Version": "2.0.0",
        }
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def create_post_payload(
        author_urn: str, commentary: str, visibility: str
    ) -> dict[str, Any]:
        """Return the LinkedIn post payload."""
        return {
            "author": author_urn,
            "commentary": commentary,
            "visibility": visibility,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }

    @staticmethod
    def base64url_decode(input_str: str) -> bytes:
        """Return decoded bytes for a base64url-encoded JWT segment."""
        padding = "=" * (-len(input_str) % 4)
        return base64.urlsafe_b64decode(input_str + padding)

    @classmethod
    def parse_jwt_payload(cls, jwt_token: str) -> dict[str, Any]:
        """Return the decoded JWT payload."""
        parts = jwt_token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid id_token format")
        payload_json = cls.base64url_decode(parts[1]).decode("utf-8")
        decoded = json.loads(payload_json)
        if not isinstance(decoded, dict):
            raise ValueError("Decoded id_token payload must be a JSON object")
        return decoded

    async def resolve_organization_urn(
        self,
        state: State,
        config: RunnableConfig,
        access_token: str,
        linkedin_version: str,
    ) -> str:
        """Return the single approved organization URN for org posting."""
        result = await HttpRequestNode(
            name="resolve_organization_request",
            method="GET",
            url=_ORG_ACLS_URL,
            headers=self.linkedin_headers(
                access_token, linkedin_version, json_content=False
            ),
            timeout=self.timeout,
        )(state, config)
        payload = result["node_results"]["resolve_organization_request"]
        if payload["status_code"] == 401:
            raise _LinkedInAuthError("organizationAcls returned 401 Unauthorized")
        if payload["status_code"] != 200:
            raise ValueError(
                "organizationAcls failed "
                f"({payload['status_code']}): {payload['content']}. "
                "Your LinkedIn app and token need the rw_organization_admin scope."
            )

        data = payload.get("json")
        if not isinstance(data, Mapping):
            raise ValueError("organizationAcls returned a non-JSON response")

        urns: list[str] = []
        for element in data.get("elements", []):
            if not isinstance(element, Mapping):
                continue
            org = element.get("organization")
            if isinstance(org, str) and element.get("state") == "APPROVED":
                urns.append(org)

        if not urns:
            raise ValueError(
                "No approved organization URNs found in organizationAcls. "
                "Either grant the authenticated member an admin role on the "
                "organization page or set configurable.organization_urn explicitly."
            )
        unique_urns = sorted(set(urns))
        if len(unique_urns) > 1:
            raise ValueError(
                "Multiple approved organization URNs found: "
                f"{', '.join(unique_urns)}. Set configurable.organization_urn to "
                "choose one."
            )
        return unique_urns[0]

    async def resolve_person_identity(
        self,
        state: State,
        config: RunnableConfig,
        access_token: str,
        id_token: str = "",
    ) -> tuple[str, str]:
        """Return the LinkedIn person id and author URN from the OIDC userinfo API."""
        configured_id_token = id_token.strip()
        if configured_id_token:
            payload = self.parse_jwt_payload(configured_id_token)
            sub = payload.get("sub")
            if isinstance(sub, str) and sub.strip():
                person_id = sub.strip()
                return person_id, f"urn:li:person:{person_id}"
            raise ValueError("Could not resolve member id from id_token")

        result = await HttpRequestNode(
            name="resolve_person_request",
            method="GET",
            url=_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self.timeout,
        )(state, config)
        payload = result["node_results"]["resolve_person_request"]
        if payload["status_code"] == 401:
            raise _LinkedInAuthError("userinfo returned 401 Unauthorized")
        if payload["status_code"] != 200:
            raise ValueError(
                "userinfo failed "
                f"({payload['status_code']}): {payload['content']}. "
                "To avoid calling userinfo, provide the linkedin_id_token vault "
                "credential. Otherwise your LinkedIn app needs the "
                "'Sign in with LinkedIn using OpenID Connect' product and the "
                "matching OIDC scopes."
            )

        data = payload.get("json")
        if not isinstance(data, Mapping):
            raise ValueError("userinfo returned a non-JSON response")

        sub = data.get("sub")
        if not isinstance(sub, str) or not sub.strip():
            raise ValueError("Could not resolve member id from OIDC userinfo")
        person_id = sub.strip()
        return person_id, f"urn:li:person:{person_id}"

    async def resolve_author_urn(
        self,
        state: State,
        config: RunnableConfig,
        configurable: Mapping[str, Any],
        access_token: str,
    ) -> tuple[str, str | None, str | None]:
        """Resolve author identity from token context."""
        post_as = str(configurable["post_as"]).strip().lower()
        if post_as == "organization":
            configured_organization_urn = str(
                configurable.get("organization_urn", "")
            ).strip()
            organization_urn = configured_organization_urn
            if not organization_urn:
                organization_urn = await self.resolve_organization_urn(
                    state,
                    config,
                    access_token,
                    str(configurable["linkedin_version"]).strip(),
                )
            return organization_urn, None, organization_urn
        if post_as == "person":
            person_id, author_urn = await self.resolve_person_identity(
                state,
                config,
                access_token,
                self.linkedin_id_token,
            )
            return author_urn, person_id, None
        raise ValueError(
            "configurable.post_as must be either 'person' or 'organization'"
        )

    async def refresh_access_token(
        self, state: State, config: RunnableConfig
    ) -> tuple[str, str | None]:
        """Exchange the refresh token for a new access token via the LinkedIn token endpoint."""  # noqa: E501
        client_id = self.linkedin_client_id.strip()
        client_secret = self.linkedin_client_secret.strip()
        if not client_id or not client_secret:
            raise ValueError(
                "linkedin_client_id and linkedin_client_secret vault credentials are "
                "required to refresh an expired access token. Add these credentials "
                "or renew the access token manually."
            )
        result = await HttpRequestNode(
            name="refresh_token_request",
            method="POST",
            url=_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.linkedin_refresh_token.strip(),
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=self.timeout,
        )(state, config)
        payload = result["node_results"]["refresh_token_request"]
        if payload["status_code"] != 200:
            raise ValueError(
                f"Token refresh failed ({payload['status_code']}): {payload['content']}"
            )
        data = payload.get("json")
        if not isinstance(data, Mapping):
            raise ValueError("Token refresh returned a non-JSON response")
        new_access_token = data.get("access_token")
        if not isinstance(new_access_token, str) or not new_access_token.strip():
            raise ValueError("Token refresh response missing access_token")
        new_refresh_token = data.get("refresh_token")
        if not isinstance(new_refresh_token, str) or not new_refresh_token.strip():
            new_refresh_token = None
        return new_access_token.strip(), new_refresh_token

    def _update_vault_tokens(
        self, new_access_token: str, new_refresh_token: str | None
    ) -> None:
        """Write refreshed tokens back to the vault when a resolver is active."""
        resolver = get_active_credential_resolver()
        if resolver is None:
            logger.warning(
                "No active credential resolver; refreshed LinkedIn tokens will not "
                "be persisted to the vault."
            )
            return

        try:
            updated = resolver.persist_refreshed_tokens(
                "linkedin_access_token",
                new_access_token=new_access_token,
                new_refresh_token=new_refresh_token,
                fallback_refresh_token=self.linkedin_refresh_token or None,
                actor="linkedin_node",
            )
            if not updated:
                logger.warning(
                    "Credential '%s' not found in vault by name; "
                    "the refreshed access token will not be persisted.",
                    "linkedin_access_token",
                )
        except Exception:
            logger.warning(
                "Failed to update '%s' in vault after token refresh.",
                "linkedin_access_token",
                exc_info=True,
            )

        if new_refresh_token:
            try:
                resolver.persist_refreshed_tokens(
                    "linkedin_refresh_token",
                    new_access_token=new_refresh_token,
                    actor="linkedin_node",
                )
            except Exception:
                logger.warning(
                    "Failed to update '%s' in vault after token refresh.",
                    "linkedin_refresh_token",
                    exc_info=True,
                )

    async def _attempt_post(
        self,
        state: State,
        config: RunnableConfig,
        access_token: str,
        configurable: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Resolve author and create the post; raises _LinkedInAuthError on HTTP 401."""
        linkedin_version = str(configurable["linkedin_version"]).strip()
        visibility = str(configurable["visibility"]).strip().upper()
        commentary = str(configurable["commentary"]).strip()
        if visibility not in _ALLOWED_VISIBILITY:
            allowed_values = ", ".join(sorted(_ALLOWED_VISIBILITY))
            raise ValueError(
                "configurable.visibility must be one of "
                f"{allowed_values}; received '{visibility}'."
            )

        author_urn, person_id, organization_urn = await self.resolve_author_urn(
            state, config, configurable, access_token
        )
        result = await HttpRequestNode(
            name="create_post_request",
            method="POST",
            url=_POSTS_URL,
            headers=self.linkedin_headers(access_token, linkedin_version),
            json_body=self.create_post_payload(author_urn, commentary, visibility),
            timeout=self.timeout,
        )(state, config)
        response = result["node_results"]["create_post_request"]

        if response["status_code"] == 401:
            raise _LinkedInAuthError("Post creation returned 401 Unauthorized")
        if response["status_code"] != 201:
            raise ValueError(
                f"Post creation failed ({response['status_code']}): "
                f"{response['content']}"
            )

        return {
            "author_urn": author_urn,
            "organization_urn": organization_urn,
            "person_id": person_id,
            "post_id": response["headers"].get("x-restli-id", ""),
            "post_as": str(configurable["post_as"]).strip().lower(),
            "refresh_token_configured": True,
            "status_code": response["status_code"],
        }

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Create the LinkedIn post, refreshing the access token on 401 if needed."""
        access_token = self.linkedin_access_token.strip()
        refresh_token = self.linkedin_refresh_token.strip()
        if not access_token:
            raise ValueError("Vault credential 'linkedin_access_token' is required")
        if not refresh_token:
            raise ValueError("Vault credential 'linkedin_refresh_token' is required")

        configurable = self.read_configurable(config)
        commentary = str(configurable["commentary"]).strip()
        if not commentary:
            raise ValueError("configurable.commentary is required")

        try:
            return await self._attempt_post(state, config, access_token, configurable)
        except _LinkedInAuthError:
            new_access_token, new_refresh_token = await self.refresh_access_token(
                state, config
            )
            self._update_vault_tokens(new_access_token, new_refresh_token)
            try:
                return await self._attempt_post(
                    state, config, new_access_token, configurable
                )
            except _LinkedInAuthError as err:
                raise ValueError(
                    "LinkedIn API returned 401 Unauthorized even after token refresh. "
                    "The credentials may have been revoked."
                ) from err


__all__ = ["LinkedInPostNode"]
