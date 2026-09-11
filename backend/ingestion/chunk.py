"""
Structure-Aware Chunk Data Model for Enterprise Knowledge Ingestion.

Represents an atomic, retrievable chunk of knowledge derived from an OKFConcept
or Document. Each chunk carries rich structural hierarchy (parent-child, sibling links,
section breadcrumbs, sequence step tracking), security boundaries (RBAC permissions),
and provenance metadata.

Pipeline position:
    OKFConcept / Document
           ↓
     SmartOKFChunker
           ↓
     List[SmartChunk]  <-- THIS DATA MODEL
           ↓
    Embedder / Qdrant / BM25 / Reranker
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class ContentType(str, Enum):
    """
    Semantic classification of knowledge chunk types to drive content-specific
    retrieval and context expansion strategies.
    """
    DOCUMENT_SECTION = "document_section"      # Standard document headings, paragraphs (Notion, Drive, Confluence)
    PROCEDURE_STEP = "procedure_step"          # Ordered steps, runbooks, setup workflows (Step 1, Step 2...)
    CONVERSATION_THREAD = "conversation_thread"# Message turns, replies, channels (Gmail, Slack)
    CODE_SYMBOL = "code_symbol"                # Functions, classes, code blocks (GitHub)
    TABLE_RECORD = "table_record"              # Databases, spreadsheets, markdown tables
    GENERAL = "general"                        # Unclassified fallback content

    @classmethod
    def from_string(cls, value: str) -> "ContentType":
        try:
            return cls(value.lower().strip())
        except ValueError:
            return cls.GENERAL


@dataclass
class SequenceInfo:
    """
    Carries order and progress metadata for sequential procedures or timelines.
    Enables retrieval expansion from a single matched step to the complete workflow.
    """
    sequence_id: str                           # Unique procedure ID, e.g. 'oauth_pkce_setup'
    step: int                                  # 1-indexed step number, e.g. 2
    total_steps: int                           # Total steps in procedure, e.g. 4
    step_title: Optional[str] = None           # E.g. 'Step 2: Generate Code Challenge'

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SmartChunk:
    """
    An atomic, structure-aware searchable chunk with complete provenance,
    parent-child relationships, bidirectional sibling links, and RBAC metadata.
    """
    # ── 1. Identity & Provenance ─────────────────────────────────────────────
    chunk_id: str                              # Unique ID (e.g. 'github:repo:owner/name#c3')
    resource_id: str                           # Parent canonical resource ID (e.g. 'github:repo:owner/name')
    source: str                                # Source platform ('github', 'notion', 'dropbox', 'gmail', 'slack')
    resource_type: str                         # E.g. 'repository', 'file', 'issue', 'page', 'email'
    title: str = ""                            # Resource or document title
    url: Optional[str] = None                  # Direct link to source for agent citations

    # ── 2. Content & Payload ─────────────────────────────────────────────────
    text: str = ""                             # The actual chunk content to be embedded & searched
    content_type: ContentType = ContentType.GENERAL # Classification for strategy routing

    # ── 3. Hierarchy & Relationships (Small-to-Large & Neighbors) ────────────
    parent_id: Optional[str] = None            # Parent section or document ID
    parent_text: Optional[str] = None          # Large parent context block (for small-to-large RAG)
    prev_chunk_id: Optional[str] = None        # Sibling pointer for preceding context expansion
    next_chunk_id: Optional[str] = None        # Sibling pointer for succeeding context expansion
    chunk_index: int = 0                       # Sequence index within parent document
    total_chunks: int = 1                      # Total chunks generated for parent document

    # ── 4. Structural Navigation & Sequence ──────────────────────────────────
    section_path: List[str] = field(default_factory=list) # Breadcrumb stack, e.g. ['Auth', 'OAuth 2.0', 'PKCE']
    section_heading: Optional[str] = None      # Markdown section header, e.g. '### PKCE Verification'
    sequence: Optional[SequenceInfo] = None    # Set if chunk represents a step in an ordered procedure

    # ── 5. Enterprise Security & Metadata ────────────────────────────────────
    permissions: Dict[str, Any] = field(default_factory=lambda: {
        "is_public": True,
        "allowed_roles": ["employee"],
        "allowed_users": [],
        "allowed_groups": [],
    })
    created_at: Optional[str] = None           # ISO 8601 creation timestamp
    updated_at: Optional[str] = None           # ISO 8601 last modified timestamp
    extra_metadata: Dict[str, Any] = field(default_factory=dict) # Platform-specific tags/details

    def to_dict(self) -> Dict[str, Any]:
        """Convert chunk to full dictionary."""
        d = asdict(self)
        d["content_type"] = self.content_type.value
        if self.sequence:
            d["sequence"] = self.sequence.to_dict()
        return d

    def to_qdrant_payload(self) -> Dict[str, Any]:
        """
        Convert chunk to a payload dictionary optimized for Qdrant storage,
        neighbor expansion, and metadata/RBAC pre-filtering.
        """
        return {
            "chunk_id": self.chunk_id,
            "resource_id": self.resource_id,
            "source": self.source,
            "resource_type": self.resource_type,
            "text": self.text,
            "title": self.title,
            "url": self.url or "",
            "content_type": self.content_type.value,
            "parent_id": self.parent_id or "",
            "prev_chunk_id": self.prev_chunk_id or "",
            "next_chunk_id": self.next_chunk_id or "",
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "section_path": self.section_path,
            "section_heading": self.section_heading or "",
            "sequence": self.sequence.to_dict() if self.sequence else None,
            "permissions": self.permissions,
            # Flattened permission fields for fast Qdrant indexing
            "is_public": self.permissions.get("is_public", True),
            "allowed_roles": self.permissions.get("allowed_roles", ["employee"]),
            "allowed_users": self.permissions.get("allowed_users", []),
            "allowed_groups": self.permissions.get("allowed_groups", []),
            "created_at": self.created_at or "",
            "updated_at": self.updated_at or "",
            "extra_metadata": self.extra_metadata,
        }

    def to_context_string(self) -> str:
        """
        Format chunk as a clear citation block with breadcrumbs for LLM prompt context.
        """
        path_str = " > ".join(self.section_path) if self.section_path else (self.section_heading or "")
        header = f"[{self.source.upper()}] {self.title}"
        if path_str:
            header += f" > {path_str}"
        if self.url:
            header += f" ({self.url})"

        if self.sequence:
            header += f" [Step {self.sequence.step}/{self.sequence.total_steps}]"

        return f"{header}\n{self.text}"


# Alias for backward compatibility
Chunk = SmartChunk
