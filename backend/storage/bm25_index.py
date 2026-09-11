"""
BM25 Keyword Search Index for Enterprise Knowledge Retrieval.

Provides exact lexical/keyword matching for:
  - Exact identifiers: 'PR #1842', 'PAY-928', 'Commit 7a8b9c'
  - Code symbols: 'AuthService.validate_token()', 'JWT_SECRET'
  - Error codes: 'HTTP 401', 'ECONNREFUSED'
  - Exact person names, repo names, and file paths

Supports:
  1. Code- and identifier-aware tokenization (preserving snake_case, camelCase, and ticket keys).
  2. Strict RBAC pre-filtering (so keyword search obeys the same access control rules as vector search).
  3. Disk serialization and fast in-memory query execution using BM25Plus.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from rank_bm25 import BM25Plus

from backend.ingestion.chunk import SmartChunk


class BM25Index:
    """
    In-memory BM25 lexical search index with disk persistence and RBAC filtering.
    """

    DEFAULT_INDEX_PATH = "./data/bm25_index.json"

    def __init__(self, index_path: Optional[str] = None) -> None:
        self.index_path = index_path or os.getenv("BM25_INDEX_PATH", self.DEFAULT_INDEX_PATH)
        self.chunks_map: Dict[str, Dict[str, Any]] = {} # chunk_id -> payload dict
        self.chunk_ids: List[str] = []                  # ordered list of chunk_ids
        self.tokenized_corpus: List[List[str]] = []     # tokenized text for each chunk_id
        self._bm25: Optional[BM25Plus] = None

        # Load from disk if file exists
        if os.path.exists(self.index_path):
            self.load_from_disk()

    # ── Tokenizer ────────────────────────────────────────────────────────────

    def tokenize(self, text: str) -> List[str]:
        """
        Tokenizes text preserving code symbols, snake_case, kebab-case, and ticket identifiers.
        E.g. 'PAY-928: fix auth_token in AuthService' ->
             ['pay-928', 'pay', '928', 'fix', 'auth_token', 'auth', 'token', 'authservice']
        """
        if not text:
            return []

        text_lower = text.lower()
        tokens = set()

        # 1. Extract alphanumeric tokens with hyphens/underscores/dots (e.g. 'pay-928', 'auth_token', 'v1.0')
        raw_words = re.findall(r"[a-zA-Z0-9_\-\.#]+", text_lower)
        for w in raw_words:
            tokens.add(w)

            # Split sub-tokens on hyphens, underscores, and dots
            sub_parts = re.split(r"[_\-\.#]+", w)
            for sp in sub_parts:
                if sp and len(sp) > 1:
                    tokens.add(sp)

        # 2. Extract standard alphanumeric words (for natural language text)
        words = re.findall(r"\b[a-zA-Z0-9]+\b", text_lower)
        for w in words:
            if len(w) > 1:
                tokens.add(w)

        return list(tokens)

    # ── Indexing Operations ──────────────────────────────────────────────────

    def add_chunks(self, chunks: List[SmartChunk]) -> int:
        """
        Adds or updates a batch of SmartChunks in the BM25 index.
        Rebuilds the BM25Plus model.
        """
        if not chunks:
            return 0

        for chunk in chunks:
            payload = chunk.to_qdrant_payload()
            c_id = chunk.chunk_id

            # Create searchable text representation (Title + Breadcrumbs + Content)
            searchable_text = f"{chunk.title} {' '.join(chunk.section_path)} {chunk.text}"
            tokens = self.tokenize(searchable_text)

            if c_id in self.chunks_map:
                # Update existing chunk
                idx = self.chunk_ids.index(c_id)
                self.chunks_map[c_id] = payload
                self.tokenized_corpus[idx] = tokens
            else:
                # Add new chunk
                self.chunks_map[c_id] = payload
                self.chunk_ids.append(c_id)
                self.tokenized_corpus.append(tokens)

        # Re-initialize BM25 model
        if self.tokenized_corpus:
            self._bm25 = BM25Plus(self.tokenized_corpus)

        return len(chunks)

    def delete_by_resource_id(self, resource_id: str) -> int:
        """
        Deletes all chunks associated with a parent resource ID.
        """
        to_delete = [
            c_id for c_id, payload in self.chunks_map.items()
            if payload.get("resource_id") == resource_id
        ]

        if not to_delete:
            return 0

        for c_id in to_delete:
            idx = self.chunk_ids.index(c_id)
            del self.chunks_map[c_id]
            del self.chunk_ids[idx]
            del self.tokenized_corpus[idx]

        if self.tokenized_corpus:
            self._bm25 = BM25Plus(self.tokenized_corpus)
        else:
            self._bm25 = None

        return len(to_delete)

    def clear(self) -> None:
        """Clears all indexed chunks from memory and disk."""
        self.chunks_map.clear()
        self.chunk_ids.clear()
        self.tokenized_corpus.clear()
        self._bm25 = None
        if os.path.exists(self.index_path):
            try:
                os.remove(self.index_path)
            except Exception:
                pass

    def count(self) -> int:
        """Returns total number of indexed chunks."""
        return len(self.chunk_ids)

    # ── Search & RBAC Pre-Filtering ──────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 5,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_groups: Optional[List[str]] = None,
        source: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes BM25 keyword search with strict RBAC access pre-filtering.

        RBAC Rule: A chunk is returned IF:
          (is_public == True) OR
          (allowed_roles matches user_roles) OR
          (allowed_users matches user_id) OR
          (allowed_groups matches user_groups)
        """
        if not self._bm25 or not query or not query.strip():
            return []

        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        query_tokens_set = set(query_tokens)
        scores = self._bm25.get_scores(query_tokens)

        # Filter by RBAC and metadata
        candidates = []
        user_roles_set = set(user_roles or [])
        user_groups_set = set(user_groups or [])

        for idx, score in enumerate(scores):
            doc_tokens = set(self.tokenized_corpus[idx])
            matching_tokens = query_tokens_set.intersection(doc_tokens)
            if not matching_tokens:
                continue

            c_id = self.chunk_ids[idx]
            payload = self.chunks_map[c_id]

            # 1. Metadata Filters
            if source and payload.get("source", "").lower() != source.lower():
                continue
            if resource_type and payload.get("resource_type", "").lower() != resource_type.lower():
                continue

            # 2. RBAC Access Control Filter
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
            item["score"] = float(score)
            item["matching_tokens"] = list(matching_tokens)
            item["chunk_id"] = c_id
            candidates.append(item)

        # Sort candidates descending by BM25 score
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k]

    # ── Disk Persistence ─────────────────────────────────────────────────────

    def save_to_disk(self, filepath: Optional[str] = None) -> None:
        """Serializes indexed chunks and corpus to a local JSON file."""
        target_path = filepath or self.index_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)

        data = {
            "chunk_ids": self.chunk_ids,
            "chunks_map": self.chunks_map,
            "tokenized_corpus": self.tokenized_corpus,
        }

        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_from_disk(self, filepath: Optional[str] = None) -> bool:
        """Loads indexed chunks from disk and initializes BM25Plus."""
        target_path = filepath or self.index_path
        if not os.path.exists(target_path):
            return False

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.chunk_ids = data.get("chunk_ids", [])
            self.chunks_map = data.get("chunks_map", {})
            self.tokenized_corpus = data.get("tokenized_corpus", [])

            if self.tokenized_corpus:
                self._bm25 = BM25Plus(self.tokenized_corpus)
            return True
        except Exception:
            return False
