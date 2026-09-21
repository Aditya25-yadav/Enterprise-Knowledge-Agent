"""
Test Suite for LangGraph-Orchestrated Enterprise Agent (Phase 4 & Phase 5).

Validates:
  1. LangGraph StateGraph compilation and state transitions.
  2. Multi-turn reasoning loop via LangGraph (reasoner -> tool_node -> reasoner -> generator).
  3. Multi-tool execution in a single turn (semantic_search + keyword_search).
  4. Integration with SemanticRetriever & KeywordRetriever (Qdrant + BM25).
  5. RBAC context propagation through LangGraph AgentState.
  6. Native LangChain BaseTool execution with Pydantic validation.
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

from backend.agent.langchain_tools import create_langchain_tools
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
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class MockLLMForLangGraph(LLMProvider):
    """
    Deterministic mock LLM for testing single-tool LangGraph loop.
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


class MockMultiToolLLMForLangGraph(LLMProvider):
    """
    Deterministic mock LLM for testing multi-tool execution in a single turn.
    Emits both `keyword_search` and `semantic_search` simultaneously in Turn 1.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/multitool-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        return "Payment bug PAY-928 is resolved by checking token timeout [1], following the payments guide [2]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)

        # If previous turn was a TOOL_RESULT, generate final answer synthesizing both tools
        if messages and messages[-1].role == MessageRole.TOOL_RESULT:
            return LLMResponse(
                content="Bug PAY-928 describes a 3DS timeout in the checkout flow [1]. Per the API documentation [2], transactions must be initiated via POST /v1/payments/initiate."
            )

        # Turn 1: request BOTH keyword_search AND semantic_search in a single turn!
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="keyword_search",
                    arguments={"query": "PAY-928"},
                    call_id="call_kw_1",
                ),
                ToolCall(
                    tool_name="semantic_search",
                    arguments={"query": "payment transaction initiation"},
                    call_id="call_sem_2",
                ),
            ]
        )


class TestLangGraphAgent(unittest.TestCase):

    def setUp(self) -> None:
        self.embedder = LocalEmbedder()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_langgraph_collection")
        self.bm25_index = BM25Index(index_path="./data/test_langgraph_bm25.json")
        self.bm25_index.clear()

        # Ingestion pipeline with dual-indexing (vector + BM25)
        self.pipeline = IngestionPipeline(
            embedder=self.embedder,
            vector_store=self.vector_store,
            bm25_index=self.bm25_index,
        )

        self.retriever = SemanticRetriever(embedder=self.embedder, vector_store=self.vector_store)
        self.keyword_retriever = KeywordRetriever(bm25_index=self.bm25_index)
        self.tool_registry = create_default_tool_registry(
            semantic_retriever=self.retriever,
            keyword_retriever=self.keyword_retriever,
        )

        # Ingest Doc 1: Architecture Guide
        doc1 = OKFConcept(
            type="Architecture",
            title="Payments API Guide",
            resource="https://github.com/company/payments/docs/api.md",
            body="""# Payments API Guide
To initiate a transaction, send a POST request to `/v1/payments/initiate` containing the amount, currency, and customer_id. The gateway returns a client_secret token for 3DS verification.
""",
            tags=["github", "payments", "api"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "github", "resource_type": "file"},
        )
        self.pipeline.ingest_concept(doc1)

        # Ingest Doc 2: Bug Ticket PAY-928
        doc2 = OKFConcept(
            type="Issue",
            title="PAY-928: 3DS timeout in Checkout Flow",
            resource="https://jira.enterprise.com/browse/PAY-928",
            body="""# Bug PAY-928: 3DS Timeout
Users encounter HTTP 401 during the Checkout Flow when calling AuthService.validate_token.
Status: In Progress. Assignee: dev-team.
""",
            tags=["jira", "payments", "bug"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "jira", "resource_type": "issue"},
        )
        self.pipeline.ingest_concept(doc2)

    def tearDown(self) -> None:
        self.bm25_index.clear()

    def test_01_graph_compilation(self) -> None:
        mock_llm = MockLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )
        self.assertIsNotNone(planner.graph)
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
        lc_tools = create_langchain_tools(
            semantic_retriever=self.retriever,
            keyword_retriever=self.keyword_retriever,
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

    def test_05_multi_tool_execution_in_single_turn(self) -> None:
        """
        Tests that when the LLM emits multiple tool calls in a single turn
        (keyword_search + semantic_search), LangGraph executes both in _tool_node,
        aggregates chunks from both tools, and synthesizes a grounded answer.
        """
        mock_multitool_llm = MockMultiToolLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_multitool_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )

        result = planner.run(
            query="What is PAY-928 and how does our payment transaction initiation work?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify 2 tool calls were recorded in Turn 1
        self.assertEqual(len(result["tool_calls"]), 2)
        tool_names_called = [tc["tool"] for tc in result["tool_calls"]]
        self.assertIn("keyword_search", tool_names_called)
        self.assertIn("semantic_search", tool_names_called)

        # 2. Verify evidence was retrieved from BOTH sources
        retrieved_titles = [c.get("title") for c in result["retrieved_chunks"]]
        self.assertTrue(any("PAY-928" in t for t in retrieved_titles), "Expected PAY-928 chunk in retrieved evidence")
        self.assertTrue(any("Payments API Guide" in t for t in retrieved_titles), "Expected Payments API Guide in retrieved evidence")

        # 3. Verify grounded answer references both [1] and [2]
        self.assertIn("PAY-928", result["answer"])
        self.assertIn("[1]", result["answer"])
        self.assertIn("[2]", result["answer"])
        self.assertEqual(result["turns"], 2)

    def test_06_native_keyword_search_langchain_tool(self) -> None:
        """
        Verifies direct execution of the native LangChain keyword_search StructuredTool.
        """
        lc_tools = create_langchain_tools(
            semantic_retriever=self.retriever,
            keyword_retriever=self.keyword_retriever,
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )
        kw_tool = next(t for t in lc_tools if t.name == "keyword_search")
        res_json = kw_tool.invoke({"query": "PAY-928", "top_k": 1})
        self.assertIn("PAY-928", res_json)
        self.assertIn("3DS Timeout", res_json)


if __name__ == "__main__":
    unittest.main()
