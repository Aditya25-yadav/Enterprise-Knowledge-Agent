"""
Resource Lookup Retrieval Layer for Enterprise Knowledge Agent.

Provides direct, deterministic retrieval of full documents, resources, or specific
chunks by their canonical URI, URL, chunk_id, or title, without embedding overhead.
Reconstructs multi-chunk documents in sequential reading order with strict RBAC enforcement
and complete metadata fidelity.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class ResourceLookupRetriever:
    """
    Retrieves full documents or chunks by exact canonical resource ID, URL, or chunk ID.
    Guarantees sequential multi-chunk stitching, outline extraction, and RBAC pre-filtering.
    """

    def __init__(
        self,
        bm25_index: Optional[BM25Index] = None,
        vector_store: Optional[QdrantVectorStore] = None,
    ) -> None:
        self.bm25_index = bm25_index or BM25Index()
        self.vector_store = vector_store or QdrantVectorStore()

    def lookup(
        self,
        resource_id: str,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_groups: Optional[List[str]] = None,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Looks up all chunks belonging to a resource by resource_id, url, chunk_id, or exact title.
        Results are returned in sequential reading order (chunk_index ascending).

        Args:
            resource_id: Canonical URI, URL, chunk ID, or exact document title to retrieve.
            user_roles: List of roles associated with the requesting user.
            user_id: User identifier for explicit permission checking.
            user_groups: List of groups associated with the requesting user.
            user_context: Security context dictionary (unpacked into roles, user_id, groups).

        Returns:
            List of authorized matching chunk dictionaries in sequential order.
        """
        if not resource_id or not resource_id.strip():
            return []

        target = resource_id.strip()

        # Unpack user_context if provided
        if user_context:
            user_roles = user_roles or user_context.get("roles") or user_context.get("allowed_roles")
            user_id = user_id or user_context.get("user_id")
            user_groups = user_groups or user_context.get("groups")

        user_roles_set = set(user_roles or [])
        user_groups_set = set(user_groups or [])

        matches: List[Dict[str, Any]] = []

        # 1. Search in-memory chunks_map (fastest, O(N))
        for c_id, payload in self.bm25_index.chunks_map.items():
            r_id = payload.get("resource_id", "")
            url = payload.get("url", "")
            title = payload.get("title", "")

            # Match on resource_id, url, chunk_id, or title
            is_match = (
                r_id == target
                or url == target
                or c_id == target
                or title.lower() == target.lower()
                or (target.startswith("#") and r_id.endswith(target))
            )

            if not is_match:
                continue

            # RBAC Pre-Filter
            is_public = payload.get("is_public", True)
            allowed_roles = set(payload.get("allowed_roles") or [])
            allowed_users = payload.get("allowed_users") or []
            allowed_groups = set(payload.get("allowed_groups") or [])

            can_access = (
                is_public
                or bool(user_roles_set.intersection(allowed_roles))
                or (user_id and user_id in allowed_users)
                or bool(user_groups_set.intersection(allowed_groups))
            )

            if not can_access:
                continue

            item = dict(payload)
            item["score"] = 1.0  # Exact deterministic match
            item.setdefault("title", "Untitled")
            item.setdefault("url", "")
            item.setdefault("source", "unknown")
            item.setdefault("content_type", "general")
            item.setdefault("section_path", [])
            item.setdefault("extra_metadata", {})
            matches.append(item)

        # 2. Fallback to Qdrant vector store if not found in in-memory BM25 index
        if not matches and hasattr(self.vector_store, "_client"):
            try:
                from qdrant_client.http import models as rest
                # Build RBAC filter
                rbac_should: List[rest.Condition] = [
                    rest.FieldCondition(key="is_public", match=rest.MatchValue(value=True))
                ]
                if user_roles:
                    rbac_should.append(
                        rest.FieldCondition(key="allowed_roles", match=rest.MatchAny(any=user_roles))
                    )
                if user_id:
                    rbac_should.append(
                        rest.FieldCondition(key="allowed_users", match=rest.MatchValue(value=user_id))
                    )
                if user_groups:
                    rbac_should.append(
                        rest.FieldCondition(key="allowed_groups", match=rest.MatchAny(any=user_groups))
                    )

                resource_filter = rest.Filter(
                    must=[
                        rest.FieldCondition(key="resource_id", match=rest.MatchValue(value=target)),
                        rest.Filter(should=rbac_should),
                    ]
                )

                scroll_results, _ = self.vector_store._client.scroll(
                    collection_name=self.vector_store.collection_name,
                    scroll_filter=resource_filter,
                    limit=100,
                    with_payload=True,
                    with_vectors=False,
                )

                for pt in scroll_results:
                    item = dict(pt.payload or {})
                    item["score"] = 1.0
                    item.setdefault("title", "Untitled")
                    item.setdefault("url", "")
                    item.setdefault("source", "unknown")
                    item.setdefault("content_type", "general")
                    item.setdefault("section_path", [])
                    item.setdefault("extra_metadata", {})
                    matches.append(item)
            except Exception:
                pass

        # 3. Sort chunks sequentially by chunk_index
        matches.sort(key=lambda x: x.get("chunk_index", 0))
        return matches

    def get_document(
        self,
        resource_id: str,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Reconstructs an entire document by fetching all its authorized chunks and
        stitching their text bodies in sequential order without metadata discrepancy.

        Returns:
            Dictionary with aggregated document metadata, complete outline/section_paths,
            and stitched full_text, or None if not found.
        """
        chunks = self.lookup(resource_id=resource_id, user_context=user_context)
        if not chunks:
            return None

        first = chunks[0]
        total_chunks = first.get("total_chunks", len(chunks))

        # Stitch text preserving original paragraph breaks
        stitched_text = "\n\n".join(c.get("text", "") for c in chunks)

        # Collect unique breadcrumb section paths
        section_paths: List[List[str]] = []
        for c in chunks:
            path = c.get("section_path")
            if path and path not in section_paths:
                section_paths.append(path)

        # Merge extra metadata across chunks without losing root attributes
        merged_extra: Dict[str, Any] = dict(first.get("extra_metadata") or {})
        for c in chunks[1:]:
            for k, v in (c.get("extra_metadata") or {}).items():
                if k not in merged_extra:
                    merged_extra[k] = v

        return {
            "resource_id": first.get("resource_id", resource_id),
            "title": first.get("title", "Untitled"),
            "source": first.get("source", "unknown"),
            "url": first.get("url", ""),
            "resource_type": first.get("resource_type", "document"),
            "total_chunks": total_chunks,
            "retrieved_chunks_count": len(chunks),
            "is_complete": len(chunks) == total_chunks,
            "section_paths": section_paths,
            "permissions": first.get("permissions", {}),
            "created_at": first.get("created_at"),
            "updated_at": first.get("updated_at"),
            "extra_metadata": merged_extra,
            "full_text": stitched_text,
            "stitched_text": stitched_text,
            "chunks": chunks,
        }
