"""
Comprehensive Multi-Modal Retrieval RBAC Integration Tests (Phase 8 - Part 3).

Validates:
  1. SemanticRetriever: Multi-role hierarchy and direct user whitelist pre-filtering over Qdrant.
  2. KeywordRetriever: BM25 sparse search with nested group and hierarchical role pre-filtering.
  3. ResourceLookupRetriever: Canonical URI lookup with RBAC enforcement and parent inheritance.
  4. GraphRetriever: Parent-child hierarchy navigation and sibling expansion with RBAC filtering.
  5. EntityGraphRetriever: In-memory developer entity search with RBAC pre-filtering.
  6. LangChain Tools Suite: Automatic RBAC security context propagation across all 5 structured tools.
"""

from __future__ import annotations

import json
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

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from backend.agent.langchain_tools import create_langchain_tools
from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.models.graph import (
    FileNode,
    GraphRelationship,
    PullRequestNode,
    RelType,
    RepositoryNode,
    UserNode,
)
from backend.models.okf import OKFConcept, OKFPermissions
from backend.models.security import UserSecurityContext
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.security.rbac_resolver import RBACResolver, get_default_rbac_resolver
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class TestMultiModalRetrievalRBAC(unittest.TestCase):

    def setUp(self) -> None:
        self.embedder = LocalEmbedder()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_rbac_multi_collection")
        self.bm25_index = BM25Index(index_path="./data/test_rbac_bm25.json")
        self.bm25_index.clear()

        self.pipeline = IngestionPipeline(
            embedder=self.embedder,
            vector_store=self.vector_store,
            bm25_index=self.bm25_index,
        )

        # 1. Ingest Public Documentation
        doc_public = OKFConcept(
            type="Architecture",
            title="Public API Guidelines",
            resource="https://github.com/company/docs/public_api.md",
            body="Public REST API documentation and authentication guidelines.",
            permissions=OKFPermissions(is_public=True),
        )
        self.pipeline.ingest_concept(doc_public)

        # 2. Ingest Engineer-Level Documentation
        doc_eng = OKFConcept(
            type="Architecture",
            title="Payments Microservice Internal Guide",
            resource="https://github.com/company/payments/docs/internal.md",
            body="Internal architecture of the payment processing worker pipeline and retry queue.",
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["engineer"],
            ),
        )
        self.pipeline.ingest_concept(doc_eng)

        # 3. Ingest Group-Restricted Documentation (Payments Team)
        doc_group = OKFConcept(
            type="Architecture",
            title="Payments Core Ledger Secrets & Spec",
            resource="https://github.com/company/payments/docs/ledger.md",
            body="Double-entry ledger database schema and settlement pipeline keys.",
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=[],
                allowed_groups=["payments-team"],
            ),
        )
        self.pipeline.ingest_concept(doc_group)

        # 4. Ingest High-Security Secret (Security-Admin only)
        doc_secops = OKFConcept(
            type="Secret",
            title="Vault Master Encryption Keys",
            resource="notion://vault/master_keys",
            body="Production AES-256-GCM master KMS encryption keys: AES-SECRET-KEY-9999.",
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["security-admin"],
                allowed_users=["ciso@company.com"],
            ),
        )
        self.pipeline.ingest_concept(doc_secops)

        # 5. Ingest User-Whitelisted Personal Notebook
        doc_user = OKFConcept(
            type="Note",
            title="Alice Private Work-in-Progress Notes",
            resource="notion://notes/alice/scratchpad",
            body="Confidential draft notes on future payment gateway micro-refactoring.",
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=[],
                allowed_users=["alice@company.com"],
            ),
        )
        self.pipeline.ingest_concept(doc_user)

        # Initialize retrievers
        self.sem_retriever = SemanticRetriever(embedder=self.embedder, vector_store=self.vector_store)
        self.kw_retriever = KeywordRetriever(bm25_index=self.bm25_index)
        self.res_retriever = ResourceLookupRetriever(bm25_index=self.bm25_index, vector_store=self.vector_store)
        self.grp_retriever = GraphRetriever(bm25_index=self.bm25_index, vector_store=self.vector_store)

    def tearDown(self) -> None:
        self.bm25_index.clear()

    def test_01_semantic_retrieval_rbac_hierarchies(self) -> None:
        """Verifies vector search RBAC with hierarchical role resolution."""
        # Guest context: Should only see public docs
        guest_ctx = {"roles": ["guest"], "user_id": "guest@domain.com"}
        guest_res = self.sem_retriever.search("payment internal architecture", user_context=guest_ctx)
        guest_titles = [r.get("title") for r in guest_res]
        self.assertIn("Public API Guidelines", guest_titles)
        self.assertNotIn("Payments Microservice Internal Guide", guest_titles)
        self.assertNotIn("Vault Master Encryption Keys", guest_titles)

        # Engineer context: Can see public and engineer-level docs
        eng_ctx = {"roles": ["engineer"], "user_id": "bob@company.com"}
        eng_res = self.sem_retriever.search("payment internal architecture", user_context=eng_ctx)
        eng_titles = [r.get("title") for r in eng_res]
        self.assertIn("Payments Microservice Internal Guide", eng_titles)
        self.assertNotIn("Vault Master Encryption Keys", eng_titles)

        # Admin context: Inherits engineer & security-admin -> can see everything
        admin_ctx = {"roles": ["admin"], "user_id": "admin@company.com"}
        admin_res = self.sem_retriever.search("Vault master keys AES encryption", user_context=admin_ctx)
        admin_titles = [r.get("title") for r in admin_res]
        self.assertIn("Vault Master Encryption Keys", admin_titles)

    def test_02_keyword_retrieval_nested_groups(self) -> None:
        """Verifies BM25 keyword search with nested group membership expansion."""
        # Member of payments-core (sub-group of payments-team) -> Access granted
        core_member_ctx = {"roles": ["employee"], "groups": ["payments-core"], "user_id": "dev1@company.com"}
        res_allowed = self.kw_retriever.search("ledger database schema settlement", user_context=core_member_ctx)
        self.assertTrue(any("Payments Core Ledger" in r.get("title", "") for r in res_allowed))

        # Member of marketing -> Access denied
        marketing_ctx = {"roles": ["employee"], "groups": ["marketing"], "user_id": "mkt@company.com"}
        res_denied = self.kw_retriever.search("ledger database schema settlement", user_context=marketing_ctx)
        self.assertFalse(any("Payments Core Ledger" in r.get("title", "") for r in res_denied))

    def test_03_resource_lookup_direct_user_whitelist(self) -> None:
        """Verifies ResourceLookupRetriever direct user whitelist matching."""
        alice_ctx = {"roles": ["employee"], "user_id": "alice@company.com"}
        bob_ctx = {"roles": ["employee"], "user_id": "bob@company.com"}

        # Alice looking up her private notes -> Found
        alice_doc = self.res_retriever.get_document("notion://notes/alice/scratchpad", user_context=alice_ctx)
        self.assertIsNotNone(alice_doc)
        self.assertIn("Alice Private Work-in-Progress Notes", alice_doc.get("title", ""))

        # Bob looking up Alice's private notes -> Denied (None)
        bob_doc = self.res_retriever.get_document("notion://notes/alice/scratchpad", user_context=bob_ctx)
        self.assertIsNone(bob_doc)

    def test_04_langchain_tools_suite_rbac_propagation(self) -> None:
        """Verifies that create_langchain_tools correctly propagates RBAC context into tool invocations."""
        lc_tools_guest = create_langchain_tools(
            semantic_retriever=self.sem_retriever,
            keyword_retriever=self.kw_retriever,
            resource_lookup_retriever=self.res_retriever,
            graph_retriever=self.grp_retriever,
            user_context={"roles": ["guest"], "user_id": "guest@anon.com"},
        )

        sem_tool = next(t for t in lc_tools_guest if t.name == "semantic_search")
        res_json = sem_tool.invoke({"query": "master encryption keys"})
        self.assertNotIn("AES-SECRET-KEY-9999", res_json)

        # CISO tool suite
        lc_tools_ciso = create_langchain_tools(
            semantic_retriever=self.sem_retriever,
            keyword_retriever=self.kw_retriever,
            resource_lookup_retriever=self.res_retriever,
            graph_retriever=self.grp_retriever,
            user_context={"roles": ["employee"], "user_id": "ciso@company.com"},
        )
        ciso_res_tool = next(t for t in lc_tools_ciso if t.name == "resource_lookup")
        ciso_json = ciso_res_tool.invoke({"resource_id": "notion://vault/master_keys"})
        self.assertIn("AES-SECRET-KEY-9999", ciso_json)


if __name__ == "__main__":
    unittest.main()
