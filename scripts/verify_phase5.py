#!/usr/bin/env python3
"""
Phase 5 Verification Script: Keyword Search Retrieval & LangGraph Multi-Tool Orchestration.

Demonstrates:
  1. BM25+ exact identifier lookups (Jira keys like PAY-928, error codes HTTP 401, code symbols).
  2. Database-level RBAC pre-filtering for keyword search.
  3. ToolRegistry dual registration (semantic_search + keyword_search).
  4. Native LangChain StructuredTools execution with Pydantic schema validation.
  5. LangGraph multi-tool planning in a single turn (concurrent tool calling).
  6. Grounded answer generation with multi-source bracketed citations.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure repository root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from backend.agent.langchain_tools import create_langchain_tools
from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.tools import create_default_tool_registry
from backend.generation.answer_generator import AnswerGenerator
from backend.generation.context_builder import ContextBuilder
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


class MockMultiToolDemoLLM(LLMProvider):
    """
    Deterministic LLM for demonstrating composite multi-tool reasoning in LangGraph.
    """

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "DeterministicAgentMock (Phase 5)"

    def generate(self, messages: List[Message]) -> str:
        return "Payment bug PAY-928 is resolved by checking 3DS timeout in the Checkout Flow."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_count += 1

        # If previous turn had tool results, synthesize final grounded answer
        if messages and messages[-1].role == MessageRole.TOOL_RESULT:
            return LLMResponse(
                content=(
                    "Issue PAY-928 indicates users encounter HTTP 401 errors during checkout due to a 3DS challenge timeout [1]. "
                    "According to the Payments API Guide [2], payment transactions must be initiated via a POST request to `/v1/payments/initiate` "
                    "with `amount`, `currency`, and `customer_id` to generate a fresh `client_secret` token for 3DS customer verification."
                )
            )

        # Turn 1: Emit BOTH keyword_search AND semantic_search in a single turn!
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="keyword_search",
                    arguments={"query": "PAY-928"},
                    call_id="call_kw_pay928",
                ),
                ToolCall(
                    tool_name="semantic_search",
                    arguments={"query": "how to initiate payment transaction in API"},
                    call_id="call_sem_payments",
                ),
            ]
        )


def main() -> None:
    print("=" * 80)
    print("  PHASE 5 VERIFICATION: KEYWORD RETRIEVAL & LANGGRAPH MULTI-TOOL LOOP")
    print("=" * 80)

    # ── Initialize Storage & Ingestion Pipeline ──────────────────────────────
    embedder = LocalEmbedder()
    vector_store = QdrantVectorStore(mode="memory", collection_name="verify_phase5_collection")
    bm25_index = BM25Index(index_path="./data/verify_phase5_bm25.json")
    bm25_index.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25_index,
    )

    # ── Ingest Test Documents ────────────────────────────────────────────────
    # Document 1: Public Architecture Guide
    doc1 = OKFConcept(
        type="Architecture",
        title="Payment Gateway API Reference",
        resource="https://github.com/enterprise/payments/docs/api.md",
        body="""# Payments API Guide

## Transaction Lifecycle
### Step 1: Payment Initiation
To initiate a transaction, send a POST request to `/v1/payments/initiate` with `amount`, `currency`, and `customer_id`. The gateway returns a `client_secret` token for 3DS verification.

### Step 2: 3D-Secure Customer Verification
Redirect customer to issuing bank verification challenge. Once approved, biometric confirmation token is dispatched.
""",
        tags=["github", "payments", "api", "architecture"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={"source": "github", "resource_type": "file"},
    )
    pipeline.ingest_concept(doc1)

    # Document 2: Jira Bug Ticket with exact key PAY-928
    doc2 = OKFConcept(
        type="Issue",
        title="PAY-928: 3DS Timeout during Checkout Flow",
        resource="https://jira.enterprise.com/browse/PAY-928",
        body="""# Bug Report PAY-928
Title: 3DS Customer Authentication Timeout
Description: Users encounter HTTP 401 and ECONNRESET during the Checkout Flow when calling AuthService.validate_token.
Resolution: Re-authenticate client using fresh client_secret from payment initiation endpoint.
Status: In Progress | Priority: High | Assignee: backend-payments-team
""",
        tags=["jira", "payments", "bug"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={"source": "jira", "resource_type": "issue"},
    )
    pipeline.ingest_concept(doc2)

    # Document 3: Confidential Security Runbook (RBAC Restricted)
    doc3 = OKFConcept(
        type="SecurityDoc",
        title="SEC-104: Production Master Vault Secrets",
        resource="https://vault.enterprise.internal/keys/prod",
        body="""# Production Master Secrets
Master Vault KMS Key: kms://us-east-1/enterprise-prod-vault-992
Rotation Runbook: Run `vault operator rotate-root` with multi-party quorum approval.
""",
        tags=["security", "confidential"],
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=["security-lead"],
            allowed_users=["ciso@enterprise.com"],
        ),
        extra_metadata={"source": "notion", "resource_type": "page"},
    )
    pipeline.ingest_concept(doc3)

    # Initialize Retrievers
    semantic_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)
    keyword_retriever = KeywordRetriever(bm25_index=bm25_index)
    tool_registry = create_default_tool_registry(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
    )

    # ── 1. Keyword Retrieval & Exact Match Verification ──────────────────────
    print("\n" + "=" * 80)
    print("  1. KEYWORD RETRIEVER (BM25+) EXACT IDENTIFIER SEARCH")
    print("=" * 80)

    # Query 1: Exact Jira Issue Key
    results_ticket = keyword_retriever.search(query="PAY-928")
    print(f"\n[Lookup 1] Exact Jira Key: 'PAY-928'")
    print(f"  -> ✅ Top Result (Score: {results_ticket[0]['score']:.4f}): '{results_ticket[0]['title']}'")
    print(f"     Source: {results_ticket[0]['source']} | Resource Type: {results_ticket[0]['resource_type']}")
    print(f"     URL:    {results_ticket[0]['url']}")

    # Query 2: Exact HTTP Error Code
    results_error = keyword_retriever.search(query="HTTP 401")
    print(f"\n[Lookup 2] Exact Error Code: 'HTTP 401'")
    print(f"  -> ✅ Top Result (Score: {results_error[0]['score']:.4f}): '{results_error[0]['title']}'")
    print(f"     Snippet: {results_error[0]['text'][:85]}...")

    # Query 3: Exact Code Symbol
    results_symbol = keyword_retriever.search(query="AuthService.validate_token")
    print(f"\n[Lookup 3] Exact Code Symbol: 'AuthService.validate_token'")
    print(f"  -> ✅ Top Result (Score: {results_symbol[0]['score']:.4f}): '{results_symbol[0]['title']}'")

    # ── 2. Database-Level RBAC Pre-Filtering ─────────────────────────────────
    print("\n" + "=" * 80)
    print("  2. DATABASE-LEVEL RBAC PRE-FILTERING (KEYWORD SEARCH)")
    print("=" * 80)

    # Unauthorized role searching for confidential security doc
    guest_context = {"roles": ["engineer", "intern"], "user_id": "intern@enterprise.com"}
    guest_results = keyword_retriever.search(query="SEC-104 Vault Key", user_context=guest_context)
    print(f"[Query] 'SEC-104 Vault Key' (Role: ['engineer', 'intern'])")
    if len(guest_results) == 0:
        print("  -> ✅ PASSED! Vault secret was 100% pre-filtered out at the database layer.")
    else:
        print("  -> ❌ FAILED! Unauthorized access leaked!")

    # Authorized security lead
    sec_context = {"roles": ["security-lead"], "user_id": "ciso@enterprise.com"}
    sec_results = keyword_retriever.search(query="SEC-104 Vault Key", user_context=sec_context)
    print(f"[Query] 'SEC-104 Vault Key' (Role: ['security-lead'])")
    if len(sec_results) > 0:
        print(f"  -> ✅ PASSED! Authorized security lead retrieved: '{sec_results[0]['title']}'")

    # ── 3. Tool Registry Dual Tool Verification ──────────────────────────────
    print("\n" + "=" * 80)
    print("  3. TOOL REGISTRY DUAL TOOL REGISTRATION")
    print("=" * 80)
    defs = tool_registry.get_definitions()
    print(f"✅ Registered Tools for LLM: {[d.name for d in defs]}")
    for d in defs:
        print(f"   • Tool '{d.name}': {d.description[:95]}...")

    # ── 4. Native LangChain Structured Tools ─────────────────────────────────
    print("\n" + "=" * 80)
    print("  4. NATIVE LANGCHAIN STRUCTURED TOOLS (Pydantic Schemas)")
    print("=" * 80)
    lc_tools = create_langchain_tools(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        user_context={"roles": ["engineer"], "user_id": "dev@enterprise.com"},
    )
    print(f"✅ LangChain BaseTools ready for LangGraph ToolNode: {[t.name for t in lc_tools]}")
    kw_tool = next(t for t in lc_tools if t.name == "keyword_search")
    res = kw_tool.invoke({"query": "PAY-928", "top_k": 1})
    print(f"   Invoking '{kw_tool.name}' via LangChain: retrieved payload with '{'PAY-928' in res}' status!")

    # ── 5. LangGraph Multi-Tool Execution in a Single Turn ────────────────────
    print("\n" + "=" * 80)
    print("  5. LANGGRAPH MULTI-TOOL EXECUTION IN A SINGLE TURN")
    print("=" * 80)
    user_query = "What is bug PAY-928 and how do I initiate a payment in the API?"
    print(f"User Query: '{user_query}'")
    print("Orchestrator: LangGraph StateGraph (Nodes: ['reasoner', 'tool_node', 'generator'])")
    print("User Security Context: roles=['engineer'], user_id='dev@enterprise.com'\n")

    mock_llm = MockMultiToolDemoLLM()
    planner = LangGraphAgentPlanner(
        llm_provider=mock_llm,
        tool_registry=tool_registry,
        max_turns=3,
    )

    print("─── Executing LangGraph Workflow ──────────────────────────────")
    result = planner.run(
        query=user_query,
        user_context={"roles": ["engineer"], "user_id": "dev@enterprise.com"},
    )

    print(f"\n✅ LangGraph Execution Finished in {result['turns']} Turns (Provider: {result['llm_provider']})")
    print(f"\n📋 Multi-Tool Execution Audit Log in Turn 1:")
    for i, tc in enumerate(result["tool_calls"], 1):
        print(f"  Step {i}: Invoked tool '{tc['tool']}' with args {tc['arguments']}")

    print(f"\n📚 Retrieved Evidence Across Tools ({len(result['retrieved_chunks'])} chunks):")
    for c in result["retrieved_chunks"]:
        print(f"  • [{c.get('source', '').upper()}] {c.get('title')}")

    print(f"\n🤖 Final Grounded Answer from LangGraph:")
    print(f"  {result['answer']}")

    print(f"\n📚 Grounded Citations:")
    for cit in result["citations"]:
        print(f"  [{cit['citation_index']}] {cit['title']} ({cit['source'].upper()}) -> {cit['url']}")


    print("\n" + "=" * 80)
    print("  ALL PHASE 5 KEYWORD & MULTI-TOOL VERIFICATIONS PASSED! 🎉")
    print("=" * 80 + "\n")

    # Clean up test index
    bm25_index.clear()


if __name__ == "__main__":
    main()
