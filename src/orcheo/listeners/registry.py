"""Registry for listener platforms, compiler hooks, and runtime adapters."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid5
from pydantic import BaseModel
from orcheo.listeners.models import ListenerPlatform, ListenerSubscription


_LISTENER_NAMESPACE = UUID("cb2512f5-f1d4-4883-bc6f-6d5c7a560fce")


class ListenerAdapterFactory(Protocol):
    """Factory contract for runtime listener adapters."""

    def __call__(
        self,
        *,
        repository: Any,
        subscription: ListenerSubscription,
        runtime_id: str,
    ) -> Any:
        """Build one listener adapter instance."""


class ListenerCompilerHook(Protocol):
    """Compiler hook for one listener platform."""

    def __call__(
        self,
        *,
        workflow_id: UUID,
        workflow_version_id: UUID,
        item: dict[str, Any],
        platform_id: str,
    ) -> ListenerSubscription | None:
        """Compile one graph listener item into a normalized subscription."""


class ListenerMetadata(BaseModel):
    """User-facing metadata for a listener platform."""

    id: str
    display_name: str
    component_kind: str = "listener"
    connection_mode: str = "long_connection"
    description: str = ""


@dataclass(slots=True)
class ListenerRegistration:
    """Listener platform registration record."""

    metadata: ListenerMetadata
    compiler: ListenerCompilerHook
    adapter_factory: ListenerAdapterFactory
    aliases: set[str] = field(default_factory=set)


class ListenerRegistry:
    """Registry for built-in and plugin-provided listener platforms."""

    def __init__(self) -> None:
        """Initialize an empty listener registry."""
        self._registrations: dict[str, ListenerRegistration] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        metadata: ListenerMetadata,
        *,
        compiler: ListenerCompilerHook,
        adapter_factory: ListenerAdapterFactory,
        aliases: tuple[str, ...] = (),
    ) -> None:
        """Register a listener platform."""
        if metadata.id in self._registrations or metadata.id in self._aliases:
            msg = f"Listener platform '{metadata.id}' is already registered."
            raise ValueError(msg)
        for alias in aliases:
            if alias in self._registrations or alias in self._aliases:
                msg = f"Listener platform alias '{alias}' is already registered."
                raise ValueError(msg)
        self._registrations[metadata.id] = ListenerRegistration(
            metadata=metadata,
            compiler=compiler,
            adapter_factory=adapter_factory,
            aliases=set(aliases),
        )
        for alias in aliases:
            self._aliases[alias] = metadata.id

    def resolve(self, platform_id: str) -> ListenerRegistration | None:
        """Return the registration for ``platform_id`` or one of its aliases."""
        canonical = self._aliases.get(platform_id, platform_id)
        return self._registrations.get(canonical)

    def list_metadata(self) -> list[ListenerMetadata]:
        """Return registered listener metadata sorted by id."""
        return sorted(
            (registration.metadata for registration in self._registrations.values()),
            key=lambda item: item.id.lower(),
        )

    def compile_subscription(
        self,
        *,
        workflow_id: UUID,
        workflow_version_id: UUID,
        item: dict[str, Any],
        platform_id: str,
    ) -> ListenerSubscription | None:
        """Compile one graph listener entry into a normalized subscription."""
        registration = self.resolve(platform_id)
        if registration is None:
            return None
        return registration.compiler(
            workflow_id=workflow_id,
            workflow_version_id=workflow_version_id,
            item=item,
            platform_id=registration.metadata.id,
        )

    def build_adapter(
        self,
        platform_id: str,
        *,
        repository: Any,
        subscription: ListenerSubscription,
        runtime_id: str,
    ) -> Any:
        """Instantiate an adapter for ``platform_id``."""
        registration = self.resolve(platform_id)
        if registration is None:
            msg = f"Unsupported listener platform: {platform_id!r}"
            raise ValueError(msg)
        return registration.adapter_factory(
            repository=repository,
            subscription=subscription,
            runtime_id=runtime_id,
        )

    def unregister(self, platform_id: str) -> None:
        """Remove one listener platform and any aliases pointing at it."""
        registration = self.resolve(platform_id)
        if registration is None:
            return
        canonical = registration.metadata.id
        self._registrations.pop(canonical, None)
        aliases = [
            alias
            for alias, resolved in self._aliases.items()
            if resolved == canonical or alias == platform_id
        ]
        for alias in aliases:
            self._aliases.pop(alias, None)


def _derive_bot_identity_key(platform_id: str, item: dict[str, Any]) -> str:
    """Return the bot identity key for a compiled listener subscription."""
    explicit = item.get("bot_identity_key")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    for key in ("token", "app_id", "credential_ref"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return f"{platform_id}:{value.strip()}"
    return f"{platform_id}:{item.get('node_name') or item.get('name')}"


def default_listener_compiler(
    *,
    workflow_id: UUID,
    workflow_version_id: UUID,
    item: dict[str, Any],
    platform_id: str,
) -> ListenerSubscription | None:
    """Compile a generic listener subscription for built-ins and plugins."""
    node_name = str(item.get("node_name") or item.get("name") or "").strip()
    if not node_name:
        return None
    bot_identity_key = _derive_bot_identity_key(platform_id, item)
    subscription_id = uuid5(
        _LISTENER_NAMESPACE,
        f"{workflow_version_id}:{platform_id}:{node_name}:{bot_identity_key}",
    )
    config = {
        key: value
        for key, value in item.items()
        if key not in {"name", "node_name", "type", "platform"}
    }
    return ListenerSubscription(
        id=subscription_id,
        workflow_id=workflow_id,
        workflow_version_id=workflow_version_id,
        node_name=node_name,
        platform=platform_id,
        bot_identity_key=bot_identity_key,
        config=config,
    )


listener_registry = ListenerRegistry()


def register_builtin_listeners() -> None:
    """Register the built-in Telegram, Discord, and QQ listener platforms."""
    if listener_registry.resolve(ListenerPlatform.TELEGRAM) is None:
        from orcheo.listeners.telegram import TelegramPollingAdapter

        listener_registry.register(
            ListenerMetadata(
                id=ListenerPlatform.TELEGRAM,
                display_name="Telegram",
                description=(
                    "Receive Telegram bot updates through managed long polling."
                ),
            ),
            compiler=default_listener_compiler,
            adapter_factory=lambda *, repository, subscription, runtime_id: (
                TelegramPollingAdapter(
                    repository=repository,
                    subscription=subscription,
                    runtime_id=runtime_id,
                )
            ),
        )
    if listener_registry.resolve(ListenerPlatform.DISCORD) is None:
        from orcheo.listeners.discord import DiscordGatewayAdapter

        listener_registry.register(
            ListenerMetadata(
                id=ListenerPlatform.DISCORD,
                display_name="Discord",
                description="Receive Discord bot messages through the Gateway.",
            ),
            compiler=default_listener_compiler,
            adapter_factory=lambda *, repository, subscription, runtime_id: (
                DiscordGatewayAdapter(
                    repository=repository,
                    subscription=subscription,
                    runtime_id=runtime_id,
                )
            ),
        )
    if listener_registry.resolve(ListenerPlatform.QQ) is None:
        from orcheo.listeners.qq import QQGatewayAdapter

        listener_registry.register(
            ListenerMetadata(
                id=ListenerPlatform.QQ,
                display_name="QQ",
                description="Receive QQ bot messages through the managed Gateway.",
            ),
            compiler=default_listener_compiler,
            adapter_factory=lambda *, repository, subscription, runtime_id: (
                QQGatewayAdapter(
                    repository=repository,
                    subscription=subscription,
                    runtime_id=runtime_id,
                )
            ),
        )


__all__ = [
    "ListenerAdapterFactory",
    "ListenerCompilerHook",
    "ListenerMetadata",
    "ListenerRegistration",
    "ListenerRegistry",
    "default_listener_compiler",
    "listener_registry",
    "register_builtin_listeners",
]
