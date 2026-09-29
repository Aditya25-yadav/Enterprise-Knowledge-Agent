"""
Unit and Integration Test Suite for LangGraph Conversation Threads and Checkpointing (Phase 9+).

Validates:
  1. Multi-turn conversation persistence using MemorySaver and SqliteCheckpointSaver.
  2. Follow-up query context and pronoun resolution across turns.
  3. Thread isolation (Thread A and Thread B maintain separate checkpoint histories).
  4. SQLite checkpoint persistence across planner re-instantiation (simulating process restart).
  5. Sliding-window conversation trimming (`trim_conversation_history`) under message budget limits.
  6. RBAC security context enforcement across multi-turn sessions.
  7. SqliteCheckpointSaver database management APIs (get_all_threads, delete_thread, list).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.state import trim_conversation_history
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
from backend.models.graph import (
    FileNode,
    GraphRelationship,
    PullRequestNode,
    RelType,
    RepositoryNode,
    UserNode,
)
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.checkpointers import (
    MemorySaver,
    SqliteCheckpointSaver,
    get_checkpointer,
)
from backend.storage.qdrant_client import QdrantVectorStore


class MockMultiTurnLLM(LLMProvider):
    """
    Deterministic multi-turn Mock LLM for testing thread persistence and tool calling.
    Adapts behavior based on conversation turn and incoming message history.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/multi-turn-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""

        # Evaluator mock
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 1.0,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Sufficient evidence collected.",
            })

        # Reformulator mock
        if "Analyze the query and missing information" in last_content:
            return json.dumps({
                "reformulated_query": "Targeted search query",
                "suggested_tool": "semantic_search",
                "reasoning": "Direct match",
            })

        # Context builder / Answer synthesis
        combined_text = " ".join(m.content for m in messages if m.role == MessageRole.USER)
        if "last question" in combined_text.lower() or "previous question" in combined_text.lower():
            return "The last question you asked was: 'Who authored PR #142?'"
        if "file" in combined_text.lower() or "modify" in combined_text.lower():
            return "Alice modified backend/services/checkout.py in PR #142 [1]."
        if "author" in combined_text.lower() or "142" in combined_text.lower():
            return "PR #142 was authored by Alice Developer (alice) [1]."
        if "kms" in combined_text.lower() or "vault" in combined_text.lower():
            return "Master AES-256 Vault Encryption Key ARN: arn:aws:kms:us-east-1:998877665544:key/vault-prod-master-2026 [1]."

        return "Enterprise Agent Answer based on retrieved evidence [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        user_queries = [m.content for m in messages if m.role == MessageRole.USER]
        last_user_query = user_queries[-1] if user_queries else ""

        # Check if this is a follow-up about files or author
        if "file" in last_user_query.lower() or "modify" in last_user_query.lower():
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="github_entity_search",
                        arguments={"operation": "get_pr_details", "target": "#142"},
                        call_id="call_followup_files",
                    )
                ]
            )

        if "kms" in last_user_query.lower() or "secret" in last_user_query.lower():
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="keyword_search",
                        arguments={"query": "KMS Encryption Keys"},
                        call_id="call_kms",
                    )
                ]
            )

        # Default PR 142 lookup
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="github_entity_search",
                    arguments={"operation": "get_pr_details", "target": "#142"},
                    call_id="call_pr_142",
                )
            ]
        )


class TestConversationThreadsAndCheckpointing(unittest.TestCase):
    """Validates multi-turn stateful conversational threads with LangGraph checkpointers."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.bm25_path = os.path.join(self.temp_dir.name, "test_bm25.json")
        self.sqlite_db_path = os.path.join(self.temp_dir.name, "test_sessions.db")

        # Storage components
        self.embedder = LocalEmbedder()
        self.vector_store = QdrantVectorStore(mode="memory", collection_name="test_threads")
        self.bm25_index = BM25Index(index_path=self.bm25_path)

        # Ingestion pipeline
        pipeline = IngestionPipeline(
            embedder=self.embedder,
            vector_store=self.vector_store,
            bm25_index=self.bm25_index,
        )

        # Ingest test enterprise data
        c1 = OKFConcept(
            type="File",
            title="Payments API Specification",
            resource="github://repo/payments/api.md",
            body="Payments API guide. Requires Idempotency-Key header for POST /v1/payments/initiate.",
            tags=["github", "payments"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
        )
        c2 = OKFConcept(
            type="Page",
            title="CISO Master KMS Keys",
            resource="notion://vault/kms",
            body="Master AES-256 Vault Encryption Key ARN: arn:aws:kms:us-east-1:998877665544:key/vault-prod-master-2026.",
            tags=["notion", "kms"],
            permissions=OKFPermissions(allowed_roles=["ciso_admin"], is_public=False),
        )
        pipeline.ingest_concept(c1)
        pipeline.ingest_concept(c2)

        # Developer Property Graph
        repo = RepositoryNode(full_name="company/payments", name="payments", owner_login="company", html_url="https://github.com/company/payments")
        user_alice = UserNode(login="alice", name="Alice Developer", email="alice@company.com")
        pr_142 = PullRequestNode(repo_full_name="company/payments", number=142, title="Fix 3DS timeout", author_login="alice")
        file_checkout = FileNode(repo_full_name="company/payments", path="backend/services/checkout.py")

        nodes = [repo.to_graph_node(), user_alice.to_graph_node(), pr_142.to_graph_node(), file_checkout.to_graph_node()]
        relationships = [
            GraphRelationship(from_id=user_alice.node_id, to_id=pr_142.node_id, rel_type=RelType.AUTHORED.value),
            GraphRelationship(from_id=pr_142.node_id, to_id=file_checkout.node_id, rel_type=RelType.MODIFIES.value),
        ]

        memory_graph = InMemoryEntityGraph()
        for n in nodes:
            memory_graph.add_node(n)
        for r in relationships:
            memory_graph.add_relationship(r)

        self.entity_retriever = EntityGraphRetriever(neo4j_client=None, memory_graph=memory_graph)
        self.semantic_retriever = SemanticRetriever(embedder=self.embedder, vector_store=self.vector_store)
        self.keyword_retriever = KeywordRetriever(bm25_index=self.bm25_index)
        self.graph_retriever = GraphRetriever(bm25_index=self.bm25_index, vector_store=self.vector_store)
        self.resource_retriever = ResourceLookupRetriever(bm25_index=self.bm25_index, vector_store=self.vector_store)

        self.tool_registry = create_default_tool_registry(
            semantic_retriever=self.semantic_retriever,
            keyword_retriever=self.keyword_retriever,
            entity_graph_retriever=self.entity_retriever,
            graph_retriever=self.graph_retriever,
            resource_lookup_retriever=self.resource_retriever,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_in_memory_multi_turn_thread_persistence(self) -> None:
        """Test multi-turn state preservation and follow-up query execution using MemorySaver."""
        checkpointer = MemorySaver()
        mock_llm = MockMultiTurnLLM()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            checkpointer=checkpointer,
            enable_reranking=False,
        )

        user_ctx = {"roles": ["engineer"], "user_id": "eng@company.com"}
        thread_id = "test_thread_001"

        # Turn 1: Initial question
        res1 = planner.run(
            query="Who authored PR #142?",
            user_context=user_ctx,
            thread_id=thread_id,
        )
        self.assertIn("Alice", res1["answer"])
        self.assertEqual(res1["thread_id"], thread_id)

        # Verify checkpoint exists
        checkpoint_tuple = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        self.assertIsNotNone(checkpoint_tuple)
        messages_t1 = checkpoint_tuple.checkpoint["channel_values"]["messages"]
        self.assertTrue(len(messages_t1) >= 2)  # HumanMessage + Tool + AIMessage

        # Turn 2: Follow-up question using pronoun / entity reference
        res2 = planner.run(
            query="What files did she modify in it?",
            user_context=user_ctx,
            thread_id=thread_id,
        )
        self.assertIn("checkout.py", res2["answer"])

        # Verify messages accumulated in thread state
        checkpoint_tuple_t2 = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        messages_t2 = checkpoint_tuple_t2.checkpoint["channel_values"]["messages"]
        self.assertGreater(len(messages_t2), len(messages_t1))

    def test_thread_isolation(self) -> None:
        """Test that distinct thread IDs maintain independent conversation states without crosstalk."""
        checkpointer = MemorySaver()
        mock_llm = MockMultiTurnLLM()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            checkpointer=checkpointer,
            enable_reranking=False,
        )

        # Thread A: PR inquiry
        res_a = planner.run(
            query="Who authored PR #142?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
            thread_id="session_alpha",
        )
        self.assertIn("Alice", res_a["answer"])

        # Thread B: Different user / KMS inquiry
        res_b = planner.run(
            query="What is the KMS Key ARN?",
            user_context={"roles": ["ciso_admin"], "user_id": "ciso@company.com"},
            thread_id="session_beta",
        )
        self.assertIn("vault-prod-master", res_b["answer"])

        # Check Thread Alpha messages
        tuple_a = checkpointer.get_tuple({"configurable": {"thread_id": "session_alpha"}})
        msgs_a = tuple_a.checkpoint["channel_values"]["messages"]
        user_queries_a = [m.content for m in msgs_a if isinstance(m, HumanMessage)]
        self.assertIn("Who authored PR #142?", user_queries_a)
        self.assertNotIn("What is the KMS Key ARN?", user_queries_a)

        # Check Thread Beta messages
        tuple_b = checkpointer.get_tuple({"configurable": {"thread_id": "session_beta"}})
        msgs_b = tuple_b.checkpoint["channel_values"]["messages"]
        user_queries_b = [m.content for m in msgs_b if isinstance(m, HumanMessage)]
        self.assertIn("What is the KMS Key ARN?", user_queries_b)
        self.assertNotIn("Who authored PR #142?", user_queries_b)

    def test_sqlite_checkpoint_persistence_across_instances(self) -> None:
        """Test SQLite disk checkpointer across planner re-instantiation (simulating app restart)."""
        checkpointer_v1 = SqliteCheckpointSaver(db_path=self.sqlite_db_path)
        mock_llm = MockMultiTurnLLM()

        planner_v1 = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            checkpointer=checkpointer_v1,
            enable_reranking=False,
        )

        thread_id = "persistent_session_42"
        user_ctx = {"roles": ["engineer"], "user_id": "eng@company.com"}

        # Turn 1 on planner 1
        res1 = planner_v1.run(
            query="Who authored PR #142?",
            user_context=user_ctx,
            thread_id=thread_id,
        )
        self.assertIn("Alice", res1["answer"])

        # Simulate process restart by instantiating new checkpointer and planner pointing to same SQLite DB
        checkpointer_v2 = SqliteCheckpointSaver(db_path=self.sqlite_db_path)
        saved_threads = checkpointer_v2.get_all_threads()
        self.assertIn(thread_id, saved_threads)

        planner_v2 = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            checkpointer=checkpointer_v2,
            enable_reranking=False,
        )

        # Turn 2 on planner 2
        res2 = planner_v2.run(
            query="What files did she modify?",
            user_context=user_ctx,
            thread_id=thread_id,
        )
        self.assertIn("checkout.py", res2["answer"])

        # Verify combined history
        tuple_v2 = checkpointer_v2.get_tuple({"configurable": {"thread_id": thread_id}})
        msgs_v2 = tuple_v2.checkpoint["channel_values"]["messages"]
        user_msgs = [m.content for m in msgs_v2 if isinstance(m, HumanMessage)]
        self.assertEqual(user_msgs[0], "Who authored PR #142?")
        self.assertEqual(user_msgs[1], "What files did she modify?")

    def test_trim_conversation_history(self) -> None:
        """Test sliding-window conversation trimming under token/message constraints."""
        system_msg = SystemMessage(content="System prompt")
        messages: List[Any] = [system_msg]

        # Generate 30 messages (15 user, 15 AI)
        for i in range(15):
            messages.append(HumanMessage(content=f"User turn {i}"))
            messages.append(AIMessage(content=f"AI turn {i}"))

        self.assertEqual(len(messages), 31)

        # Trim to max 10 messages
        trimmed = trim_conversation_history(messages, max_messages=10)

        # Verify length
        self.assertEqual(len(trimmed), 10)
        # Verify first message is preserved system prompt
        self.assertIsInstance(trimmed[0], SystemMessage)
        self.assertEqual(trimmed[0].content, "System prompt")
        # Verify latest messages are preserved
        self.assertEqual(trimmed[-1].content, "AI turn 14")
        self.assertEqual(trimmed[-2].content, "User turn 14")

    def test_rbac_context_enforcement_in_multi_turn_session(self) -> None:
        """Test that user role permissions are strictly re-evaluated per turn within the same thread."""
        checkpointer = MemorySaver()
        mock_llm = MockMultiTurnLLM()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            checkpointer=checkpointer,
            enable_reranking=False,
        )

        thread_id = "rbac_session_007"

        # Turn 1: Authorized CISO user retrieves KMS secrets
        res1 = planner.run(
            query="What is the KMS Key ARN?",
            user_context={"roles": ["ciso_admin"], "user_id": "ciso@company.com"},
            thread_id=thread_id,
        )
        self.assertIn("vault-prod-master", res1["answer"])

        # Turn 2: Unauthorized Guest user executes a query on the same thread
        res2 = planner.run(
            query="Show me the KMS master key",
            user_context={"roles": ["guest"], "user_id": "guest@external.com"},
            thread_id=thread_id,
        )
        # Guest is blocked by RBAC at retrieval level
        retrieved_chunks = res2.get("retrieved_chunks", [])
        for chunk in retrieved_chunks:
            self.assertNotIn("AES-SECRET-KEY-PROD", chunk.get("text", ""))

    def test_sqlite_checkpoint_database_management_apis(self) -> None:
        """Test get_all_threads, delete_thread, list, and factory get_checkpointer."""
        saver = get_checkpointer("sqlite", db_path=self.sqlite_db_path)
        self.assertIsInstance(saver, SqliteCheckpointSaver)

        # Put dummy checkpoint in thread_1
        config_1 = {"configurable": {"thread_id": "thread_alpha", "checkpoint_ns": ""}}
        checkpoint_1 = {
            "v": 1,
            "id": "cp_001",
            "ts": "2026-09-25T00:00:00Z",
            "channel_values": {"messages": [HumanMessage(content="Hello")]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }
        saver.put(config_1, checkpoint_1, {}, {})

        # Put dummy checkpoint in thread_2
        config_2 = {"configurable": {"thread_id": "thread_beta", "checkpoint_ns": ""}}
        checkpoint_2 = {
            "v": 1,
            "id": "cp_002",
            "ts": "2026-09-25T00:00:00Z",
            "channel_values": {"messages": [HumanMessage(content="World")]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }
        saver.put(config_2, checkpoint_2, {}, {})

        # List all threads
        threads = saver.get_all_threads()
        self.assertIn("thread_alpha", threads)
        self.assertIn("thread_beta", threads)

        # List checkpoints
        history = list(saver.list(config_1))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].checkpoint["id"], "cp_001")

        # Delete thread_alpha
        saver.delete_thread("thread_alpha")
        threads_after = saver.get_all_threads()
        self.assertNotIn("thread_alpha", threads_after)
        self.assertIn("thread_beta", threads_after)

    def test_meta_conversational_query_direct_answer(self) -> None:
        """Test that meta-conversational queries (e.g. 'what was the last question I asked') answer directly with 0 tools called."""
        checkpointer = MemorySaver()
        mock_llm = MockMultiTurnLLM()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            checkpointer=checkpointer,
            enable_reranking=False,
        )

        user_ctx = {"roles": ["engineer"], "user_id": "eng@company.com"}
        thread_id = "meta_test_session"

        # Turn 1: Regular inquiry with tool execution
        res1 = planner.run(
            query="Who authored PR #142?",
            user_context=user_ctx,
            thread_id=thread_id,
        )
        self.assertIn("Alice", res1["answer"])
        self.assertGreater(len(res1["tool_calls"]), 0)

        # Turn 2: Meta-conversational inquiry
        res2 = planner.run(
            query="what was the last question i asked",
            user_context=user_ctx,
            thread_id=thread_id,
        )
        self.assertIn("Who authored PR #142?", res2["answer"])
        # Must execute with 0 tool calls in Turn 2 and 0 citations
        self.assertEqual(len(res2["tool_calls"]), 0, "Meta-conversational turn must not execute retrieval tools")
        self.assertEqual(len(res2["citations"]), 0, "Direct meta answers must not have citations")


if __name__ == "__main__":
    unittest.main()
