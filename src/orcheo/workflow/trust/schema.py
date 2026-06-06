"""Declarative workflow graph schema for trusted production workflows."""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class DeclarativeNodeDef(BaseModel):
    """A single node definition in a declarative workflow graph."""

    id: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class DeclarativeEdgeDef(BaseModel):
    """A directed edge between two nodes."""

    source: str
    target: str


class DeclarativeConditionalEdgeDef(BaseModel):
    """A conditional edge with branching logic."""

    source: str
    branch: str
    mapping: dict[str, str] = Field(default_factory=dict)
    default: str | None = None


class DeclarativeTriggerDef(BaseModel):
    """A trigger definition for the workflow."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class DeclarativeListenerDef(BaseModel):
    """A listener definition for the workflow."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class DeclarativeCredentialReference(BaseModel):
    """A credential reference required by the workflow."""

    name: str
    scope: str | None = None


class DeclarativeWorkflowGraph(BaseModel):
    """Canonical declarative workflow graph payload for production workflows."""

    format: str = Field(default="orcheo-declarative-graph")
    version: int = Field(default=1)
    nodes: list[DeclarativeNodeDef] = Field(default_factory=list)
    edges: list[DeclarativeEdgeDef] = Field(default_factory=list)
    conditional_edges: list[DeclarativeConditionalEdgeDef] = Field(default_factory=list)
    triggers: list[DeclarativeTriggerDef] = Field(default_factory=list)
    listeners: list[DeclarativeListenerDef] = Field(default_factory=list)
    credential_references: list[DeclarativeCredentialReference] = Field(
        default_factory=list
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "DeclarativeNodeDef",
    "DeclarativeEdgeDef",
    "DeclarativeConditionalEdgeDef",
    "DeclarativeTriggerDef",
    "DeclarativeListenerDef",
    "DeclarativeCredentialReference",
    "DeclarativeWorkflowGraph",
]
