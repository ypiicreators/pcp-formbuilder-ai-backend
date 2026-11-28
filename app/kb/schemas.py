"""Knowledge Base API models — keep in sync with admin `knowledgeBase.types.ts`."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class KbKind(str, Enum):
    template = "template"
    api_endpoint = "api_endpoint"
    policy = "policy"
    recommendation = "recommendation"
    bundle = "bundle"


class KbStatus(str, Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class KbMode(str, Enum):
    generate = "generate"
    edit = "edit"
    document = "document"


class KbChangeType(str, Enum):
    added = "added"
    modified = "modified"
    archived = "archived"


class KbAppliesTo(BaseModel):
    modes: list[KbMode] = Field(default_factory=list)
    fieldTypes: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class KbEntryInput(BaseModel):
    kind: KbKind
    title: str
    body: str
    appliesTo: KbAppliesTo
    payload: dict[str, Any] | None = None


class KbCreateInput(KbEntryInput):
    key: str


class KbUpdateInput(KbEntryInput):
    expectedVersion: int


class KbEntry(BaseModel):
    key: str
    kind: KbKind
    title: str
    body: str
    appliesTo: KbAppliesTo
    payload: dict[str, Any] | None
    status: KbStatus
    version: int
    publishedVersion: int | None
    hasPendingChanges: bool
    updatedBy: str
    updatedAt: str


class KbEntryVersion(BaseModel):
    version: int
    title: str
    body: str
    appliesTo: KbAppliesTo
    payload: dict[str, Any] | None
    updatedBy: str
    updatedAt: str


class KbListResult(BaseModel):
    items: list[KbEntry]
    total: int
    pendingCount: int


class KbPendingChange(BaseModel):
    key: str
    title: str
    kind: KbKind
    change: KbChangeType
    fromVersion: int | None
    toVersion: int | None


class KbPublishInput(BaseModel):
    note: str
    keys: list[str] | None = None


class KbRelease(BaseModel):
    id: str
    number: int
    note: str
    publishedBy: str
    publishedAt: str
    entryCount: int
    changes: list[KbPendingChange]
    isCurrent: bool