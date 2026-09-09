"""
Chunk Data Model for Enterprise Knowledge Ingestion.

Represents an atomic, retrievable chunk of knowledge derived from an OKFConcept
or Document. Each chunk carries rich metadata and permission boundaries so that
downstream systems (Vector DB, BM25 Index, RBAC Filter, LLM Context Builder)
can operate without needing to look up the original document.

Pipeline position:
    OKFConcept / Document
           ↓
      OKFChunker
           ↓
     List[Chunk]  <-- THIS DATA MODEL
           ↓
    Embedder / Qdrant / BM25 / Reranker
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Chunk:
    """
    An atomic searchable chunk with complete provenance and security metadata.
    """
    chunk_id: str                              # Unique hash or ID (e.g. 'github:repo:owner/name#chunk_0')
    resource_id: str                           # Parent canonical ID (e.g. 'github:repo:owner/name')
    source: str                                # Source platform: 'github', 'notion', 'dropbox', 'gmail'
    resource_type: str                         # E.g. 'repository', 'file', 'issue', 'page', 'email'
    text: str                                  # The actual chunk content to be embedded & searched
    title: str = ""                            # Resource title or document name
    url: Optional[str] = None                  # Direct link to source for agent citations
    section_heading: Optional[str] = None      # Markdown section header if applicable (e.g. '## Installation')
    chunk_index: int = 0                       # Sequence index within parent document
    total_chunks: int = 1                      # Total chunks generated for parent document
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
        return asdict(self)

    def to_qdrant_payload(self) -> Dict[str, Any]:
        """
        Convert chunk to a payload dictionary optimized for Qdrant storage
        and metadata/RBAC filtering.
        """
        return {
            "chunk_id": self.chunk_id,
            "resource_id": self.resource_id,
            "source": self.source,
            "resource_type": self.resource_type,
            "text": self.text,
            "title": self.title,
            "url": self.url or "",
            "section_heading": self.section_heading or "",
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "permissions": self.permissions,
            # Flattened permission fields for fast Qdrant filtering
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
        Format chunk as a clear citation block for LLM prompt context.
        """
        header = f"[{self.source.upper()}] {self.title}"
        if self.section_heading:
            header += f" > {self.section_heading}"
        if self.url:
            header += f" ({self.url})"
        return f"{header}\n{self.text}"
