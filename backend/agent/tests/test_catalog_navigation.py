"""
Test Suite for Map-First Catalog Navigation & Progressive Disclosure in LangGraph Agent.

Validates:
  1. Map-First Discovery on Turn 1: Reasoner calls `catalog_discovery` first.
  2. Evaluator Reflection on Catalog Summaries: Evaluator detects that only index summaries exist and triggers `RETRIEVE_MORE`.
  3. Targeted Deep Tool Dispatch on Turn 2: Reasoner follows catalog confidence guidance to call `resource_lookup` / `github_entity_search`.
  4. Final Answer Generation: Full document body triggers `GENERATE` with grounded citations [1], [2].
  5. Expanded 10-turn planning budget.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.tools import create_default_tool_registry
from backend.ingestion.catalog_aggregator import GlobalCatalogManager
from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolDefinition,
)
from backend.models.okf import OKFConcept, OKFPermissions
from backend.ranking.reranker import CrossEncoderReranker
from backend.retrieval.catalog import CatalogRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class MockMapFirstLLMProvider(LLMProvider):
    """
    Simulates a Map-First Reasoner & Evaluator:
    - Turn 1: Calls `catalog_discovery`
    - Turn 2: Calls `resource_lookup` after evaluator reflection
    - Generator: Emits final cited response
    """

    def __init__(self, target_resource_url: str):
        self.target_resource_url = target_resource_url
        self.turn = 0
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/map-first-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""

        # Evaluator prompt evaluation
        if "Evaluate the evidence above" in last_content:
            # If evidence only contains catalog summary, mark insufficient
            if "[CATALOG DISCOVERY SUMMARY" in last_content and "Step 2: Execute manual switchover" not in last_content:
                return json.dumps({
                    "relevance_score": 0.9,
                    "evidence_sufficient": False,
                    "missing_information": ["Full step-by-step failover commands"],
                    "recommended_action": "RETRIEVE_MORE",
                    "recommended_tool": "resource_lookup",
                    "reasoning": "Catalog summary identified DR runbook, but deep body is required.",
                })
            else:
                return json.dumps({
                    "relevance_score": 1.0,
                    "evidence_sufficient": True,
                    "missing_information": [],
                    "recommended_action": "GENERATE",
                    "recommended_tool": None,
                    "reasoning": "Full runbook steps retrieved.",
                })

        # Answer Generator prompt
        return (
            "To failover the PostgreSQL database in disaster recovery:\n"
            "1. Verify replication lag using patronictl [1].\n"
            "2. Execute manual switchover: patronictl switchover --master db-node-01 [1].\n"
            "3. Point PgBouncer connection pool to db-node-02 [1]."
        )

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        self.turn += 1

        # Check if Self-RAG reflection guidance is in messages
        has_reflection = any("[Self-RAG Reflection]" in (m.content or "") for m in messages)

        if not has_reflection and self.turn == 1:
            # Turn 1: Primary Discovery
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="catalog_discovery",
                        arguments={"query": "PostgreSQL disaster recovery failover runbook"},
                        call_id="call_catalog_1",
                    )
                ]
            )
        else:
            # Turn 2: Follow catalog guidance to deep-fetch target runbook
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="resource_lookup",
                        arguments={"resource_id": self.target_resource_url},
                        call_id="call_resource_2",
                    )
                ]
            )


class TestCatalogNavigation(unittest.TestCase):

    def setUp(self) -> None:
        self.vector_store = QdrantVectorStore(mode="memory")
        self.bm25_index = BM25Index(index_path="./data/test_catalog_nav_bm25.json")
        self.bm25_index.clear()
        self.embedder = LocalEmbedder()

        self.pipeline = IngestionPipeline(
            embedder=self.embedder,
            vector_store=self.vector_store,
            bm25_index=self.bm25_index,
        )

        self.catalog_manager = GlobalCatalogManager()

        # Ingest SRE Failover Runbook into vector store, BM25, and Catalog
        self.sre_concept = OKFConcept(
            type="Playbook",
            title="SRE Disaster Recovery: PostgreSQL Failover Runbook v3",
            resource="https://dropbox.enterprise.com/sre/dr_failover_v3.docx",
            body="""# SRE DR Failover Runbook
## 1. Replication Check
Verify PostgreSQL replication lag with `patronictl topology`.

## 2. Master Switchover
Step 2: Execute manual switchover via `patronictl switchover --master db-node-01`.

## 3. Traffic Redirection
Point PgBouncer connection pool to `db-node-02`.
""",
            tags=["dropbox", "sre", "runbook", "disaster_recovery"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "sre"], is_public=True),
            extra_metadata={"source": "dropbox", "resource_type": "playbook", "domain": "Infrastructure & Disaster Recovery"},
        )

        self.pipeline.ingest_concept(self.sre_concept)
        self.catalog_manager.add_concept(self.sre_concept)

        self.mock_llm = MockMapFirstLLMProvider(target_resource_url=self.sre_concept.resource)
        self.catalog_retriever = CatalogRetriever(catalog_manager=self.catalog_manager, llm_provider=self.mock_llm)
        self.semantic_retriever = SemanticRetriever(embedder=self.embedder, vector_store=self.vector_store)
        self.keyword_retriever = KeywordRetriever(bm25_index=self.bm25_index)
        self.resource_retriever = ResourceLookupRetriever(bm25_index=self.bm25_index, vector_store=self.vector_store)

        self.tool_registry = create_default_tool_registry(
            catalog_retriever=self.catalog_retriever,
            semantic_retriever=self.semantic_retriever,
            keyword_retriever=self.keyword_retriever,
            resource_lookup_retriever=self.resource_retriever,
        )

        self.reranker = CrossEncoderReranker()
        self.planner = LangGraphAgentPlanner(
            llm_provider=self.mock_llm,
            tool_registry=self.tool_registry,
            reranker=self.reranker,
            enable_reranking=True,
            max_turns=10,
            max_retrieval_attempts=3,
        )

    def test_default_max_turns_is_10(self) -> None:
        """Verify default planning turn budget is configured to 10."""
        default_planner = LangGraphAgentPlanner(
            llm_provider=self.mock_llm,
            tool_registry=self.tool_registry,
        )
        self.assertEqual(default_planner.max_turns, 10)

    def test_map_first_catalog_discovery_and_deep_retrieval(self) -> None:
        """
        End-to-End Test:
        1. Turn 1: Reasoner calls catalog_discovery
        2. Evaluator detects catalog summary and demands RETRIEVE_MORE with resource_lookup
        3. Turn 2: Reasoner calls resource_lookup
        4. Evaluator detects complete body and triggers GENERATE
        5. Generator produces response with citations
        """
        user_query = "What are the exact steps to failover PostgreSQL database in disaster recovery?"
        user_ctx = {"roles": ["sre", "engineer"], "user_id": "sre@enterprise.com"}

        result = self.planner.run(query=user_query, user_context=user_ctx)

        # Assertions on tool execution flow
        tool_names = [tc["tool"] for tc in result["tool_calls"]]
        self.assertIn("catalog_discovery", tool_names, "Turn 1 must invoke catalog_discovery")
        self.assertIn("resource_lookup", tool_names, "Turn 2 must invoke deep resource_lookup")

        # Assertions on chunks retrieved
        retrieved = result["retrieved_chunks"]
        self.assertGreater(len(retrieved), 0)
        
        # Verify both catalog entry and deep body exist
        has_catalog = any(c.get("is_catalog") for c in retrieved)
        has_deep_body = any("patronictl switchover" in c.get("text", "") for c in retrieved)
        self.assertTrue(has_catalog, "Catalog summary chunk must be preserved in state")
        self.assertTrue(has_deep_body, "Deep document text chunk must be retrieved")

        # Assertions on final answer & citations
        answer = result["answer"]
        self.assertIn("patronictl switchover", answer)
        self.assertIn("db-node-01", answer)
        self.assertIn("[1]", answer)
        self.assertGreater(len(result["citations"]), 0)


if __name__ == "__main__":
    unittest.main()
