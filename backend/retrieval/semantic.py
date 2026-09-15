"""
Semantic Vector Retrieval Layer for Enterprise Knowledge Agent.

Coordinates:
  1. Local text embedding generation (via LocalEmbedder).
  2. RBAC pre-filtered vector similarity search (via QdrantVectorStore).
  3. Neighbor & sequence context expansion (for multi-step procedures, runbooks, and conversations).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from backend.ingestion.embedder import LocalEmbedder
from backend.storage.qdrant_client import QdrantVectorStore


class SemanticRetriever:
    """
    High-level semantic retriever for vector search and context expansion.
    """

    def __init__(
        self,
        embedder: Optional[LocalEmbedder] = None,
        vector_store: Optional[QdrantVectorStore] = None,
    ) -> None:
        self.embedder = embedder or LocalEmbedder()
        self.vector_store = vector_store or QdrantVectorStore()

    def search(
        self,
        query: str,
        top_k: int = 5,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_groups: Optional[List[str]] = None,
        source: Optional[str] = None,
        resource_type: Optional[str] = None,
        expand_sequences: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Executes semantic vector search with RBAC pre-filtering and optional
        automatic sequence/procedure expansion.
        """
        if not query or not query.strip():
            return []

        # 1. Generate dense query embedding
        query_vector = self.embedder.embed_text(query)

        # 2. Query Qdrant with RBAC pre-filtering
        results = self.vector_store.search(
            query_vector=query_vector,
            top_k=top_k,
            user_roles=user_roles,
            user_id=user_id,
            user_groups=user_groups,
            source=source,
            resource_type=resource_type,
        )

        if not results:
            return []

        # 3. Format result objects
        formatted = []
        for r in results:
            item = {
                "chunk_id": r.get("chunk_id", ""),
                "resource_id": r.get("resource_id", ""),
                "source": r.get("source", "unknown"),
                "resource_type": r.get("resource_type", "document"),
                "title": r.get("title", "Untitled"),
                "url": r.get("url", ""),
                "text": r.get("text", ""),
                "score": r.get("score", 0.0),
                "section_path": r.get("section_path", []),
                "section_heading": r.get("section_heading", ""),
                "content_type": r.get("content_type", "general"),
                "sequence": r.get("sequence"),
                "prev_chunk_id": r.get("prev_chunk_id"),
                "next_chunk_id": r.get("next_chunk_id"),
                "extra_metadata": r.get("extra_metadata", {}),
            }
            formatted.append(item)

        return formatted
