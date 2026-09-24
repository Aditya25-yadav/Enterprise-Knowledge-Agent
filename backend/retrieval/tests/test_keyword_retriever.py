"""
Unit Test Suite for KeywordRetriever (Phase 5).

Validates:
  1. Exact identifier lookups (Jira keys like PAY-928, PR numbers, error codes HTTP 401, code symbols).
  2. Strict database-level Role-Based Access Control (RBAC) pre-filtering.
  3. Metadata filtering by source platform and resource type.
  4. 100% preservation of chunk payload metadata.
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
from backend.retrieval.keyword import KeywordRetriever
from backend.storage.bm25_index import BM25Index


class TestKeywordRetriever(unittest.TestCase):

    def setUp(self) -> None:
        self.bm25_index = BM25Index(index_path="./data/test_keyword_retriever_bm25.json")
        self.bm25_index.clear()
        self.retriever = KeywordRetriever(bm25_index=self.bm25_index)
        self.chunker = SmartOKFChunker()


        # Ingest 1: Public Jira Bug Report with exact key PAY-928
        doc1 = OKFConcept(
            type="Issue",
            title="PAY-928: 3DS timeout in Checkout Flow",
            resource="https://jira.enterprise.com/browse/PAY-928",
            body="""# Bug PAY-928: 3DS Timeout
Users encounter HTTP 401 and ECONNRESET during the Checkout Flow when calling AuthService.validate_token.
Steps to reproduce:
1. Initiate payment on checkout page.
2. Wait 30 seconds on 3DS challenge.
3. Observe HTTP 401 unauthorized failure.
""",
            tags=["jira", "payments", "bug"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "jira", "resource_type": "issue"},
        )
        chunks1 = self.chunker.chunk_okf_concept(doc1)
        self.bm25_index.add_chunks(chunks1)

        # Ingest 2: Confidential Security Incident Doc (Security Lead Only)
        doc2 = OKFConcept(
            type="SecurityDoc",
            title="SEC-104: Production Master Vault Access",
            resource="https://vault.enterprise.internal/keys/prod",
            body="""# Master Secret Incident SEC-104
The production master KMS encryption key was rotated. 
Only authorized security engineers may access key rotation runbooks.
""",
            tags=["security", "confidential"],
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["security-lead"],
                allowed_users=["ciso@enterprise.com"],
            ),
            extra_metadata={"source": "notion", "resource_type": "page"},
        )
        chunks2 = self.chunker.chunk_okf_concept(doc2)
        self.bm25_index.add_chunks(chunks2)

        # Ingest 3: GitHub Architecture Spec
        doc3 = OKFConcept(
            type="Architecture",
            title="Payment Gateway Architecture",
            resource="https://github.com/enterprise/payments/docs/arch.md",
            body="""# Architecture Overview
The payments microservice dispatches transactions via Kafka topic `payment.events`.
Code symbol `PaymentProcessor.process_transaction` orchestrates the settlement.
""",
            tags=["github", "architecture"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "github", "resource_type": "file"},
        )
        chunks3 = self.chunker.chunk_okf_concept(doc3)
        self.bm25_index.add_chunks(chunks3)


    def tearDown(self) -> None:
        self.bm25_index.clear()

    def test_01_exact_identifier_search(self) -> None:
        """Tests exact keyword matching for ticket keys, error codes, and code symbols."""
        # 1. Look up Jira key
        results_ticket = self.retriever.search(query="PAY-928")
        self.assertTrue(len(results_ticket) > 0)
        self.assertIn("PAY-928", results_ticket[0]["title"])

        # 2. Look up HTTP error code
        results_error = self.retriever.search(query="HTTP 401")
        self.assertTrue(len(results_error) > 0)
        self.assertIn("HTTP 401", results_error[0]["text"])

        # 3. Look up Code Symbol
        results_symbol = self.retriever.search(query="AuthService.validate_token")
        self.assertTrue(len(results_symbol) > 0)
        self.assertIn("AuthService.validate_token", results_symbol[0]["text"])

    def test_02_rbac_pre_filtering(self) -> None:
        """Verifies that unauthorized users are pre-filtered out at the database layer."""
        # Guest user searching for SEC-104 secret
        guest_context = {"roles": ["guest", "intern"], "user_id": "intern@enterprise.com"}
        guest_results = self.retriever.search(query="SEC-104 Vault", user_context=guest_context)
        self.assertEqual(len(guest_results), 0, "Guest user must not receive confidential security doc")

        # Security lead searching for SEC-104 secret
        security_context = {"roles": ["security-lead"], "user_id": "ciso@enterprise.com"}
        security_results = self.retriever.search(query="SEC-104 Vault", user_context=security_context)
        self.assertEqual(len(security_results), 1)
        self.assertIn("SEC-104", security_results[0]["title"])

    def test_03_metadata_filters(self) -> None:
        """Verifies source and resource_type metadata filtering."""
        # Search with source='jira'
        jira_results = self.retriever.search(query="Checkout Flow", source="jira")
        for r in jira_results:
            self.assertEqual(r["source"], "jira")

        # Search with resource_type='file'
        file_results = self.retriever.search(query="payments microservice", resource_type="file")
        for r in file_results:
            self.assertEqual(r["resource_type"], "file")

    def test_04_full_payload_metadata_preservation(self) -> None:
        """Verifies that 100% of chunk fields are preserved in search results."""
        results = self.retriever.search(query="PaymentProcessor.process_transaction")
        self.assertTrue(len(results) > 0)
        top = results[0]

        expected_keys = [
            "chunk_id",
            "resource_id",
            "title",
            "url",
            "source",
            "content_type",
            "section_path",
            "text",
            "score",
            "extra_metadata",
        ]
        for key in expected_keys:
            self.assertIn(key, top, f"Expected key '{key}' in keyword search result")
        self.assertIsInstance(top["score"], float)
        self.assertGreater(top["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
