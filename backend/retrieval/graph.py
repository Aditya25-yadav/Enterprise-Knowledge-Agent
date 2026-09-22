"""
Graph & Relationship Retrieval Layer for Enterprise Knowledge Agent.

Enables structural knowledge topology traversal:
  1. Parent-Child Hierarchy Navigation (e.g. repo -> files/issues, epic -> subtasks, folder -> files).
  2. Horizontal Sibling Expansion (prev_chunk_id / next_chunk_id for procedural runbooks & message turns).
  3. Small-to-Large Hierarchical Expansion (atomic chunk -> parent section context).
  4. Sequence & Procedure Assembly (reconstructing ordered step workflows).
  5. Strict database-level Role-Based Access Control (RBAC) pre-filtering across all hops.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class GraphRetriever:
    """
    Traverses knowledge relationships, parent-child hierarchies, and sibling chunk chains.
    """

    def __init__(
        self,
        bm25_index: Optional[BM25Index] = None,
        vector_store: Optional[QdrantVectorStore] = None,
    ) -> None:
        self.bm25_index = bm25_index or BM25Index()
        self.vector_store = vector_store or QdrantVectorStore()

    def _can_access(
        self,
        payload: Dict[str, Any],
        user_roles_set: Set[str],
        user_id: Optional[str],
        user_groups_set: Set[str],
    ) -> bool:
        """Helper to enforce strict RBAC pre-filtering on a chunk payload."""
        is_public = payload.get("is_public", True)
        allowed_roles = set(payload.get("allowed_roles") or [])
        allowed_users = payload.get("allowed_users") or []
        allowed_groups = set(payload.get("allowed_groups") or [])

        return (
            is_public
            or bool(user_roles_set.intersection(allowed_roles))
            or bool(user_id and user_id in allowed_users)
            or bool(user_groups_set.intersection(allowed_groups))
        )

    def _format_chunk(self, payload: Dict[str, Any], score: float = 1.0) -> Dict[str, Any]:
        """Formats chunk dictionary with safe defaults and 100% metadata preservation."""
        item = dict(payload)
        item["score"] = float(score)
        item.setdefault("title", "Untitled")
        item.setdefault("url", "")
        item.setdefault("source", "unknown")
        item.setdefault("content_type", "general")
        item.setdefault("section_path", [])
        item.setdefault("extra_metadata", {})
        return item

    # ── 1. Parent-Child Hierarchy Traversal ───────────────────────────────────

    def get_children(
        self,
        parent_id: str,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_groups: Optional[List[str]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        max_children: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all child resources/chunks directly belonging to a parent resource ID.
        (e.g., all files inside a repository, all subtasks in a project/epic, all subpages in a workspace).

        Args:
            parent_id: Canonical ID of the parent container or document.
            user_context: Security context dictionary for RBAC pre-filtering.
            max_children: Maximum child resources to return.

        Returns:
            List of authorized child chunks sorted by sequential index.
        """
        if not parent_id or not parent_id.strip():
            return []

        target_parent = parent_id.strip()

        if user_context:
            user_roles = user_roles or user_context.get("roles") or user_context.get("allowed_roles")
            user_id = user_id or user_context.get("user_id")
            user_groups = user_groups or user_context.get("groups")

        user_roles_set = set(user_roles or [])
        user_groups_set = set(user_groups or [])

        children: List[Dict[str, Any]] = []

        # Scan in-memory chunks_map
        for c_id, payload in self.bm25_index.chunks_map.items():
            p_id = payload.get("parent_id", "")
            r_id = payload.get("resource_id", "")
            extra = payload.get("extra_metadata") or {}
            extra_p_id = extra.get("parent_id", "") if isinstance(extra, dict) else ""

            # Match on parent_id, extra_metadata.parent_id, or if resource_id indicates child relationship
            is_child = (
                p_id == target_parent
                or extra_p_id == target_parent
                or (target_parent in r_id and r_id != target_parent)
            )

            if not is_child:
                continue

            if not self._can_access(payload, user_roles_set, user_id, user_groups_set):
                continue

            children.append(self._format_chunk(payload))
            if len(children) >= max_children:
                break

        children.sort(key=lambda x: x.get("chunk_index", 0))
        return children

    # ── 2. Horizontal Sibling Expansion (Prev / Next) ─────────────────────────

    def get_neighbors(
        self,
        chunk_id: str,
        window_before: int = 1,
        window_after: int = 1,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_groups: Optional[List[str]] = None,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Expands a matched chunk by traversing its bidirectional sibling links
        (`prev_chunk_id` and `next_chunk_id`) to recover preceding and succeeding context.

        Args:
            chunk_id: ID of the central matched chunk.
            window_before: Number of preceding sibling chunks to retrieve.
            window_after: Number of succeeding sibling chunks to retrieve.
            user_context: Security context dictionary for RBAC pre-filtering.

        Returns:
            Ordered list of authorized sibling chunks spanning [chunk - window_before, chunk + window_after].
        """
        if not chunk_id or not chunk_id.strip():
            return []

        target_id = chunk_id.strip()

        if user_context:
            user_roles = user_roles or user_context.get("roles") or user_context.get("allowed_roles")
            user_id = user_id or user_context.get("user_id")
            user_groups = user_groups or user_context.get("groups")

        user_roles_set = set(user_roles or [])
        user_groups_set = set(user_groups or [])

        center_payload = self.bm25_index.chunks_map.get(target_id)
        if not center_payload:
            return []

        if not self._can_access(center_payload, user_roles_set, user_id, user_groups_set):
            return []

        before_chunks: List[Dict[str, Any]] = []
        after_chunks: List[Dict[str, Any]] = []

        # 1. Traverse preceding siblings (prev_chunk_id)
        curr_prev = center_payload.get("prev_chunk_id")
        steps = 0
        while curr_prev and steps < window_before:
            p_payload = self.bm25_index.chunks_map.get(curr_prev)
            if not p_payload:
                break
            if self._can_access(p_payload, user_roles_set, user_id, user_groups_set):
                before_chunks.append(self._format_chunk(p_payload))
            curr_prev = p_payload.get("prev_chunk_id")
            steps += 1

        before_chunks.reverse()  # Restore forward sequential order

        # 2. Traverse succeeding siblings (next_chunk_id)
        curr_next = center_payload.get("next_chunk_id")
        steps = 0
        while curr_next and steps < window_after:
            n_payload = self.bm25_index.chunks_map.get(curr_next)
            if not n_payload:
                break
            if self._can_access(n_payload, user_roles_set, user_id, user_groups_set):
                after_chunks.append(self._format_chunk(n_payload))
            curr_next = n_payload.get("next_chunk_id")
            steps += 1

        # Return ordered sequence: [before..., center, after...]
        return before_chunks + [self._format_chunk(center_payload)] + after_chunks

    # ── 3. Sequence & Procedure Expansion ────────────────────────────────────

    def get_full_sequence(
        self,
        sequence_id: str,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all ordered steps for a procedural runbook or workflow by its sequence_id.

        Returns:
            List of authorized step chunks sorted by step number ascending (1..N).
        """
        if not sequence_id or not sequence_id.strip():
            return []

        target_seq = sequence_id.strip()
        ctx = user_context or {}
        user_roles_set = set(ctx.get("roles") or ctx.get("allowed_roles") or [])
        user_id = ctx.get("user_id")
        user_groups_set = set(ctx.get("groups") or [])

        steps: List[Dict[str, Any]] = []

        for c_id, payload in self.bm25_index.chunks_map.items():
            seq_info = payload.get("sequence")
            if isinstance(seq_info, dict) and seq_info.get("sequence_id") == target_seq:
                if self._can_access(payload, user_roles_set, user_id, user_groups_set):
                    steps.append(self._format_chunk(payload))

        # Sort by step number ascending
        steps.sort(key=lambda x: (x.get("sequence") or {}).get("step", 0))
        return steps
