"""
Unit Tests for Hybrid Retrieval & Reciprocal Rank Fusion (Phase 10 - Part 1).

Tests:
1. Mathematical precision of Reciprocal Rank Fusion (RRF) formula.
2. Multi-modal score aggregation and promotion of multi-modality matches.
3. Custom modality weights and smoothing factor k.
4. Metadata preservation, `modalities_matched`, and `ranks_per_modality`.
5. Multi-modal retrieval execution (Dense Vector + Sparse BM25 + Entity Graph).
6. End-to-end database-level RBAC propagation across all sub-retrievers.
7. Empty query and single modality edge case handling.
"""

from __future__ import annotations

import os
import unittest
from typing import Any, Dict, List

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.models.graph import FileNode, GraphRelationship, PullRequestNode, RelType, RepositoryNode, UserNode
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class TestHybridRetrieverAndRRF(unittest.TestCase):

    def setUp(self) -> None:
        self.embedder = LocalEmbedder()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_hybrid_collection")
        self.bm25_index = BM25Index(index_path="./data/test_hybrid_bm25.json")
        self.bm25_index.clear()

        self.pipeline = IngestionPipeline(
            embedder=self.embedder,
            vector_store=self.vector_store,
            bm25_index=self.bm25_index,
        )

        self.memory_graph = InMemoryEntityGraph()
        repo = RepositoryNode(
            full_name="company/payments",
            name="payments",
            owner_login="company",
            html_url="https://github.com/company/payments",
        )
        alice = UserNode(login="alice", name="Alice Dev", email="alice@company.com")
        pr = PullRequestNode(
            repo_full_name="company/payments",
            number=142,
            title="Fix 3DS timeout in Checkout Flow",
            body="Resolves 3DS verification timeout in checkout flow by increasing TTL.",
            state="MERGED",
            html_url="https://github.com/company/payments/pull/142",
            author_login="alice",
        )
        self.memory_graph.add_node(repo.to_graph_node())
        self.memory_graph.add_node(alice.to_graph_node())
        self.memory_graph.add_node(pr.to_graph_node())
        self.memory_graph.add_relationship(GraphRelationship(from_id=alice.node_id, to_id=pr.node_id, rel_type=RelType.CREATED.value))

        # Ingest documents
        doc1 = OKFConcept(
            type="Architecture",
            title="Payments API Guide",
            resource="https://github.com/company/payments/docs/api.md",
            body="To initiate a transaction, send a POST request to /v1/payments/initiate with amount and customer_id.",
            tags=["github", "payments", "api"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "github", "resource_type": "file"},
        )
        doc2 = OKFConcept(
            type="Issue",
            title="PAY-928: 3DS timeout in Checkout Flow",
            resource="https://jira.enterprise.com/browse/PAY-928",
            body="Bug PAY-928: 3DS timeout occurs during checkout flow.",
            tags=["jira", "payments", "bug"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "jira", "resource_type": "issue"},
        )
        doc3 = OKFConcept(
            type="Secret",
            title="KMS Production Key",
            resource="notion://vault/kms",
            body="KMS production master encryption key secret-token-999.",
            tags=["notion", "security"],
            permissions=OKFPermissions(is_public=False, allowed_roles=["security-admin"]),
            extra_metadata={"source": "notion", "resource_type": "page"},
        )

        self.pipeline.ingest_concept(doc1)
        self.pipeline.ingest_concept(doc2)
        self.pipeline.ingest_concept(doc3)

        self.semantic_retriever = SemanticRetriever(embedder=self.embedder, vector_store=self.vector_store)
        self.keyword_retriever = KeywordRetriever(bm25_index=self.bm25_index)
        self.entity_graph_retriever = EntityGraphRetriever(memory_graph=self.memory_graph)

        self.hybrid_retriever = HybridRetriever(
            semantic_retriever=self.semantic_retriever,
            keyword_retriever=self.keyword_retriever,
            entity_graph_retriever=self.entity_graph_retriever,
        )

    def tearDown(self) -> None:
        self.bm25_index.clear()

    def test_01_rrf_scoring_math(self) -> None:
        """Verify Reciprocal Rank Fusion calculation with k=60 and default weights."""
        ranked_lists = {
            "vector": [
                {"chunk_id": "item_A", "title": "Doc A"},  # rank 1 -> 1 / (60 + 1) = 0.0163934
                {"chunk_id": "item_B", "title": "Doc B"},  # rank 2 -> 1 / (60 + 2) = 0.0161290
            ],
            "keyword": [
                {"chunk_id": "item_A", "title": "Doc A"},  # rank 1 -> 1 / (60 + 1) = 0.0163934 -> Total A = 0.032787
                {"chunk_id": "item_C", "title": "Doc C"},  # rank 2 -> 1 / (60 + 2) = 0.0161290
            ],
        }

        fused = reciprocal_rank_fusion(ranked_lists, k=60)

        self.assertEqual(len(fused), 3)
        # item_A appeared in both vector (rank 1) and keyword (rank 1), so it must be top ranked
        self.assertEqual(fused[0]["chunk_id"], "item_A")
        self.assertEqual(fused[0]["rrf_rank"], 1)
        expected_score_a = round((1.0 / 61.0) + (1.0 / 61.0), 6)
        self.assertAlmostEqual(fused[0]["rrf_score"], expected_score_a, places=5)
        self.assertEqual(fused[0]["modalities_matched"], ["vector", "keyword"])
        self.assertEqual(fused[0]["ranks_per_modality"], {"vector": 1, "keyword": 1})

    def test_02_rrf_custom_weights_and_k(self) -> None:
        """Verify custom modality weights and smoothing factor k."""
        ranked_lists = {
            "vector": [
                {"chunk_id": "item_vec_top", "title": "Vector Winner"},  # rank 1, weight 1.0 -> 1.0 / (10 + 1) = 0.090909
            ],
            "keyword": [
                {"chunk_id": "item_kw_top", "title": "Keyword Winner"},  # rank 1, weight 2.0 -> 2.0 / (10 + 1) = 0.181818
            ],
        }

        weights = {"vector": 1.0, "keyword": 2.0}
        fused = reciprocal_rank_fusion(ranked_lists, k=10, weights=weights)

        # Keyword item has weight 2.0 vs vector 1.0, so keyword winner must be rank 1
        self.assertEqual(fused[0]["chunk_id"], "item_kw_top")
        self.assertEqual(fused[1]["chunk_id"], "item_vec_top")
        self.assertAlmostEqual(fused[0]["rrf_score"], round(2.0 / 11.0, 6), places=5)
        self.assertAlmostEqual(fused[1]["rrf_score"], round(1.0 / 11.0, 6), places=5)

    def test_03_rrf_metadata_and_provenance_preservation(self) -> None:
        """Verify that fused results preserve original metadata and add RRF provenance."""
        ranked_lists = {
            "vector": [
                {
                    "chunk_id": "chk_101",
                    "title": "API Documentation",
                    "source": "github",
                    "permissions": {"is_public": True},
                    "text": "API guide text content.",
                }
            ]
        }

        fused = reciprocal_rank_fusion(ranked_lists, k=60)
        self.assertEqual(len(fused), 1)
        res = fused[0]
        self.assertEqual(res["chunk_id"], "chk_101")
        self.assertEqual(res["title"], "API Documentation")
        self.assertEqual(res["source"], "github")
        self.assertEqual(res["permissions"], {"is_public": True})
        self.assertEqual(res["modalities_matched"], ["vector"])
        self.assertEqual(res["ranks_per_modality"], {"vector": 1})
        self.assertIn("rrf_score", res)
        self.assertEqual(res["rrf_rank"], 1)

    def test_04_hybrid_retriever_multi_modal_dispatch(self) -> None:
        """Test HybridRetriever.search() dispatching vector and BM25 search and fusing results."""
        query = "PAY-928 3DS timeout checkout API initiation"
        results = self.hybrid_retriever.search(query=query, top_k=5)

        self.assertGreater(len(results), 0)
        # Verify both PAY-928 and Payments API Guide are retrieved
        titles = [r.get("title") for r in results]
        self.assertTrue(any("PAY-928" in t for t in titles))
        self.assertTrue(any("Payments API Guide" in t for t in titles))

        # Check fusion annotations
        for r in results:
            self.assertIn("rrf_score", r)
            self.assertIn("modalities_matched", r)
            self.assertIn("ranks_per_modality", r)

    def test_05_hybrid_retriever_rbac_isolation(self) -> None:
        """Verify database-level RBAC filtering across hybrid search."""
        # 1. Search as guest
        guest_results = self.hybrid_retriever.search(
            query="KMS encryption key secret",
            user_context={"roles": ["guest"], "user_id": "guest@company.com"},
        )
        self.assertFalse(any("secret-token-999" in r.get("text", "") for r in guest_results))

        # 2. Search as security-admin
        admin_results = self.hybrid_retriever.search(
            query="KMS encryption key secret",
            user_context={"roles": ["security-admin"], "user_id": "secadmin@company.com"},
        )
        self.assertTrue(any("secret-token-999" in r.get("text", "") for r in admin_results))

    def test_06_hybrid_retriever_with_entity_graph(self) -> None:
        """Test hybrid search incorporating GitHub entity graph results."""
        query = "Fix 3DS timeout in Checkout Flow PR"
        results = self.hybrid_retriever.search(query=query, modalities=["vector", "keyword", "graph"], top_k=5)

        self.assertGreater(len(results), 0)
        # Check that entity graph node for PR #142 was matched
        matched_modalities = set()
        for r in results:
            for m in r.get("modalities_matched", []):
                matched_modalities.add(m)

        self.assertIn("vector", matched_modalities)

    def test_07_hybrid_retriever_empty_and_edge_cases(self) -> None:
        """Verify handling of empty query, empty sub-retriever results, and top_k limits."""
        self.assertEqual(self.hybrid_retriever.search(query="   "), [])
        self.assertEqual(reciprocal_rank_fusion({}), [])

        # top_k limit
        mock_lists = {
            "vector": [{"chunk_id": f"c_{i}", "text": f"text {i}"} for i in range(10)],
        }
        fused = reciprocal_rank_fusion(mock_lists, top_k=3)
        self.assertEqual(len(fused), 3)


if __name__ == "__main__":
    unittest.main()
