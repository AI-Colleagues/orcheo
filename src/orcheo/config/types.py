"""Type aliases for Orcheo configuration."""

from typing import Literal


CheckpointBackend = Literal["postgres"]
GraphStoreBackend = Literal["postgres"]
ChatKitBackend = Literal["postgres"]
AttachmentBlobBackend = Literal["postgres", "s3"]
RepositoryBackend = Literal["postgres"]
WorkspaceBackend = Literal["postgres"]
VaultBackend = Literal["postgres"]
WorkflowDefinitionMode = Literal["restricted", "unrestricted"]

__all__ = [
    "AttachmentBlobBackend",
    "ChatKitBackend",
    "CheckpointBackend",
    "GraphStoreBackend",
    "RepositoryBackend",
    "WorkspaceBackend",
    "VaultBackend",
    "WorkflowDefinitionMode",
]
