"""
OKF — Open Knowledge Format
============================
A standardized, source-agnostic schema that every connector normalizes its
data into. Downstream components (indexers, retrievers, the agent planner)
only ever talk to OKF objects — they never know or care whether a chunk
originally came from Google Drive, Notion, Confluence, or anywhere else.

Four core object types:
  - OKFPermission   : who is allowed to see a piece of content
  - OKFChunk        : a retrievable unit of text (goes into vector + keyword index)
  - OKFEntity       : a named thing extracted from content (goes into the graph)
  - OKFRelationship : a typed edge between two entities (goes into the graph)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class SourceSystem(str, Enum):
    GOOGLE_DRIVE = "google_drive"
    NOTION = "notion"
    CONFLUENCE = "confluence"
    JIRA = "jira"
    SLACK = "slack"
    GITHUB = "github"
    SHAREPOINT = "sharepoint"
    EMAIL = "email"


class PermissionLevel(str, Enum):
    """Mirrors the general shape of most source-system permission models."""
    OWNER = "owner"
    EDITOR = "editor"
    COMMENTER = "commenter"
    VIEWER = "viewer"


class OKFPrincipal(BaseModel):
    """
    A single grantee on a piece of content. Can be a specific user, a group,
    the whole domain/org, or fully public — mirrors how Drive/Confluence/etc.
    actually express permissions, so nothing is lost in translation.
    """
    type: str = Field(..., description="user | group | domain | anyone")
    identifier: str = Field(..., description="email, group id, domain name, or '*' for anyone")
    level: PermissionLevel


class OKFPermission(BaseModel):
    """
    Full permission record for one piece of source content. Stored alongside
    every chunk/entity so retrieval can filter by requesting user without a
    second round-trip to the source system on every query.
    """
    source_system: SourceSystem
    source_object_id: str = Field(..., description="native id of the file/page/message in its source system")
    principals: list[OKFPrincipal] = Field(default_factory=list)
    is_public: bool = False
    synced_at: datetime = Field(default_factory=datetime.utcnow)

    def allowed_identifiers(self) -> set[str]:
        """Flat set of every identifier (emails, group ids, 'domain', '*') that can see this content."""
        ids = {p.identifier for p in self.principals}
        if self.is_public:
            ids.add("*")
        return ids


class OKFChunk(BaseModel):
    """
    A single retrievable unit of text. This is what gets embedded and put
    into the vector index, and what gets indexed for keyword search.
    """
    chunk_id: str
    document_id: str = Field(..., description="id of the parent document this chunk belongs to")
    source_system: SourceSystem
    source_object_id: str
    title: str
    text: str
    chunk_index: int = Field(..., description="position of this chunk within its parent document")
    url: Optional[str] = Field(None, description="deep link back to the original source, for citations")
    author: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    mime_type: Optional[str] = None
    permission_id: str = Field(..., description="foreign key into the OKFPermission record for this document")
    metadata: dict = Field(default_factory=dict, description="source-specific extras (folder path, labels, etc.)")


class OKFEntity(BaseModel):
    """A named entity extracted from one or more chunks, for the knowledge graph."""
    entity_id: str
    name: str
    entity_type: str = Field(..., description="e.g. person, project, team, system, document")
    source_chunk_ids: list[str] = Field(default_factory=list, description="chunks this entity was extracted from")
    description: Optional[str] = None


class OKFRelationship(BaseModel):
    """A typed edge between two entities, for graph traversal / multi-hop reasoning."""
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str = Field(..., description="e.g. owns, mentions, works_on, depends_on")
    source_chunk_ids: list[str] = Field(default_factory=list, description="evidence chunks supporting this edge")
    weight: float = Field(1.0, description="confidence / strength of the relationship")


class OKFDocument(BaseModel):
    """
    Optional top-level record for a whole source document, useful for
    dedup, re-sync checks (has this file changed since last index?), and
    listing what's been ingested without pulling every chunk.
    """
    document_id: str
    source_system: SourceSystem
    source_object_id: str
    title: str
    url: Optional[str] = None
    mime_type: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    permission_id: str
    chunk_count: int = 0
    content_hash: Optional[str] = Field(None, description="hash of raw text, used to skip re-processing unchanged files")
