"""
Test Suite for LangGraph-Orchestrated Enterprise Agent (Phase 4).

Validates:
  1. LangGraph StateGraph compilation and state transitions.
  2. Multi-turn reasoning loop via LangGraph (reasoner -> tool_node -> reasoner -> generator).
  3. Integration with SemanticRetriever & Qdrant vector storage.
  4. RBAC context propagation through LangGraph AgentState.
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

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.tools import ToolRegistry, create_default_tool_registry
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
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.qdrant_client import QdrantVectorStore


class MockLLMForLangGraph(LLMProvider):
    """
    Deterministic mock LLM for testing LangGraph state machine.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        return "Based on [1], you should call POST /v1/payments/initiate."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)

        # If previous turn was a TOOL_RESULT, generate final answer
        if messages and messages[-1].role == MessageRole.TOOL_RESULT:
            return LLMResponse(
                content="To initialize a payment, call POST /v1/payments/initiate with amount and customer_id [1]."
            )

        # Turn 1: request semantic_search tool
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="semantic_search",
                    arguments={"query": "initialize payment intent API"},
                )
            ]
        )


class TestLangGraphAgent(unittest.TestCase):

    def setUp(self) -> None:
        self.embedder = LocalEmbedder()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_langgraph_collection")
        self.pipeline = IngestionPipeline(embedder=self.embedder, vector_store=self.vector_store)
        self.retriever = SemanticRetriever(embedder=self.embedder, vector_store=self.vector_store)
        self.tool_registry = create_default_tool_registry(semantic_retriever=self.retriever)

        # Ingest sample knowledge document
        doc = OKFConcept(
            type="Architecture",
            title="Payments API Guide",
            resource="https://github.com/company/payments/docs/api.md",
            body="""# Payments API

## Payment Initiation
To initiate a transaction, send a POST request to /v1/payments/initiate containing the amount, currency, and customer_id. The gateway returns a client_secret token for 3DS verification.
""",
            permissions=OKFPermissions(is_public=True),
        )
        self.pipeline.ingest_concept(doc)

    def test_01_graph_compilation(self) -> None:
        mock_llm = MockLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )
        self.assertIsNotNone(planner.graph)
        # Graph nodes should be present
        nodes = planner.graph.nodes
        self.assertIn("reasoner", nodes)
        self.assertIn("tool_node", nodes)
        self.assertIn("generator", nodes)

    def test_02_autonomous_langgraph_tool_loop(self) -> None:
        mock_llm = MockLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )

        result = planner.run(
            query="How do I initiate a payment transaction?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify tool was called in Turn 1
        self.assertGreaterEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["tool"], "semantic_search")
        self.assertEqual(result["tool_calls"][0]["arguments"]["query"], "initialize payment intent API")

        # 2. Verify chunks were retrieved
        self.assertGreater(len(result["retrieved_chunks"]), 0)

        # 3. Verify final answer and citation attribution
        self.assertIn("POST /v1/payments/initiate", result["answer"])
        self.assertIn("[1]", result["answer"])
        self.assertEqual(len(result["citations"]), len(result["retrieved_chunks"]))
        self.assertEqual(result["citations"][0]["title"], "Payments API Guide")

        # 4. Verify turn count
        self.assertEqual(result["turns"], 2)

    def test_03_langgraph_rbac_isolation(self) -> None:
        # Ingest restricted document
        secret_doc = OKFConcept(
            type="Secret",
            title="Vault Master Key",
            resource="notion://vault/master",
            body="Master encryption key is AES-256-GCM-SECRET-9999.",
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["security-admin"],
            ),
        )
        self.pipeline.ingest_concept(secret_doc)

        mock_llm = MockLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )

        # Run as guest
        guest_res = planner.run(
            query="What is the master encryption key?",
            user_context={"roles": ["guest"]},
        )
        self.assertFalse(any("AES-256-GCM-SECRET-9999" in r.get("text", "") for r in guest_res["retrieved_chunks"]))

    def test_04_native_langchain_tools_execution(self) -> None:
        from backend.agent.langchain_tools import create_langchain_tools

        lc_tools = create_langchain_tools(
            semantic_retriever=self.retriever,
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )
        self.assertEqual(len(lc_tools), 2)
        tool_names = [t.name for t in lc_tools]
        self.assertIn("semantic_search", tool_names)
        self.assertIn("keyword_search", tool_names)

        # Execute semantic_search directly as a LangChain tool
        sem_tool = next(t for t in lc_tools if t.name == "semantic_search")
        res_json = sem_tool.invoke({"query": "payment transaction initiation", "top_k": 2})
        self.assertIn("Payments API Guide", res_json)
        self.assertIn("/v1/payments/initiate", res_json)


if __name__ == "__main__":
    unittest.main()
