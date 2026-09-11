"""
Master Ingestion Pipeline for Enterprise Knowledge Agent.

Orchestrates the complete knowledge ingestion workflow:
  Raw Document / OKFConcept
             ↓
     SmartOKFChunker       (structure-preserving, procedure-aware, breadcrumbs)
             ↓
       LocalEmbedder       (sentence-transformers bge-base 768-dim)
             ↓
     QdrantVectorStore     (RBAC payload indexing & upsert)

Pipeline entrypoint:
    from backend.ingestion.pipeline import IngestionPipeline
    pipeline = IngestionPipeline()
    result = pipeline.ingest_concept(concept)
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from backend.ingestion.chunk import SmartChunk
from backend.ingestion.chunker import SmartOKFChunker
from backend.ingestion.embedder import LocalEmbedder
from backend.models.document import Document
from backend.models.okf import OKFBundle, OKFConcept
from backend.storage.qdrant_client import QdrantVectorStore


class IngestionPipeline:
    """
    End-to-end ingestion orchestrator that chunks, embeds, and indexes
    knowledge documents into local vector and keyword storage.
    """

    def __init__(
        self,
        chunker: Optional[SmartOKFChunker] = None,
        embedder: Optional[LocalEmbedder] = None,
        vector_store: Optional[QdrantVectorStore] = None,
    ) -> None:
        self.chunker = chunker or SmartOKFChunker()
        self.embedder = embedder or LocalEmbedder()
        self.vector_store = vector_store or QdrantVectorStore()

    def ingest_concept(self, concept: OKFConcept) -> Dict[str, Any]:
        """
        Ingests a single OKFConcept:
          1. Splits concept body into linked SmartChunks.
          2. Generates local dense vector embeddings.
          3. Upserts points with full security payloads into Qdrant.
        """
        t0 = time.time()

        # Step 1: Structure-aware chunking
        chunks: List[SmartChunk] = self.chunker.chunk_okf_concept(concept)
        if not chunks:
            return {
                "resource_id": concept.resource or concept.title,
                "chunks_count": 0,
                "points_upserted": 0,
                "elapsed_seconds": round(time.time() - t0, 3),
                "status": "skipped_empty",
            }

        # Step 2: Local Vector Embeddings
        chunk_vector_pairs = self.embedder.embed_chunks(chunks)

        # Step 3: Vector Store Upsert
        points_upserted = self.vector_store.upsert_chunks(chunk_vector_pairs)

        elapsed = round(time.time() - t0, 3)
        return {
            "resource_id": concept.resource or concept.title,
            "title": concept.title,
            "chunks_count": len(chunks),
            "points_upserted": points_upserted,
            "elapsed_seconds": elapsed,
            "status": "success",
            "chunks": chunks,
        }

    def ingest_document(self, doc: Document) -> Dict[str, Any]:
        """
        Converts an intermediate Document into an OKFConcept, then ingests it.
        """
        concept = OKFConcept.from_intermediate_document(doc)
        return self.ingest_concept(concept)

    def ingest_documents(self, docs: List[Document]) -> Dict[str, Any]:
        """
        Batch ingests a list of intermediate Documents.
        """
        t0 = time.time()
        total_chunks = 0
        total_points = 0
        results = []

        for doc in docs:
            res = self.ingest_document(doc)
            total_chunks += res.get("chunks_count", 0)
            total_points += res.get("points_upserted", 0)
            results.append(res)

        return {
            "documents_count": len(docs),
            "total_chunks": total_chunks,
            "total_points_upserted": total_points,
            "elapsed_seconds": round(time.time() - t0, 3),
            "results": results,
        }

    def ingest_bundle(self, bundle: OKFBundle) -> Dict[str, Any]:
        """
        Ingests an entire OKFBundle of concepts.
        """
        t0 = time.time()
        total_chunks = 0
        total_points = 0
        results = []

        for path, concept in bundle.concepts.items():
            res = self.ingest_concept(concept)
            total_chunks += res.get("chunks_count", 0)
            total_points += res.get("points_upserted", 0)
            results.append(res)

        return {
            "bundle_name": bundle.name,
            "concepts_count": len(bundle.concepts),
            "total_chunks": total_chunks,
            "total_points_upserted": total_points,
            "elapsed_seconds": round(time.time() - t0, 3),
            "results": results,
        }
