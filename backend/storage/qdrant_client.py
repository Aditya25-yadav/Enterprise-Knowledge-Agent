"""
Qdrant Vector Store Client for Enterprise Knowledge Agent.

Manages local in-process storage (SQLite/RocksDB) or remote Qdrant Docker server,
collection creation, payload indexing, batch vector upserts, and security-aware
RBAC pre-filtered similarity search.

Supported Modes (.env):
  QDRANT_MODE=local   -> in-process embedded storage at QDRANT_PATH (default ./data/qdrant_storage)
  QDRANT_MODE=server  -> remote Qdrant instance at QDRANT_URL (e.g. http://localhost:6333)
  QDRANT_MODE=memory  -> ephemeral in-memory storage for unit testing
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

from backend.ingestion.chunk import SmartChunk


class QdrantVectorStore:
    """
    Client for interacting with Qdrant vector database with full RBAC filtering support.
    """

    DEFAULT_COLLECTION = "enterprise_knowledge"
    DEFAULT_STORAGE_PATH = "./data/qdrant_storage"

    def __init__(
        self,
        mode: Optional[str] = None,
        path: Optional[str] = None,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        collection_name: Optional[str] = None,
        vector_size: int = 1024,
    ) -> None:
        self.mode = mode or os.getenv("QDRANT_MODE", "local").lower()
        self.path = path or os.getenv("QDRANT_PATH", self.DEFAULT_STORAGE_PATH)
        self.url = url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.api_key = api_key or os.getenv("QDRANT_API_KEY")
        self.collection_name = collection_name or os.getenv("QDRANT_COLLECTION", self.DEFAULT_COLLECTION)
        self.vector_size = vector_size

        self._client = self._init_client()
        self.ensure_collection()

    def _init_client(self) -> Any:
        from qdrant_client import QdrantClient

        if self.mode == "memory":
            return QdrantClient(location=":memory:")

        if self.mode == "server":
            return QdrantClient(url=self.url, api_key=self.api_key)

        # Default: local embedded disk storage
        os.makedirs(self.path, exist_ok=True)
        return QdrantClient(path=self.path)

    # ── Collection & Schema Management ───────────────────────────────────────

    def ensure_collection(
        self,
        collection_name: Optional[str] = None,
        vector_size: Optional[int] = None,
    ) -> None:
        """
        Creates the Qdrant collection if it does not exist, and creates payload
        indexes for fast metadata and RBAC pre-filtering.
        """
        from qdrant_client.http import models as rest

        col_name = collection_name or self.collection_name
        v_size = vector_size or self.vector_size

        collections = self._client.get_collections().collections
        exists = any(c.name == col_name for c in collections)

        if not exists:
            self._client.create_collection(
                collection_name=col_name,
                vectors_config=rest.VectorParams(
                    size=v_size,
                    distance=rest.Distance.COSINE,
                ),
            )

            # Create payload indexes on core search and RBAC fields
            index_fields = [
                ("is_public", rest.PayloadSchemaType.KEYWORD),
                ("allowed_roles", rest.PayloadSchemaType.KEYWORD),
                ("allowed_users", rest.PayloadSchemaType.KEYWORD),
                ("allowed_groups", rest.PayloadSchemaType.KEYWORD),
                ("source", rest.PayloadSchemaType.KEYWORD),
                ("resource_type", rest.PayloadSchemaType.KEYWORD),
                ("resource_id", rest.PayloadSchemaType.KEYWORD),
                ("content_type", rest.PayloadSchemaType.KEYWORD),
            ]

            for field_name, field_type in index_fields:
                try:
                    self._client.create_payload_index(
                        collection_name=col_name,
                        field_name=field_name,
                        field_schema=field_type,
                    )
                except Exception:
                    pass

    # ── Upsert Operations ────────────────────────────────────────────────────

    def upsert_chunks(
        self,
        chunk_vector_pairs: List[Tuple[SmartChunk, List[float]]],
        collection_name: Optional[str] = None,
    ) -> int:
        """
        Upserts chunks with their vectors and full metadata payloads into Qdrant.
        Uses deterministic UUIDs generated from chunk_id for idempotent re-indexing.
        """
        if not chunk_vector_pairs:
            return 0

        from qdrant_client.http import models as rest

        col_name = collection_name or self.collection_name
        points = []

        for chunk, vector in chunk_vector_pairs:
            # Deterministic UUID5 based on unique chunk_id
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id))
            payload = chunk.to_qdrant_payload()

            points.append(
                rest.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            )

        self._client.upsert(collection_name=col_name, points=points)
        return len(points)

    # ── Search & Retrieval with RBAC Pre-Filtering ───────────────────────────

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        user_roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        user_groups: Optional[List[str]] = None,
        source: Optional[str] = None,
        resource_type: Optional[str] = None,
        collection_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Performs vector similarity search with strict RBAC access pre-filtering.

        RBAC Rule: A chunk is returned IF:
          (is_public == True) OR
          (allowed_roles matches user_roles) OR
          (allowed_users matches user_id) OR
          (allowed_groups matches user_groups)
        """
        from qdrant_client.http import models as rest

        col_name = collection_name or self.collection_name
        filter_conditions: List[rest.Condition] = []

        # 1. Source platform filter (optional)
        if source:
            filter_conditions.append(
                rest.FieldCondition(
                    key="source",
                    match=rest.MatchValue(value=source.lower()),
                )
            )

        # 2. Resource type filter (optional)
        if resource_type:
            filter_conditions.append(
                rest.FieldCondition(
                    key="resource_type",
                    match=rest.MatchValue(value=resource_type.lower()),
                )
            )

        # 3. RBAC Pre-Filter
        rbac_should_clauses: List[rest.Condition] = [
            # Public content is accessible to all
            rest.FieldCondition(key="is_public", match=rest.MatchValue(value=True)),
        ]

        if user_roles:
            rbac_should_clauses.append(
                rest.FieldCondition(
                    key="allowed_roles",
                    match=rest.MatchAny(any=user_roles),
                )
            )

        if user_id:
            rbac_should_clauses.append(
                rest.FieldCondition(
                    key="allowed_users",
                    match=rest.MatchValue(value=user_id),
                )
            )

        if user_groups:
            rbac_should_clauses.append(
                rest.FieldCondition(
                    key="allowed_groups",
                    match=rest.MatchAny(any=user_groups),
                )
            )

        # Add RBAC should filter (at least one must match)
        filter_conditions.append(rest.Filter(should=rbac_should_clauses))

        search_filter = rest.Filter(must=filter_conditions)

        # Execute vector search
        if hasattr(self._client, "query_points"):
            response = self._client.query_points(
                collection_name=col_name,
                query=query_vector,
                query_filter=search_filter,
                limit=top_k,
            )
            points = response.points
            
        else:
            points = self._client.search(
                collection_name=col_name,
                query_vector=query_vector,
                query_filter=search_filter,
                limit=top_k,
            )

        formatted_results = []
        for r in points:
            item = dict(r.payload or {})
            item["score"] = r.score
            item["point_id"] = r.id
            formatted_results.append(item)

        return formatted_results

    # ── Helpers ──────────────────────────────────────────────────────────────

    def count_points(self, collection_name: Optional[str] = None) -> int:
        col_name = collection_name or self.collection_name
        try:
            return self._client.count(collection_name=col_name).count
        except Exception:
            return 0

    def delete_by_resource_id(
        self,
        resource_id: str,
        collection_name: Optional[str] = None,
    ) -> None:
        """Deletes all chunks associated with a specific resource ID (for incremental sync)."""
        from qdrant_client.http import models as rest
        col_name = collection_name or self.collection_name
        self._client.delete(
            collection_name=col_name,
            points_selector=rest.FilterSelector(
                filter=rest.Filter(
                    must=[
                        rest.FieldCondition(
                            key="resource_id",
                            match=rest.MatchValue(value=resource_id),
                        )
                    ]
                )
            ),
        )

    def clear_collection(self, collection_name: Optional[str] = None) -> None:
        col_name = collection_name or self.collection_name
        try:
            self._client.delete_collection(collection_name=col_name)
            self.ensure_collection(collection_name=col_name)
        except Exception:
            pass
