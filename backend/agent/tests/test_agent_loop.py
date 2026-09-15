"""
Test Suite for Agent Planner & Autonomous Tool Calling Loop (Phase 4).

Validates:
  1. Multi-turn tool execution loop (Agent -> Tool Call -> Tool Result -> Final Answer).
  2. Integration with SemanticRetriever & Qdrant vector storage.
  3. Grounded answer generation and source citation tracking.
  4. RBAC context propagation during agent tool execution.
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
from typing import Any, Dict, List, Optional

from backend.agent.planner import AgentPlanner, AgentResult
from backend.agent.tools import ToolRegistry, create_default_tool_registry
from backend.generation.answer_generator import AnswerGenerator
from backend.generation.context_builder import ContextBuilder
from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.llm.base import LLMProvider, LLMResponse, Message, MessageRole, ToolCall, ToolDefinition
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.qdrant_client import QdrantVectorStore


class MockLLMProvider(LLMProvider):
    """
    Deterministic mock provider simulating an agent calling semantic_search
    and then synthesizing a grounded answer.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/mock-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        return "Based on [1], you should call POST /v1/payments/initiate."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)

        # If last message is TOOL_RESULT, formulate final answer
        if messages and messages[-1].role == MessageRole.TOOL_RESULT:
            return LLMResponse(
                content="To initialize a payment, call POST /v1/payments/initiate with amount and customer_id [1]."
            )

        # First turn: call semantic_search tool
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="semantic_search",
                    arguments={"query": "initialize payment intent API"},
                )
            ]
        )


class TestAgentLoop(unittest.TestCase):

    def setUp(self) -> None:
        self.embedder = LocalEmbedder()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_agent_collection")
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

    def test_01_context_builder_formatting(self) -> None:
        chunks = self.retriever.search("initiate transaction")
        self.assertGreaterEqual(len(chunks), 1)

        context_str, citations = ContextBuilder.build_context(chunks)
        self.assertIn("Payments API", context_str)
        self.assertIn("[1]", context_str)
        self.assertGreaterEqual(len(citations), 1)
        self.assertEqual(citations[0]["title"], "Payments API Guide")
        self.assertEqual(citations[0]["url"], "https://github.com/company/payments/docs/api.md")

    def test_02_autonomous_agent_tool_loop(self) -> None:
        mock_llm = MockLLMProvider()
        planner = AgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )

        result: AgentResult = planner.run(
            query="How do I initiate a payment transaction?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # Verify tool was called in Turn 1
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0]["tool"], "semantic_search")
        self.assertEqual(result.tool_calls[0]["arguments"]["query"], "initialize payment intent API")

        # Verify chunks were retrieved
        self.assertGreater(len(result.retrieved_chunks), 0)

        # Verify final answer and citation attribution
        self.assertIn("POST /v1/payments/initiate", result.answer)
        self.assertIn("[1]", result.answer)
        self.assertEqual(len(result.citations), len(result.retrieved_chunks))
        self.assertEqual(result.citations[0]["title"], "Payments API Guide")

        # Verify total turns took exactly 2 turns (Turn 1: Tool Call, Turn 2: Final Text)
        self.assertEqual(result.turns, 2)

    def test_03_agent_rbac_isolation(self) -> None:
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

        # Search as guest
        guest_results = self.tool_registry.execute(
            tool_name="semantic_search",
            arguments={"query": "Master encryption key"},
            user_context={"roles": ["guest"]},
        )
        self.assertFalse(any("AES-256-GCM-SECRET-9999" in r.get("text", "") for r in guest_results))

        # Search as security-admin
        admin_results = self.tool_registry.execute(
            tool_name="semantic_search",
            arguments={"query": "Master encryption key"},
            user_context={"roles": ["security-admin"]},
        )
        self.assertTrue(any("AES-256-GCM-SECRET-9999" in r.get("text", "") for r in admin_results))


if __name__ == "__main__":
    unittest.main()
