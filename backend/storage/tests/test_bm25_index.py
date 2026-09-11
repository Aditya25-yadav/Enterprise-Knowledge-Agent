"""
Test Suite for BM25 Keyword Search Index.

Validates:
  1. Identifier & symbol tokenization (PR #, Jira keys, snake_case, camelCase).
  2. Exact keyword matching accuracy.
  3. RBAC Pre-Filtering on lexical search.
  4. Disk serialization and persistence reloading.
  5. IngestionPipeline dual vector + keyword indexing.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from backend.ingestion.chunk import ContentType, SmartChunk, SequenceInfo
from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.models.okf import OKFConcept, OKFPermissions
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class TestBM25Index(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.index_file = os.path.join(self.tmp_dir.name, "test_bm25.json")
        self.bm25 = BM25Index(index_path=self.index_file)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_01_tokenization_preserves_identifiers(self) -> None:
        tokens = self.bm25.tokenize("Jira PAY-928: Fix validate_token() in AuthService for PR #1842 with HTTP 401")
        self.assertIn("pay-928", tokens)
        self.assertIn("pay", tokens)
        self.assertIn("928", tokens)
        self.assertIn("validate_token", tokens)
        self.assertIn("authservice", tokens)
        self.assertIn("#1842", tokens)
        self.assertIn("401", tokens)

    def test_02_exact_identifier_search(self) -> None:
        chunk1 = SmartChunk(
            chunk_id="jira:ticket:PAY-928#c0",
            resource_id="jira:ticket:PAY-928",
            source="jira",
            resource_type="ticket",
            title="PAY-928: Stripe Webhook Signature Failure",
            text="Customers experiencing HTTP 401 during checkout due to webhook signature mismatch in StripeHandler.",
            permissions={"is_public": True, "allowed_roles": ["employee"], "allowed_users": [], "allowed_groups": []}
        )

        chunk2 = SmartChunk(
            chunk_id="github:repo:payments#c1",
            resource_id="github:repo:payments",
            source="github",
            resource_type="file",
            title="auth_service.py",
            text="def validate_token(token: str) -> bool: Verifies RSA256 signature against Okta JWKS endpoint.",
            permissions={"is_public": True, "allowed_roles": ["employee"], "allowed_users": [], "allowed_groups": []}
        )

        self.bm25.add_chunks([chunk1, chunk2])
        self.assertEqual(self.bm25.count(), 2)

        # 1. Search for Jira Ticket Key
        res_jira = self.bm25.search("PAY-928", top_k=2)
        self.assertEqual(len(res_jira), 1)
        self.assertEqual(res_jira[0]["chunk_id"], "jira:ticket:PAY-928#c0")
        self.assertIn("Stripe Webhook", res_jira[0]["title"])

        # 2. Search for exact function name
        res_code = self.bm25.search("validate_token", top_k=2)
        self.assertEqual(len(res_code), 1)
        self.assertEqual(res_code[0]["chunk_id"], "github:repo:payments#c1")

    def test_03_rbac_filtering_on_keyword_search(self) -> None:
        # Restricted chunk
        secret_chunk = SmartChunk(
            chunk_id="vault:secrets:db#c0",
            resource_id="vault:secrets:db",
            source="vault",
            resource_type="secret",
            title="Production Database Credentials",
            text="Root password for PostgreSQL cluster is supersecretpassword123.",
            permissions={"is_public": False, "allowed_roles": ["devops", "security-lead"], "allowed_users": [], "allowed_groups": []}
        )

        # Public chunk
        public_chunk = SmartChunk(
            chunk_id="notion:docs:wiki#c0",
            resource_id="notion:docs:wiki",
            source="notion",
            resource_type="page",
            title="Engineering Onboarding Wiki",
            text="Welcome to the team! PostgreSQL client can be downloaded via brew install postgresql.",
            permissions={"is_public": True, "allowed_roles": ["employee"], "allowed_users": [], "allowed_groups": []}
        )

        self.bm25.add_chunks([secret_chunk, public_chunk])

        # Query for 'PostgreSQL' as devops -> should get both chunks
        devops_res = self.bm25.search("PostgreSQL", top_k=5, user_roles=["devops"])
        self.assertEqual(len(devops_res), 2)
        self.assertTrue(any("supersecretpassword123" in r["text"] for r in devops_res))

        # Query for 'PostgreSQL' as guest -> should ONLY get public onboarding chunk
        guest_res = self.bm25.search("PostgreSQL", top_k=5, user_roles=["guest"])
        self.assertEqual(len(guest_res), 1)
        self.assertFalse(any("supersecretpassword123" in r["text"] for r in guest_res))
        self.assertIn("Engineering Onboarding", guest_res[0]["title"])

    def test_04_disk_serialization_and_reload(self) -> None:
        chunk = SmartChunk(
            chunk_id="doc:persistent#c0",
            resource_id="doc:persistent",
            source="notion",
            resource_type="page",
            title="Persistent Doc",
            text="Testing disk persistence across restarts with error code ERR-7789.",
            permissions={"is_public": True, "allowed_roles": ["employee"], "allowed_users": [], "allowed_groups": []}
        )

        self.bm25.add_chunks([chunk])
        self.bm25.save_to_disk()
        self.assertTrue(os.path.exists(self.index_file))

        # Reload into a fresh BM25Index instance
        reloaded_bm25 = BM25Index(index_path=self.index_file)
        self.assertEqual(reloaded_bm25.count(), 1)

        res = reloaded_bm25.search("ERR-7789")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["chunk_id"], "doc:persistent#c0")

    def test_05_pipeline_dual_indexing(self) -> None:
        concept = OKFConcept(
            type="Playbook",
            title="Incident Response Playbook",
            resource="github://repo/incident.md",
            body="# Incident Triage\nFor P0 outages involving HTTP 503, page the on-call engineer immediately.",
            permissions=OKFPermissions(is_public=True)
        )

        embedder = LocalEmbedder()
        vector_store = QdrantVectorStore(mode="memory", collection_name="test_pipeline_dual")
        pipeline = IngestionPipeline(embedder=embedder, vector_store=vector_store, bm25_index=self.bm25)

        res = pipeline.ingest_concept(concept)
        self.assertEqual(res["status"], "success")
        self.assertGreater(res["points_upserted"], 0)
        self.assertGreater(res["bm25_indexed"], 0)

        # Verify keyword search finds the incident playbook
        kw_results = self.bm25.search("P0 outages HTTP 503")
        self.assertGreater(len(kw_results), 0)
        self.assertIn("Incident Response Playbook", kw_results[0]["title"])


if __name__ == "__main__":
    unittest.main()
