"""Type aliases for Orcheo configuration."""

from typing import Literal


CheckpointBackend = Literal["sqlite", "postgres"]
GraphStoreBackend = Literal["sqlite", "postgres"]
ChatKitBackend = Literal["sqlite", "postgres"]
RepositoryBackend = Literal["inmemory", "sqlite", "postgres"]
WorkspaceBackend = Literal["inmemory", "sqlite", "postgres"]
VaultBackend = Literal["inmemory", "file", "aws_kms", "postgres"]

__all__ = [
    "ChatKitBackend",
    "CheckpointBackend",
    "GraphStoreBackend",
    "RepositoryBackend",
    "WorkspaceBackend",
    "VaultBackend",
]
