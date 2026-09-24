"""
Unit Test Suite for ResourceLookupRetriever and GraphRetriever (Phase 6).

Validates:
  1. Direct canonical resource, URL, chunk ID, and title lookups.
  2. Multi-chunk sequential document reconstruction and outline stitching.
  3. Parent-child hierarchy navigation (`get_children`).
  4. Horizontal sibling expansion (`get_neighbors`) via `prev_chunk_id` / `next_chunk_id`.
  5. Ordered procedural sequence assembly (`get_full_sequence`).
  6. Strict database-level RBAC enforcement across all graph traversals and direct lookups.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from backend.ingestion.chunker import SmartOKFChunker
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class TestGraphAndResourceRetrievers(unittest.TestCase):

    def setUp(self) -> None:
        self.bm25_index = BM25Index(index_path="./data/test_graph_retrievers_bm25.json")
        self.bm25_index.clear()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_graph_retrievers_qdrant")

        self.resource_retriever = ResourceLookupRetriever(
            bm25_index=self.bm25_index,
            vector_store=self.vector_store,
        )
        self.graph_retriever = GraphRetriever(
            bm25_index=self.bm25_index,
            vector_store=self.vector_store,
        )
        self.chunker = SmartOKFChunker()

        # Ingest 1: Parent Repository Doc
        parent_repo = OKFConcept(
            type="Repository",
            title="Payments Monorepo",
            resource="https://github.com/company/payments",
            body="Central monorepo containing checkout, billing, and gateway microservices.",
            tags=["github", "monorepo"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "github", "resource_type": "repository"},
        )
        repo_chunks = self.chunker.chunk_okf_concept(parent_repo)
        self.bm25_index.add_chunks(repo_chunks)

        # Ingest 2: Multi-Chunk Child Architecture Guide (child of Payments Monorepo)
        child_doc1 = OKFConcept(
            type="Architecture",
            title="Payments API Specification",
            resource="https://github.com/company/payments/docs/api.md",
            body="""# Payments API Specification

## Section 1: Overview
The Payments API handles credit card and 3DS payment intents.

## Section 2: Initiation
Send POST /v1/payments/initiate with amount, currency, and customer_id.

## Section 3: Webhook Verification
Listen for payment.succeeded events and verify cryptographic HMAC signature.
""",
            tags=["github", "api", "architecture"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={
                "source": "github",
                "resource_type": "file",
                "parent_id": "https://github.com/company/payments",
            },
        )
        child1_chunks = self.chunker.chunk_okf_concept(child_doc1)
        self.bm25_index.add_chunks(child1_chunks)
        self.child1_chunks = child1_chunks

        # Ingest 3: Second Child File in Repo
        child_doc2 = OKFConcept(
            type="File",
            title="Payment Gateway Core Engine",
            resource="https://github.com/company/payments/src/engine.py",
            body="""# Payment Engine Implementation
class PaymentEngine:
    def process_transaction(self, tx_id: str):
        pass
""",
            tags=["github", "code"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={
                "source": "github",
                "resource_type": "file",
                "parent_id": "https://github.com/company/payments",
            },
        )
        child2_chunks = self.chunker.chunk_okf_concept(child_doc2)
        self.bm25_index.add_chunks(child2_chunks)

        # Ingest 4: Multi-Step Disaster Recovery Runbook (Sequence & Siblings)
        runbook_doc = OKFConcept(
            type="Playbook",
            title="Disaster Recovery Runbook",
            resource="https://company.notion.site/dr-runbook",
            body="""# Disaster Recovery Runbook

### Step 1: Drain Ingress Traffic
Route incoming traffic to the secondary failover cluster immediately.

### Step 2: Restart Payment Worker
Issue `systemctl restart payment-worker` on all application nodes.

### Step 3: Verify Gateway Health
Send a health probe to `/healthz` and verify HTTP 200 response code.
""",
            tags=["notion", "runbook"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={
                "source": "notion",
                "resource_type": "playbook",
                "parent_id": "https://company.notion.site/engineering",
            },
        )
        runbook_chunks = self.chunker.chunk_okf_concept(runbook_doc)
        self.bm25_index.add_chunks(runbook_chunks)
        self.runbook_chunks = runbook_chunks

        # Ingest 5: Restricted Security Document
        secret_doc = OKFConcept(
            type="Secret",
            title="Master KMS Encryption Keys",
            resource="notion://vault/master",
            body="""# Vault Master Key
Master encryption key: AES-256-GCM-SECRET-9999.
Rotation cycle: Every 90 days.
""",
            tags=["notion", "security"],
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["security-admin"],
                allowed_users=["ciso@company.com"],
            ),
            extra_metadata={
                "source": "notion",
                "resource_type": "page",
                "parent_id": "https://company.notion.site/engineering",
            },
        )
        secret_chunks = self.chunker.chunk_okf_concept(secret_doc)
        self.bm25_index.add_chunks(secret_chunks)

    def tearDown(self) -> None:
        self.bm25_index.clear()

    # ── ResourceLookupRetriever Tests ─────────────────────────────────────────

    def test_01_lookup_by_resource_id_and_url(self) -> None:
        """Verifies direct lookup by canonical resource URI."""
        results = self.resource_retriever.lookup(
            resource_id="https://github.com/company/payments/docs/api.md",
            user_context={"roles": ["engineer"]},
        )
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertEqual(r["resource_id"], "https://github.com/company/payments/docs/api.md")
            self.assertEqual(r["title"], "Payments API Specification")

    def test_02_lookup_by_chunk_id(self) -> None:
        """Verifies exact lookup by specific chunk_id."""
        target_chunk_id = self.child1_chunks[0].chunk_id
        results = self.resource_retriever.lookup(
            resource_id=target_chunk_id,
            user_context={"roles": ["engineer"]},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], target_chunk_id)

    def test_03_lookup_by_exact_title(self) -> None:
        """Verifies lookup by document title."""
        results = self.resource_retriever.lookup(
            resource_id="Disaster Recovery Runbook",
            user_context={"roles": ["engineer"]},
        )
        self.assertGreater(len(results), 0)
        self.assertTrue(any("Drain Ingress Traffic" in r["text"] for r in results))

    def test_03b_lookup_by_fuzzy_topic_and_token_overlap(self) -> None:
        """Verifies lookup by approximate/fuzzy title and token-set overlap (e.g. 'disaster recovery module')."""
        # 1. Look up disaster recovery module -> should match 'Disaster Recovery Runbook'
        results = self.resource_retriever.lookup(
            resource_id="disaster recovery module",
            user_context={"roles": ["engineer"]},
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["title"], "Disaster Recovery Runbook")
        self.assertTrue(any("Drain Ingress Traffic" in r["text"] for r in results))

        # 2. Look up payments api -> should match 'Payments API Specification'
        api_results = self.resource_retriever.lookup(
            resource_id="payments api",
            user_context={"roles": ["engineer"]},
        )
        self.assertGreater(len(api_results), 0)
        self.assertEqual(api_results[0]["title"], "Payments API Specification")

    def test_03c_lookup_by_url_slug_and_filename(self) -> None:
        """Verifies lookup by URL path slug or file name (e.g. 'api.md' or 'dr-runbook')."""
        results = self.resource_retriever.lookup(
            resource_id="dr-runbook",
            user_context={"roles": ["engineer"]},
        )
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["title"], "Disaster Recovery Runbook")

    def test_04_get_document_sequential_stitching(self) -> None:
        """Verifies multi-chunk document assembly, completeness, and sequential ordering."""
        doc = self.resource_retriever.get_document(
            resource_id="https://github.com/company/payments/docs/api.md",
            user_context={"roles": ["engineer"]},
        )
        self.assertIsNotNone(doc)
        self.assertEqual(doc["title"], "Payments API Specification")
        self.assertEqual(doc["resource_id"], "https://github.com/company/payments/docs/api.md")
        self.assertTrue(doc["is_complete"])
        self.assertGreaterEqual(doc["total_chunks"], 1)

        # Verify stitched content contains sections in reading order
        full_text = doc["stitched_text"]
        self.assertIn("Section 1: Overview", full_text)
        self.assertIn("Section 2: Initiation", full_text)
        self.assertIn("Section 3: Webhook Verification", full_text)

        idx1 = full_text.find("Section 1")
        idx2 = full_text.find("Section 2")
        idx3 = full_text.find("Section 3")
        self.assertTrue(idx1 < idx2 < idx3, "Chunks must be stitched in sequential reading order")

    def test_05_resource_lookup_rbac(self) -> None:
        """Verifies that unauthorized users are denied access during direct resource lookups."""
        # Unauthorized guest
        guest_doc = self.resource_retriever.get_document(
            resource_id="notion://vault/master",
            user_context={"roles": ["guest"], "user_id": "guest@company.com"},
        )
        self.assertIsNone(guest_doc)

        guest_chunks = self.resource_retriever.lookup(
            resource_id="notion://vault/master",
            user_context={"roles": ["guest"], "user_id": "guest@company.com"},
        )
        self.assertEqual(len(guest_chunks), 0)

        # Authorized security-admin
        admin_doc = self.resource_retriever.get_document(
            resource_id="notion://vault/master",
            user_context={"roles": ["security-admin"], "user_id": "admin@company.com"},
        )
        self.assertIsNotNone(admin_doc)
        self.assertIn("AES-256-GCM-SECRET-9999", admin_doc["stitched_text"])

    # ── GraphRetriever Tests ──────────────────────────────────────────────────

    def test_06_get_children_hierarchy(self) -> None:
        """Verifies parent-child navigation to retrieve all child files under a repository."""
        children = self.graph_retriever.get_children(
            parent_id="https://github.com/company/payments",
            user_context={"roles": ["engineer"]},
        )
        self.assertGreaterEqual(len(children), 2)
        child_resources = {c["resource_id"] for c in children}
        self.assertIn("https://github.com/company/payments/docs/api.md", child_resources)
        self.assertIn("https://github.com/company/payments/src/engine.py", child_resources)

    def test_07_get_neighbors_sibling_expansion(self) -> None:
        """Verifies horizontal expansion using bidirectional prev/next chunk links."""
        # Find the middle chunk in runbook (Step 2)
        step2_chunk = next(
            (c for c in self.runbook_chunks if "Step 2" in c.text),
            None,
        )
        self.assertIsNotNone(step2_chunk)

        neighbors = self.graph_retriever.get_neighbors(
            chunk_id=step2_chunk.chunk_id,
            window_before=1,
            window_after=1,
            user_context={"roles": ["engineer"]},
        )

        # Should retrieve Step 1 (preceding), Step 2 (target), and Step 3 (succeeding)
        neighbor_texts = [n["text"] for n in neighbors]
        self.assertTrue(any("Step 1" in t for t in neighbor_texts), "Expected preceding Step 1")
        self.assertTrue(any("Step 2" in t for t in neighbor_texts), "Expected target Step 2")
        self.assertTrue(any("Step 3" in t for t in neighbor_texts), "Expected succeeding Step 3")

    def test_08_get_full_sequence_procedure_assembly(self) -> None:
        """Verifies assembly of ordered multi-step sequence by sequence_id."""
        first_chunk = self.runbook_chunks[0]
        seq_id = first_chunk.sequence.sequence_id if first_chunk.sequence else None

        if seq_id:
            full_seq = self.graph_retriever.get_full_sequence(
                sequence_id=seq_id,
                user_context={"roles": ["engineer"]},
            )
            self.assertEqual(len(full_seq), len(self.runbook_chunks))
            # Verify sorted step indices
            step_indices = [c.get("sequence", {}).get("step_index", 0) for c in full_seq]
            self.assertEqual(step_indices, sorted(step_indices))

    def test_09_graph_traversal_rbac_enforcement(self) -> None:
        """Verifies that unauthorized children are filtered out of parent-child graph hops."""
        # Parent engineering wiki has both public runbook and confidential secret doc
        # Run as standard engineer
        eng_children = self.graph_retriever.get_children(
            parent_id="https://company.notion.site/engineering",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )
        eng_resources = {c["resource_id"] for c in eng_children}
        self.assertIn("https://company.notion.site/dr-runbook", eng_resources)
        self.assertNotIn("notion://vault/master", eng_resources)

        # Run as security-admin
        admin_children = self.graph_retriever.get_children(
            parent_id="https://company.notion.site/engineering",
            user_context={"roles": ["security-admin"], "user_id": "admin@company.com"},
        )
        admin_resources = {c["resource_id"] for c in admin_children}
        self.assertIn("https://company.notion.site/dr-runbook", admin_resources)
        self.assertIn("notion://vault/master", admin_resources)


if __name__ == "__main__":
    unittest.main()
