"""
End-to-End Pipeline & Storage Test Suite.

Validates:
  1. LocalEmbedder vector dimension and embedding generation.
  2. IngestionPipeline complete run (OKFConcept -> Chunks -> Embeddings -> Qdrant).
  3. QdrantVectorStore similarity search.
  4. RBAC Pre-filtering enforcement (engineer vs guest access).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import unittest
from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.models.document import Document, DocumentMetadata, ContentBlock, BlockType
from backend.models.okf import OKFConcept, OKFPermissions
from backend.storage.qdrant_client import QdrantVectorStore


class TestIngestionPipeline(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.embedder = LocalEmbedder()
        cls.vector_store = QdrantVectorStore(mode="memory", collection_name="test_enterprise_knowledge")
        cls.pipeline = IngestionPipeline(embedder=cls.embedder, vector_store=cls.vector_store)

    def test_01_local_embedder_dimension(self) -> None:
        dim = self.embedder.dimension
        self.assertEqual(dim, 1024, f"Expected 1024 dimensions for Qwen/Qwen3-Embedding-0.6B, got {dim}")
        vec = self.embedder.embed_text("Authentication with OAuth 2.0 PKCE")
        self.assertEqual(len(vec), 1024)

    def test_02_ingest_and_rbac_retrieval(self) -> None:
        # Document 1: Internal Engineering Secret (Restricted to 'engineer' role)
        secret_doc = Document(
            metadata=DocumentMetadata(
                id="doc_internal_auth",
                title="Production Database Secrets",
                source_platform="notion",
                url="https://notion.so/prod-secrets",
            ),
            blocks=[
                ContentBlock(id="b1", type=BlockType.HEADING, text="Production Database Setup", properties={"level": 1}),
                ContentBlock(id="b2", type=BlockType.PARAGRAPH, text="The master database password for the payments cluster is stored in Vault at secret/payments/db."),
            ]
        )
        secret_concept = OKFConcept.from_intermediate_document(
            doc=secret_doc,
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["engineer"],
                allowed_groups=["security"],
            )
        )

        # Document 2: Public Company Handbook (Accessible to all)
        public_doc = Document(
            metadata=DocumentMetadata(
                id="doc_public_handbook",
                title="Company Vacation Policy",
                source_platform="notion",
                url="https://notion.so/vacation",
            ),
            blocks=[
                ContentBlock(id="b3", type=BlockType.HEADING, text="Vacation Policy", properties={"level": 1}),
                ContentBlock(id="b4", type=BlockType.PARAGRAPH, text="All full-time employees receive 25 days of paid annual vacation."),
            ]
        )
        public_concept = OKFConcept.from_intermediate_document(
            doc=public_doc,
            permissions=OKFPermissions(is_public=True)
        )

        # Ingest both concepts
        res1 = self.pipeline.ingest_concept(secret_concept)
        res2 = self.pipeline.ingest_concept(public_concept)

        self.assertEqual(res1["status"], "success")
        self.assertEqual(res2["status"], "success")
        self.assertGreater(self.vector_store.count_points(), 0)

        # Query 1: Search for database password as an ENGINEER
        q_vec = self.embedder.embed_text("Where is the payments database password stored?")
        engineer_results = self.vector_store.search(
            query_vector=q_vec,
            top_k=5,
            user_roles=["engineer"],
        )

        # Engineer should find the secret document
        found_secret = any("payments cluster" in r.get("text", "") for r in engineer_results)
        self.assertTrue(found_secret, "Engineer should retrieve internal database secret document")

        # Query 2: Search for database password as a GUEST / CONTRACTOR (no 'engineer' role)
        guest_results = self.vector_store.search(
            query_vector=q_vec,
            top_k=5,
            user_roles=["guest"],
        )

        # Guest should NOT see the secret document at all
        guest_saw_secret = any("payments cluster" in r.get("text", "") for r in guest_results)
        self.assertFalse(guest_saw_secret, "RBAC VIOLATION: Guest must NOT retrieve engineer-restricted secret")

        # Query 3: Search for vacation policy as a GUEST
        vac_vec = self.embedder.embed_text("How many vacation days do employees get?")
        vac_results = self.vector_store.search(
            query_vector=vac_vec,
            top_k=5,
            user_roles=["guest"],
        )
        found_vacation = any("25 days of paid annual vacation" in r.get("text", "") for r in vac_results)
        self.assertTrue(found_vacation, "Public handbook should be retrievable by guest")


if __name__ == "__main__":
    unittest.main()
