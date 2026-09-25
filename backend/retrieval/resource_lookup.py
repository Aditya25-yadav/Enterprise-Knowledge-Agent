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
        Looks up all chunks belonging to a resource by resource_id, url, chunk_id, or document title.
        Supports multi-tiered matching:
          1. Tier 1: Exact canonical URI, URL, chunk_id, or exact title match.
          2. Tier 2: Substring and normalized token-set overlap matching against document titles.
          3. Tier 3: Lexical BM25 title/document search fallback.
          4. Tier 4: Qdrant vector store scroll/search fallback.

        Results are returned in sequential reading order (chunk_index ascending) with strict RBAC.

        Args:
            resource_id: Canonical URI, URL, chunk ID, or document title/topic to retrieve.
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

        from backend.security import QdrantFilterTranslator, get_default_rbac_resolver
        import re

        resolver = get_default_rbac_resolver()
        sec_ctx = resolver.resolve_context(
            user_context or {"roles": user_roles or ["employee"], "user_id": user_id, "groups": user_groups or []}
        )

        # ── Tier 1: Exact Chunk ID Match ──────────────────────────────────────
        # If target matches a single unique chunk_id, return only that chunk
        if target in self.bm25_index.chunks_map:
            payload = self.bm25_index.chunks_map[target]
            if resolver.evaluate_access(sec_ctx, payload).is_allowed:
                item = dict(payload)
                item["score"] = 1.0
                item.setdefault("title", "Untitled")
                item.setdefault("url", "")
                item.setdefault("source", "unknown")
                item.setdefault("content_type", "general")
                item.setdefault("section_path", [])
                item.setdefault("extra_metadata", {})
                return [item]
            return []

        # Group indexed chunks by resource_id
        doc_groups: Dict[str, List[Dict[str, Any]]] = {}
        for c_id, payload in self.bm25_index.chunks_map.items():
            r_id = payload.get("resource_id") or c_id
            doc_groups.setdefault(r_id, []).append(payload)

        matched_doc_keys: List[str] = []

        # ── Tier 1: Exact Resource ID, URL, Title, PR/Issue Key Match ─────────
        target_lower = target.lower()
        for r_id, chunks in doc_groups.items():
            first_chunk = chunks[0]
            url = (first_chunk.get("url") or "").lower()
            title = (first_chunk.get("title") or "").strip().lower()
            r_id_lower = r_id.lower()

            # Exact resource_id, URL, or title equality
            if (
                r_id_lower == target_lower
                or url == target_lower
                or title == target_lower
                or (target.startswith("#") and (r_id_lower.endswith(target_lower) or f"pull/{target.lstrip('#')}" in r_id_lower or f"pr {target_lower}" in title or f"pr #{target.lstrip('#')}" in title))
                or (re.match(r"^[A-Z]+-[0-9]+$", target, re.IGNORECASE) and (target_lower in r_id_lower or target_lower in url or target_lower in title))
            ):
                matched_doc_keys.append(r_id)

        # ── Tier 2: Normalized Substring & Token-Set Overlap Match ─────────────
        if not matched_doc_keys:
            STOPWORDS = {
                "the", "a", "an", "and", "or", "of", "in", "to", "for", "with", "on", "at", "by",
                "from", "is", "are", "was", "were", "be", "been", "this", "that", "these", "those",
                "module", "doc", "docs", "document", "documents", "guide", "guides", "sop", "sops",
                "runbook", "runbooks", "playbook", "playbooks", "spec", "specification", "specifications",
                "steps", "instructions", "file", "files", "page", "pages", "wiki", "wikis", "overview",
                "about", "how", "what", "give", "me", "show", "find", "get", "fetch", "all",
            }

            def _tokenize(text: str) -> List[str]:
                return [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_\-\.#]+\b", text) if len(w) > 1]

            target_raw_tokens = _tokenize(target)
            target_content_tokens = [t for t in target_raw_tokens if t not in STOPWORDS]
            q_tokens = set(target_content_tokens if target_content_tokens else target_raw_tokens)

            norm_target = re.sub(r"[^a-z0-9]", " ", target_lower).strip()

            candidate_scores: List[tuple[float, str]] = []

            for r_id, chunks in doc_groups.items():
                first_chunk = chunks[0]
                title = first_chunk.get("title") or ""
                url = first_chunk.get("url") or ""
                last_segment = url.split("/")[-1] if "/" in url else ""

                norm_title = re.sub(r"[^a-z0-9]", " ", title.lower()).strip()
                norm_segment = re.sub(r"[^a-z0-9]", " ", last_segment.lower()).strip()

                # Substring match score
                sub_score = 0.0
                if norm_target and norm_title:
                    if norm_target in norm_title or norm_title in norm_target:
                        sub_score = 0.9
                if norm_target and norm_segment:
                    if norm_target in norm_segment or norm_segment in norm_target:
                        sub_score = max(sub_score, 0.85)

                # Token overlap score
                doc_raw_tokens = _tokenize(f"{title} {last_segment}")
                doc_content_tokens = [t for t in doc_raw_tokens if t not in STOPWORDS]
                d_tokens = set(doc_content_tokens if doc_content_tokens else doc_raw_tokens)

                tok_score = 0.0
                if q_tokens and d_tokens:
                    overlap = q_tokens.intersection(d_tokens)
                    overlap_count = len(overlap)
                    if overlap_count > 0:
                        precision = overlap_count / len(q_tokens)
                        jaccard = overlap_count / len(q_tokens.union(d_tokens))
                        if precision == 1.0:
                            tok_score = 0.8 + 0.15 * jaccard
                        elif precision >= 0.5:
                            tok_score = 0.5 * precision + 0.3 * jaccard

                best_doc_score = max(sub_score, tok_score)
                if best_doc_score >= 0.5:
                    candidate_scores.append((best_doc_score, r_id))

            if candidate_scores:
                candidate_scores.sort(key=lambda x: x[0], reverse=True)
                top_score = candidate_scores[0][0]
                # Include all candidates matching top score
                for score, r_id in candidate_scores:
                    if score >= top_score - 0.05:
                        matched_doc_keys.append(r_id)

        # ── Tier 3: BM25 Lexical Title / Document Search Fallback ─────────────
        if not matched_doc_keys and hasattr(self.bm25_index, "search") and self.bm25_index.chunks_map:
            try:
                bm25_results = self.bm25_index.search(query=target, top_k=10)
                if bm25_results:
                    # Find highest scored document group
                    doc_bm25_scores: Dict[str, float] = {}
                    for hit in bm25_results:
                        r_id = hit.get("resource_id") or hit.get("chunk_id", "")
                        if r_id in doc_groups:
                            score = float(hit.get("score", 0.0))
                            doc_bm25_scores[r_id] = max(doc_bm25_scores.get(r_id, 0.0), score)

                    if doc_bm25_scores:
                        best_r_id = max(doc_bm25_scores.items(), key=lambda x: x[1])[0]
                        matched_doc_keys.append(best_r_id)
            except Exception:
                pass

        # ── Collect and Format Chunks from In-Memory BM25 Index ────────────────
        matches: List[Dict[str, Any]] = []
        if matched_doc_keys:
            for r_id in matched_doc_keys:
                for payload in doc_groups.get(r_id, []):
                    # Centralized RBAC Pre-Filter
                    if not resolver.evaluate_access(sec_ctx, payload).is_allowed:
                        continue
                    item = dict(payload)
                    item["score"] = 1.0
                    item.setdefault("title", "Untitled")
                    item.setdefault("url", "")
                    item.setdefault("source", "unknown")
                    item.setdefault("content_type", "general")
                    item.setdefault("section_path", [])
                    item.setdefault("extra_metadata", {})
                    matches.append(item)

        # ── Tier 4: Qdrant Vector Store Fallback ──────────────────────────────
        if not matches and hasattr(self.vector_store, "_client"):
            try:
                from qdrant_client.http import models as rest

                resource_filter = QdrantFilterTranslator.build_filter(
                    context=sec_ctx,
                    extra_filters=[rest.FieldCondition(key="resource_id", match=rest.MatchValue(value=target))],
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

        # ── Sort sequentially by chunk_index ─────────────────────────────────
        matches.sort(key=lambda x: int(x.get("chunk_index", 0)))
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
