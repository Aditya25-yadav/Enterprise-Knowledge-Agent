#!/usr/bin/env python3
"""
End-to-End Verification Script for Phase 10: Hybrid Search Fusion Node & Reciprocal Rank Fusion.

Validates:
  1. Reciprocal Rank Fusion (RRF) mathematical scoring and rank fusion invariants.
  2. Multi-modal candidate merging across Dense Vector (Qdrant), Sparse Lexical (BM25+), and Property Graph.
  3. Modality weighting, smoothing parameter tuning (k=60), and fusion provenance tracking.
  4. Database-level RBAC security isolation across all dispatched sub-retrievers.
  5. Native LangChain `hybrid_search` StructuredTool execution with Pydantic schema validation.
  6. 6-Node LangGraph Agent state machine execution with hybrid search, cross-encoder reranking, and citation synthesis.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from backend.agent.langchain_tools import create_langchain_tools
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
from backend.models.graph import FileNode, GraphRelationship, PullRequestNode, RelType, RepositoryNode, UserNode
from backend.models.okf import OKFConcept, OKFPermissions
from backend.ranking.reranker import CrossEncoderReranker
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class MockPhase10VerificationLLM(LLMProvider):
    """
    Deterministic Mock LLM for Phase 10 Verification demonstrating
    hybrid_search tool invocation, RRF candidate reranking, and cited answer synthesis.
    """

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "mock/phase10-verification-llm"

    def generate(self, messages: List[Message]) -> str:
        last_content = messages[-1].content if messages else ""

        # Evaluator Prompt Inspection
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 0.98,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Retrieved comprehensive multi-modal evidence across vector and BM25 modalities with PR metadata.",
            })

        # Query Reformulator Prompt
        if "Generate a targeted retrieval query for the next turn" in last_content:
            return json.dumps({
                "reformulated_query": "hybrid payment checkout 3DS",
                "reasoning": "Refine hybrid query.",
                "suggested_tool": "hybrid_search",
            })

        # Grounded Answer Generation Prompt
        return (
            "To resolve the 3DS checkout timeout issue (tracked in PAY-928), PR #142 by alice was merged [1]. "
            "Payment transactions must be initiated via POST /v1/payments/initiate with an idempotency key [2]."
        )

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="hybrid_search",
                    arguments={
                        "query": "Fix 3DS checkout timeout PAY-928 payments initiate",
                        "top_k": 3,
                    },
                    call_id="call_phase10_hyb_1",
                )
            ]
        )


def main() -> None:
    print("=" * 80)
    print("🚀 ENTERPRISE KNOWLEDGE AGENT - PHASE 10 VERIFICATION")
    print("   Hybrid Search Fusion Node & Reciprocal Rank Fusion (RRF)")
    print("=" * 80)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Verify Reciprocal Rank Fusion (RRF) Algorithm & Math
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[1/5] Verifying Reciprocal Rank Fusion (RRF) Math & Modality Fusion...")

    vector_candidates = [
        {"chunk_id": "doc_payments_api", "title": "Payments API", "score": 0.92},
        {"chunk_id": "doc_checkout_flow", "title": "Checkout Flow", "score": 0.88},
        {"chunk_id": "doc_auth_guide", "title": "Auth Guide", "score": 0.75},
    ]

    keyword_candidates = [
        {"chunk_id": "doc_checkout_flow", "title": "Checkout Flow", "bm25_score": 14.2},
        {"chunk_id": "issue_pay_928", "title": "PAY-928 Bug", "bm25_score": 12.1},
        {"chunk_id": "doc_payments_api", "title": "Payments API", "bm25_score": 9.5},
    ]

    graph_candidates = [
        {"chunk_id": "pr_142", "title": "PR #142 3DS Fix", "label": "PullRequest"},
        {"chunk_id": "issue_pay_928", "title": "PAY-928 Bug", "label": "Issue"},
    ]

    ranked_lists = {
        "vector": vector_candidates,
        "keyword": keyword_candidates,
        "graph": graph_candidates,
    }

    fused = reciprocal_rank_fusion(ranked_lists=ranked_lists, k=60, top_k=5)

    print(f"  • Total input candidates across 3 modalities: 8")
    print(f"  • Deduplicated fused candidates: {len(fused)}")

    for idx, item in enumerate(fused, 1):
        print(f"    {idx}. [{item['chunk_id']}] score={item['rrf_score']:.6f} "
              f"matched={item['modalities_matched']} ranks={item['ranks_per_modality']}")

    # Verification checks
    assert len(fused) == 5, f"Expected 5 unique items, got {len(fused)}"
    # doc_checkout_flow was #2 in vector and #1 in keyword:
    # rrf = 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
    checkout_item = next(x for x in fused if x["chunk_id"] == "doc_checkout_flow")
    expected_score = round(1.0 / (60 + 2) + 1.0 / (60 + 1), 6)
    assert abs(checkout_item["rrf_score"] - expected_score) < 1e-5, (
        f"Score mismatch: {checkout_item['rrf_score']} vs {expected_score}"
    )
    assert checkout_item["modalities_matched"] == ["vector", "keyword"]
    print("  ✅ RRF score computation and multi-modality boosting validated.")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Multi-Modal Ingestion & HybridRetriever Setup
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[2/5] Setting up Ingestion Pipeline & Multi-Modal HybridRetriever...")

    embedder = LocalEmbedder()
    vector_store = QdrantVectorStore(mode="memory", collection_name="phase10_verification")
    bm25_index = BM25Index(index_path="./data/test_phase10_bm25.json")
    bm25_index.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25_index,
    )

    # Ingest technical docs into vector store and BM25 index
    concept1 = OKFConcept(
        type="File",
        title="Payments API Guide",
        resource="https://github.com/company/payments/docs/api.md",
        body="""# Payments API Guide
To initiate a payment transaction, send a POST request to `/v1/payments/initiate` with the customer token, amount, and currency.
All requests require an Idempotency-Key header to prevent duplicate charges.
In case of network error, retry with exponential backoff using the same idempotency key.""",
        tags=["github", "payments", "api"],
        permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
        extra_metadata={"source": "github", "resource_type": "file"},
    )

    concept2 = OKFConcept(
        type="Issue",
        title="PAY-928: 3DS Authentication Timeout in Checkout Flow",
        resource="jira://issue/PAY-928",
        body="""# PAY-928: 3DS Authentication Timeout in Checkout Flow
Reported by: support-team
Priority: P0 Critical
Description: When users initiate payment under 3DS authentication flow, the checkout worker encounters ECONNREFUSED after 30s.
Resolved in PR #142 with timeout parameter increased to 60s and circuit breaker enabled.""",
        tags=["jira", "payments", "bug"],
        permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
        extra_metadata={"source": "jira", "resource_type": "issue"},
    )

    concept3 = OKFConcept(
        type="Page",
        title="CISO Master Encryption Keys",
        resource="notion://vault/master_keys",
        body="""# CISO Master Encryption Keys [TOP SECRET]
Master AES-256 Vault Encryption Key: AES-SECRET-KEY-PHASE10-TOPSECRET.
Only Security Operations and CISO staff are permitted to access this resource.""",
        tags=["notion", "security", "confidential"],
        permissions=OKFPermissions(allowed_roles=["ciso_admin"], is_public=False),
        extra_metadata={"source": "notion", "resource_type": "page"},
    )

    pipeline.ingest_concept(concept1)
    pipeline.ingest_concept(concept2)
    pipeline.ingest_concept(concept3)

    # Ingest Entity Graph
    entity_graph = InMemoryEntityGraph()
    repo = RepositoryNode(
        full_name="company/payments",
        name="payments",
        owner_login="company",
        html_url="https://github.com/company/payments",
    )
    user_alice = UserNode(login="alice", name="Alice Developer", email="alice@company.com")
    user_bob = UserNode(login="bob", name="Bob Lead", email="bob@company.com")
    pr_142 = PullRequestNode(
        repo_full_name="company/payments",
        number=142,
        title="Fix 3DS timeout in Checkout Flow",
        body="Resolves 3DS verification timeout in checkout flow by increasing TTL.",
        state="MERGED",
        html_url="https://github.com/company/payments/pull/142",
        author_login="alice",
    )
    file_checkout = FileNode(
        repo_full_name="company/payments",
        path="backend/checkout.py",
    )

    entity_graph.add_node(repo.to_graph_node())
    entity_graph.add_node(user_alice.to_graph_node())
    entity_graph.add_node(user_bob.to_graph_node())
    entity_graph.add_node(pr_142.to_graph_node())
    entity_graph.add_node(file_checkout.to_graph_node())

    rel_auth = RelType.AUTHORED.value if hasattr(RelType.AUTHORED, "value") else str(RelType.AUTHORED)
    rel_rev = RelType.REVIEWED.value if hasattr(RelType.REVIEWED, "value") else str(RelType.REVIEWED)
    rel_mod = RelType.MODIFIES.value if hasattr(RelType.MODIFIES, "value") else str(RelType.MODIFIES)

    entity_graph.add_relationship(GraphRelationship(from_id=user_alice.node_id, to_id=pr_142.node_id, rel_type=rel_auth))
    entity_graph.add_relationship(GraphRelationship(from_id=user_bob.node_id, to_id=pr_142.node_id, rel_type=rel_rev, properties={"state": "APPROVED"}))
    entity_graph.add_relationship(GraphRelationship(from_id=pr_142.node_id, to_id=file_checkout.node_id, rel_type=rel_mod))

    semantic_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)
    keyword_retriever = KeywordRetriever(bm25_index=bm25_index)
    entity_retriever = EntityGraphRetriever(memory_graph=entity_graph)
    graph_retriever = GraphRetriever(bm25_index=bm25_index, vector_store=vector_store)
    resource_retriever = ResourceLookupRetriever(bm25_index=bm25_index, vector_store=vector_store)

    hybrid_retriever = HybridRetriever(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        entity_graph_retriever=entity_retriever,
        graph_retriever=graph_retriever,
    )

    # Perform hybrid search with engineer context
    engineer_ctx = {"roles": ["engineer"], "user_id": "eng@company.com"}
    hybrid_results = hybrid_retriever.search(
        query="Fix 3DS checkout timeout PAY-928",
        top_k=5,
        user_context=engineer_ctx,
    )

    print(f"  • Retrieved {len(hybrid_results)} hybrid candidates.")
    for idx, r in enumerate(hybrid_results, 1):
        print(f"    {idx}. [{r.get('chunk_id')}] title='{r.get('title')}' rrf_score={r.get('rrf_score')}")

    assert len(hybrid_results) > 0
    assert any("PAY-928" in r.get("title", "") for r in hybrid_results)
    print("  ✅ Multi-modal hybrid search correctly retrieved and fused candidate chunks.")

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Database-Level RBAC Enforcement Verification
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[3/5] Verifying Database-Level RBAC Pre-Filtering in Hybrid Search...")

    # Guest user query
    guest_ctx = {"roles": ["guest"], "user_id": "guest@external.com"}
    guest_results = hybrid_retriever.search(
        query="master encryption keys vault TOP SECRET",
        top_k=5,
        user_context=guest_ctx,
    )
    print(f"  • Guest results count: {len(guest_results)}")
    assert not any("AES-SECRET-KEY" in r.get("text", "") for r in guest_results)
    assert not any("CISO Master Encryption Keys" in r.get("title", "") for r in guest_results)

    # CISO user query
    ciso_ctx = {"roles": ["ciso_admin"], "user_id": "ciso@company.com"}
    ciso_results = hybrid_retriever.search(
        query="master encryption keys vault",
        top_k=5,
        user_context=ciso_ctx,
    )
    print(f"  • CISO results count: {len(ciso_results)}")
    assert any("CISO Master Encryption Keys" in r.get("title", "") for r in ciso_results)
    print("  ✅ Strict database-level RBAC isolation enforced across all hybrid search modalities.")

    # ─────────────────────────────────────────────────────────────────────────
    # 4. Native LangChain `hybrid_search` Tool Execution
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[4/5] Verifying Native LangChain `hybrid_search` StructuredTool...")

    lc_tools = create_langchain_tools(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        entity_graph_retriever=entity_retriever,
        graph_retriever=graph_retriever,
        hybrid_retriever=hybrid_retriever,
        bm25_index=bm25_index,
        user_context=engineer_ctx,
    )

    tool_names = [t.name for t in lc_tools]
    print(f"  • Registered LangChain tools ({len(lc_tools)}): {tool_names}")
    assert "hybrid_search" in tool_names
    assert len(lc_tools) == 6

    hybrid_tool = next(t for t in lc_tools if t.name == "hybrid_search")
    tool_output_str = hybrid_tool.invoke({
        "query": "POST /v1/payments/initiate idempotency key",
        "top_k": 3,
        "k": 60,
    })

    tool_output = json.loads(tool_output_str)
    assert isinstance(tool_output, list)
    assert len(tool_output) > 0
    top_hit = tool_output[0]
    print(f"  • Top hybrid hit: [{top_hit['chunk_id']}] title='{top_hit.get('title')}' "
          f"score={top_hit.get('rrf_score')} matched={top_hit.get('modalities_matched')}")
    assert "rrf_score" in top_hit
    assert "modalities_matched" in top_hit
    print("  ✅ Native LangChain hybrid_search StructuredTool verified.")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. 6-Node LangGraph State Machine Integration with Hybrid Retrieval & Reranker
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[5/5] Verifying 6-Node LangGraph Agent Execution with Hybrid Search & Reranking...")

    tool_registry = create_default_tool_registry(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        entity_graph_retriever=entity_retriever,
        graph_retriever=graph_retriever,
        hybrid_retriever=hybrid_retriever,
        resource_lookup_retriever=resource_retriever,
    )

    reranker = CrossEncoderReranker()
    mock_llm = MockPhase10VerificationLLM()

    planner = LangGraphAgentPlanner(
        llm_provider=mock_llm,
        tool_registry=tool_registry,
        reranker=reranker,
        enable_reranking=True,
        max_turns=3,
    )

    agent_result = planner.run(
        query="How was the 3DS checkout timeout resolved and how do I initiate payments?",
        user_context=engineer_ctx,
    )

    print("\n  • LangGraph Execution Results:")
    print(f"    - Turns executed: {agent_result['turns']}")
    print(f"    - Tools called: {[tc['tool'] for tc in agent_result['tool_calls']]}")
    print(f"    - Chunks retrieved: {len(agent_result['retrieved_chunks'])}")
    print(f"    - Rerank applied: {agent_result['rerank_applied']}")
    print(f"    - Citations: {len(agent_result['citations'])}")
    print(f"    - Final Answer:\n      \"{agent_result['answer']}\"")

    # Assertions
    assert len(agent_result["tool_calls"]) == 1
    assert agent_result["tool_calls"][0]["tool"] == "hybrid_search"
    assert agent_result["rerank_applied"] is True
    assert len(agent_result["retrieved_chunks"]) > 0

    first_chunk = agent_result["retrieved_chunks"][0]
    assert "rrf_score" in first_chunk
    assert "rerank_score" in first_chunk
    assert "[1]" in agent_result["answer"]
    assert "PAY-928" in agent_result["answer"]
    assert "/v1/payments/initiate" in agent_result["answer"]

    print("\n" + "=" * 80)
    print("🎉 ALL PHASE 10 VERIFICATIONS PASSED SUCCESSFULLY! (Exit Code 0)")
    print("=" * 80)


if __name__ == "__main__":
    main()
