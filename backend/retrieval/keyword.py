"""
Keyword BM25 Search Retrieval Layer for Enterprise Knowledge Agent.

Coordinates:
  1. Lexical BM25+ index search via BM25Index.
  2. Database-level RBAC pre-filtering (roles, user_id, groups).
  3. Exact match retrieval for ticket IDs, error codes, symbols, and PR numbers.
  4. Preservation of 100% chunk payload metadata for downstream reranking and citations.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from backend.storage.bm25_index import BM25Index


class KeywordRetriever:
    """
    High-level keyword retriever for exact token and lexical BM25+ search.
    """

    def __init__(
        self,
        bm25_index: Optional[BM25Index] = None,
        index_path: Optional[str] = None,
    ) -> None:
        self.bm25_index = bm25_index or BM25Index(index_path=index_path or "./data/bm25_index.json")

    def search(
        self,
        query: str,
        top_k: int = 5,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_groups: Optional[List[str]] = None,
        source: Optional[str] = None,
        resource_type: Optional[str] = None,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes lexical BM25+ keyword search with strict RBAC pre-filtering.

        Args:
            query: Exact keyword/identifier query (e.g. 'PAY-928', 'HTTP 401', 'AuthService').
            top_k: Maximum number of chunks to return.
            user_roles: List of roles associated with the requesting user.
            user_id: User identifier for explicit permission checking.
            user_groups: List of groups associated with the requesting user.
            source: Optional source platform filter ('github', 'notion', etc.).
            resource_type: Optional resource type filter ('issue', 'page', etc.).
            user_context: Security context dictionary (unpacked into roles, user_id, groups).

        Returns:
            List of matching chunk dictionaries with scores and full metadata.
        """
        if not query or not query.strip():
            return []

        # Unpack user_context if provided
        if user_context:
            user_roles = user_roles or user_context.get("roles") or user_context.get("allowed_roles")
            user_id = user_id or user_context.get("user_id")
            user_groups = user_groups or user_context.get("groups")

        # Query BM25 index with RBAC pre-filtering
        results = self.bm25_index.search(
            query=query,
            top_k=top_k,
            user_roles=user_roles,
            user_id=user_id,
            user_groups=user_groups,
            source=source,
            resource_type=resource_type,
        )

        if not results:
            return []

        # Format result objects (preserving 100% of payload metadata)
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
