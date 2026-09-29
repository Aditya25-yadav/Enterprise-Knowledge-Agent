"""
Unit Tests for High-Yield Context Utilization Strategies (Powered by TOON).
"""

import unittest
from typing import Any, Dict, List

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.evaluation.evaluator import EvidenceEvaluator
from backend.generation.answer_generator import AnswerGenerator
from backend.ingestion.catalog_aggregator import CatalogEntry, GlobalCatalogManager
from backend.models.graph import GraphNode, GraphRelationship, NodeLabel, RelType
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.entity_graph import InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


from backend.llm.base import LLMProvider, LLMResponse, Message, ToolCall


class MockContextUtilLLM(LLMProvider):
    @property
    def provider_name(self) -> str:
        return "mock"

    def generate(self, messages: List[Message], **kwargs: Any) -> str:
        return "Mock direct answer"

    def generate_with_tools(self, messages: List[Message], tools: List[Any], **kwargs: Any) -> LLMResponse:
        return LLMResponse(content="Mock answer with tools")


class TestContextUtilizationStrategies(unittest.TestCase):

    def setUp(self):
        self.bm25_index = BM25Index(index_path="./data/test_ctx_util_bm25.json")
        self.bm25_index.clear()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_ctx_util_qdrant")
        self.graph_retriever = GraphRetriever(bm25_index=self.bm25_index, vector_store=self.vector_store)
        self.catalog_manager = GlobalCatalogManager()
        self.memory_graph = InMemoryEntityGraph()
        self.mock_llm = MockContextUtilLLM()

    def test_01_adaptive_global_manifest_and_domain_topology(self):
        # Add concepts across multiple domains
        c1 = OKFConcept(
            type="Issue",
            title="PAY-928: 3DS Timeout",
            resource="https://jira.company.com/PAY-928",
            tags=["jira", "payments"],
            body="3DS payment gateway timeout",
        )
        c2 = OKFConcept(
            type="Runbook",
            title="Postgres Failover SOP",
            resource="https://dropbox.company.com/postgres_dr.docx",
            tags=["dropbox", "postgres", "sre"],
            body="Database failover instructions",
        )
        self.catalog_manager.add_concepts([c1, c2])

        # Test Domain Topology TOON
        topology = self.catalog_manager.generate_domain_topology_toon()
        self.assertIn("[ENTERPRISE DOMAIN TOPOLOGY (TOON v0.2)]", topology)
        self.assertIn("domains[2]{domain,doc_count,connectors,primary_topics}:", topology)
        self.assertIn("Payments", topology)
        self.assertIn("Infrastructure", topology)

        # Test Adaptive Manifest (small corpus <= 30 docs returns document TOON)
        adaptive = self.catalog_manager.get_adaptive_global_manifest(max_tokens=400)
        self.assertIn("PAY-928: 3DS Timeout", adaptive)

    def test_02_sequential_window_expansion(self):
        # Register 3 sequential chunks in bm25_index chunks_map
        step1 = {
            "chunk_id": "dr_step1",
            "title": "Postgres DR SOP",
            "text": "Step 1: Check replication lag",
            "next_chunk_id": "dr_step2",
            "sequence": {"step": 1, "total_steps": 3, "sequence_id": "seq_dr"},
        }
        step2 = {
            "chunk_id": "dr_step2",
            "title": "Postgres DR SOP",
            "text": "Step 2: Run patronictl failover",
            "prev_chunk_id": "dr_step1",
            "next_chunk_id": "dr_step3",
            "sequence": {"step": 2, "total_steps": 3, "sequence_id": "seq_dr"},
        }
        step3 = {
            "chunk_id": "dr_step3",
            "title": "Postgres DR SOP",
            "text": "Step 3: Repoint PgBouncer pool",
            "prev_chunk_id": "dr_step2",
            "sequence": {"step": 3, "total_steps": 3, "sequence_id": "seq_dr"},
        }

        self.bm25_index.chunks_map["dr_step1"] = step1
        self.bm25_index.chunks_map["dr_step2"] = step2
        self.bm25_index.chunks_map["dr_step3"] = step3

        # Candidate only has step 2 retrieved
        candidates = [dict(step2)]
        expanded = self.graph_retriever.expand_sequential_windows(candidates, window_size=1)

        self.assertEqual(len(expanded), 3)
        self.assertEqual(expanded[0]["chunk_id"], "dr_step1")
        self.assertTrue(expanded[0].get("is_expanded_sibling"))
        self.assertEqual(expanded[1]["chunk_id"], "dr_step2")
        self.assertEqual(expanded[2]["chunk_id"], "dr_step3")
        self.assertTrue(expanded[2].get("is_expanded_sibling"))

    def test_03_developer_subgraph_toon_generation(self):
        # Create PR and User nodes
        pr_node = GraphNode(
            node_id="github:pr:company/payments:142",
            label="PullRequest",
            properties={
                "number": 142,
                "title": "Fix 3DS socket timeout",
                "state": "MERGED",
                "author_login": "alice",
                "merged_at": "2026-09-21T10:00:00Z",
                "base_branch": "main",
            },
        )
        user_node = GraphNode(
            node_id="github:user:bob",
            label="User",
            properties={"login": "bob", "name": "Bob Reviewer"},
        )
        review_rel = GraphRelationship(
            from_id="github:user:bob",
            to_id="github:pr:company/payments:142",
            rel_type="REVIEWED",
            properties={"state": "APPROVED"},
        )

        self.memory_graph.add_node(pr_node)
        self.memory_graph.add_node(user_node)
        self.memory_graph.add_relationship(review_rel)

        subgraph_toon = self.memory_graph.get_entity_subgraph_toon("142")
        self.assertIsNotNone(subgraph_toon)
        self.assertIn("G:PR#142", subgraph_toon)
        self.assertIn("author=alice", subgraph_toon)
        self.assertIn("reviewers=bob", subgraph_toon)
        self.assertIn("status=MERGED", subgraph_toon)

    def test_04_planner_adaptive_manifest_system_prompt(self):
        c1 = OKFConcept(
            type="Issue",
            title="PAY-928: 3DS Timeout",
            resource="https://jira.company.com/PAY-928",
            tags=["jira", "payments"],
            body="3DS payment gateway timeout",
        )
        self.catalog_manager.add_concept(c1)

        planner = LangGraphAgentPlanner(
            llm_provider=self.mock_llm,
            global_catalog_manager=self.catalog_manager,
            graph_retriever=self.graph_retriever,
            context_format="toon",
        )

        prompt = planner._get_reasoner_system_prompt()
        self.assertIn("[TOPOLOGICAL OVERVIEW]", prompt)
        self.assertIn("PAY-928", prompt)

    def test_05_answer_generator_and_evaluator_supersession_rules(self):
        self.assertIn("Conflict & Supersession Awareness", AnswerGenerator.SYSTEM_PROMPT)
        self.assertIn("Conflict & Supersession Awareness", EvidenceEvaluator.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
