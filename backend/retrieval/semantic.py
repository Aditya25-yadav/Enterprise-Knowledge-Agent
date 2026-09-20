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
        user_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes semantic vector search with RBAC pre-filtering and optional
        automatic sequence/procedure expansion.
        """
        if not query or not query.strip():
            return []

        # Unpack user_context if provided
        if user_context:
            user_roles = user_roles or user_context.get("roles") or user_context.get("allowed_roles")
            user_id = user_id or user_context.get("user_id")
            user_groups = user_groups or user_context.get("groups")

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

        # 3. Format result objects (preserving 100% of payload metadata)
        formatted = []
        for r in results:
            item = dict(r)
            item["score"] = float(r.get("score", 0.0))
            item.setdefault("title", "Untitled")
            item.setdefault("url", "")
            item.setdefault("source", "unknown")
            item.setdefault("content_type", "general")
            item.setdefault("section_path", [])
            item.setdefault("extra_metadata", {})
            formatted.append(item)

        return formatted
