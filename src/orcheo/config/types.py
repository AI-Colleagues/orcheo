"""Type aliases for Orcheo configuration."""

from typing import Literal


CheckpointBackend = Literal["postgres"]
GraphStoreBackend = Literal["postgres"]
ChatKitBackend = Literal["postgres"]
RepositoryBackend = Literal["postgres"]
WorkspaceBackend = Literal["postgres"]
VaultBackend = Literal["postgres"]

__all__ = [
    "ChatKitBackend",
    "CheckpointBackend",
    "GraphStoreBackend",
    "RepositoryBackend",
    "WorkspaceBackend",
    "VaultBackend",
]
