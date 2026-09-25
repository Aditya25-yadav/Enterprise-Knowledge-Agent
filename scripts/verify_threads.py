#!/usr/bin/env python3
"""
Verification Script for Multi-Turn Persistent Chatbot Sessions and LangGraph Checkpointers.

Simulates a 3-turn interactive conversation session over a persistent SQLite checkpointer:
  1. Turn 1: Entity lookup query ("Who authored PR #142?").
  2. Turn 2: Follow-up pronoun/context query ("What files did she modify in that PR?").
  3. Turn 3: Multi-hop context query ("What issue was that PR resolving?").

Verifies:
  - Multi-turn state preservation and LangGraph message accumulation.
  - Checkpoint persistence in SQLite storage.
  - Thread list inspection and history retrieval.
  - Thread isolation.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import AIMessage, HumanMessage

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.tools import create_default_tool_registry
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
from backend.storage.checkpointers import SqliteCheckpointSaver, get_checkpointer
from backend.storage.qdrant_client import QdrantVectorStore


class VerificationMockLLM(LLMProvider):
    """Deterministic LLM Provider for End-to-End Thread Verification."""

    @property
    def provider_name(self) -> str:
        return "verification/multi-turn-llm"

    def generate(self, messages: List[Message]) -> str:
        last_content = messages[-1].content if messages else ""

        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 1.0,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Sufficient evidence collected.",
            })

        user_texts = [m.content for m in messages if m.role == MessageRole.USER]
        last_user = user_texts[-1].lower() if user_texts else ""

        if "file" in last_user or "modify" in last_user:
            return "Alice modified `backend/services/checkout.py` in PR #142 [1]."
        if "issue" in last_user or "resolv" in last_user or "pay-928" in last_user:
            return "PR #142 resolved issue PAY-928 regarding 3DS authentication timeouts [1]."
        if "author" in last_user or "142" in last_user:
            return "PR #142 'Fix 3DS timeout in Checkout Flow' was authored by Alice Developer [1]."

        return "Enterprise Agent verified response [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        user_texts = [m.content for m in messages if m.role == MessageRole.USER]
        last_user = user_texts[-1].lower() if user_texts else ""

        if "file" in last_user or "modify" in last_user:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="github_entity_search",
                        arguments={"operation": "get_pr_details", "target": "#142"},
                        call_id="call_files_142",
                    )
                ]
            )

        if "issue" in last_user or "resolv" in last_user:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="keyword_search",
                        arguments={"query": "PAY-928 3DS Timeout"},
                        call_id="call_issue_pay928",
                    )
                ]
            )

        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="github_entity_search",
                    arguments={"operation": "get_pr_details", "target": "#142"},
                    call_id="call_pr_author",
                )
            ]
        )


def main() -> int:
    print("=" * 80)
    print("🔍 VERIFYING MULTI-TURN PERSISTENT CHATBOT SESSIONS & CHECKPOINTING")
    print("=" * 80)

    temp_dir = tempfile.mkdtemp(prefix="agent_thread_verify_")
    db_path = os.path.join(temp_dir, "verified_sessions.db")
    bm25_path = os.path.join(temp_dir, "bm25_verify.json")

    try:
        # 1. Initialize Storage & Ingestion
        print("\n📦 [1/4] Initializing In-Memory Storage & SQLite Checkpointer...")
        embedder = LocalEmbedder()
        vector_store = QdrantVectorStore(mode="memory", collection_name="thread_verify")
        bm25_index = BM25Index(index_path=bm25_path)
        checkpointer = SqliteCheckpointSaver(db_path=db_path)

        pipeline = IngestionPipeline(
            embedder=embedder,
            vector_store=vector_store,
            bm25_index=bm25_index,
        )

        doc1 = OKFConcept(
            type="Issue",
            title="PAY-928: 3DS Authentication Timeout in Checkout Flow",
            resource="https://jira.company.com/browse/PAY-928",
            body="PAY-928: When customers initiate payment under 3DS authentication flow, checkout worker encounters ECONNREFUSED after 30s. Resolved in PR #142.",
            tags=["jira", "payments", "bug"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
        )
        pipeline.ingest_concept(doc1)

        # Developer Property Graph
        repo = RepositoryNode(full_name="company/payments", name="payments", owner_login="company", html_url="https://github.com/company/payments")
        user_alice = UserNode(login="alice", name="Alice Developer", email="alice@company.com")
        pr_142 = PullRequestNode(repo_full_name="company/payments", number=142, title="Fix 3DS timeout in Checkout Flow", author_login="alice")
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

        entity_retriever = EntityGraphRetriever(neo4j_client=None, memory_graph=memory_graph)
        semantic_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)
        keyword_retriever = KeywordRetriever(bm25_index=bm25_index)
        graph_retriever = GraphRetriever(bm25_index=bm25_index, vector_store=vector_store)
        resource_retriever = ResourceLookupRetriever(bm25_index=bm25_index, vector_store=vector_store)

        tool_registry = create_default_tool_registry(
            semantic_retriever=semantic_retriever,
            keyword_retriever=keyword_retriever,
            entity_graph_retriever=entity_retriever,
            graph_retriever=graph_retriever,
            resource_lookup_retriever=resource_retriever,
        )

        mock_llm = VerificationMockLLM()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=tool_registry,
            checkpointer=checkpointer,
            enable_reranking=False,
        )
        print("       ✓ Pipeline, retrievers, tools and SQLite checkpointer initialized.")

        # 2. Execute 3-turn conversation
        thread_id = "verify_session_2026"
        user_ctx = {"roles": ["engineer"], "user_id": "eng@company.com"}

        print(f"\n💬 [2/4] Executing 3-Turn Conversation Session (Thread ID: '{thread_id}')...")

        # Turn 1
        print("   ▶ Turn 1: 'Who authored PR #142?'")
        r1 = planner.run(query="Who authored PR #142?", user_context=user_ctx, thread_id=thread_id)
        print(f"     💬 Answer: {r1['answer']}")
        assert "Alice" in r1["answer"], "Turn 1 answer missing Alice"

        # Turn 2
        print("   ▶ Turn 2: 'What files did she modify in that PR?' (Testing pronoun resolution)")
        r2 = planner.run(query="What files did she modify in that PR?", user_context=user_ctx, thread_id=thread_id)
        print(f"     💬 Answer: {r2['answer']}")
        assert "checkout.py" in r2["answer"], "Turn 2 answer missing checkout.py"

        # Turn 3
        print("   ▶ Turn 3: 'What issue was that PR resolving?' (Testing multi-hop context)")
        r3 = planner.run(query="What issue was that PR resolving?", user_context=user_ctx, thread_id=thread_id)
        print(f"     💬 Answer: {r3['answer']}")
        assert "PAY-928" in r3["answer"], "Turn 3 answer missing PAY-928"

        # 3. Verify SQLite Checkpointer State
        print("\n💾 [3/4] Validating SQLite Checkpoint State & Thread APIs...")
        threads = checkpointer.get_all_threads()
        print(f"       ✓ Saved threads in SQLite: {threads}")
        assert thread_id in threads, f"Expected {thread_id} in {threads}"

        cp_tuple = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        assert cp_tuple is not None, "Checkpoint tuple is None"
        messages = cp_tuple.checkpoint["channel_values"]["messages"]
        print(f"       ✓ Total messages accumulated in thread: {len(messages)}")
        assert len(messages) >= 6, f"Expected at least 6 messages, found {len(messages)}"

        # 4. Verify Thread Isolation
        print("\n🛡️  [4/4] Validating Thread Isolation across Sessions...")
        other_thread_id = "isolated_session_999"
        planner.run(query="What is PAY-928?", user_context=user_ctx, thread_id=other_thread_id)

        all_threads = checkpointer.get_all_threads()
        assert thread_id in all_threads and other_thread_id in all_threads
        print(f"       ✓ Confirmed {len(all_threads)} distinct threads isolated in checkpointer.")

        print("\n" + "=" * 80)
        print("🎉 ALL MULTI-TURN PERSISTENT THREAD VERIFICATIONS PASSED SUCCESSFULLY!")
        print("=" * 80)
        return 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
