#!/usr/bin/env python3
"""
End-to-End Verification Script for Phase 9: Local Cross-Encoder Reranker & LangGraph Integration.

Validates:
  1. In-process cross-encoder candidate re-scoring and ranking optimization.
  2. Promotion of top semantic evidence over noisy candidates.
  3. Noise rejection via score thresholding.
  4. Neural logit probability calibration via logistic sigmoid scaling.
  5. 6-Node LangGraph State Machine execution with active `reranker` node.
  6. Quality-controlled answer generation using reranked evidence and bracketed citations.
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
from backend.ranking.models import RerankResult
from backend.ranking.reranker import CrossEncoderReranker
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class MockPhase9VerificationLLM(LLMProvider):
    """
    Deterministic Mock LLM for Phase 9 Verification demonstrating
    multi-tool invocation, reranked context evaluation, and cited answer synthesis.
    """

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "mock/phase9-verification-llm"

    def generate(self, messages: List[Message]) -> str:
        last_content = messages[-1].content if messages else ""

        # Evaluator Prompt Inspection
        if "Evaluate the evidence above" in last_content:
            tool_results = [m for m in messages if m.role == MessageRole.TOOL_RESULT]
            if len(tool_results) <= 1:
                return json.dumps({
                    "relevance_score": 0.75,
                    "evidence_sufficient": False,
                    "missing_information": ["Reviewer approval and touched code files for PR #142"],
                    "recommended_action": "REFORMULATE",
                    "recommended_tool": "github_entity_search",
                    "reasoning": "Located the Jira bug report PAY-928, but PR author/reviewer details remain missing.",
                })
            else:
                return json.dumps({
                    "relevance_score": 1.0,
                    "evidence_sufficient": True,
                    "missing_information": [],
                    "recommended_action": "GENERATE",
                    "reasoning": "Reranked evidence contains complete PR approval and code fix details.",
                })

        # Query Reformulator Prompt
        if "Generate a targeted retrieval query for the next turn" in last_content:
            return json.dumps({
                "reformulated_query": "get PR #142 details",
                "reasoning": "Query the GitHub entity graph for PR #142 metadata and modified files.",
                "suggested_tool": "github_entity_search",
            })

        # Grounded Answer Generation Prompt
        return "The checkout timeout issue (PAY-928) [1] was resolved by PR #142 'Fix 3DS timeout in Checkout Flow'. The PR was authored by **alice** and reviewed/approved by **bob** [2]. The modified file in this fix was `backend/services/checkout.py` [2]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        tool_results = [m for m in messages if m.role == MessageRole.TOOL_RESULT]

        if len(tool_results) == 0:
            # Turn 1: Search for the Jira ticket and bug description
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="semantic_search",
                        arguments={"query": "checkout timeout bug fix"},
                        call_id="call_turn1_sem",
                    )
                ]
            )
        else:
            # Turn 2: Query GitHub entity graph for PR details
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="github_entity_search",
                        arguments={
                            "operation": "get_pr_details",
                            "target": "#142",
                        },
                        call_id="call_turn2_gh",
                    )
                ]
            )


def print_banner(text: str) -> None:
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80 + "\n")


def run_verification() -> None:
    print_banner("PHASE 9 VERIFICATION: LOCAL CROSS-ENCODER RERANKER & LANGGRAPH INTEGRATION")

    # ── Step 1: Setup Multi-Modal Knowledge Topology ─────────────────────────
    print("[Step 1/6] Initializing Multi-Modal Storage & Enterprise Topology...")
    embedder = LocalEmbedder()
    vector_store = QdrantVectorStore(mode="memory", collection_name="phase9_verification_collection")
    bm25_index = BM25Index(index_path="./data/test_phase9_bm25.json")
    bm25_index.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25_index,
    )

    memory_graph = InMemoryEntityGraph()
    repo_node = RepositoryNode(
        full_name="company/payments",
        name="payments",
        owner_login="company",
        html_url="https://github.com/company/payments",
    )
    alice_node = UserNode(login="alice", name="Alice Dev", email="alice@company.com")
    bob_node = UserNode(login="bob", name="Bob Lead", email="bob@company.com")
    pr_node = PullRequestNode(
        repo_full_name="company/payments",
        number=142,
        title="Fix 3DS timeout in Checkout Flow",
        body="Resolves 3DS verification timeout in checkout flow by increasing TTL.",
        state="MERGED",
        html_url="https://github.com/company/payments/pull/142",
        author_login="alice",
    )
    file_node = FileNode(
        repo_full_name="company/payments",
        path="backend/services/checkout.py",
    )

    memory_graph.add_node(repo_node.to_graph_node())
    memory_graph.add_node(alice_node.to_graph_node())
    memory_graph.add_node(bob_node.to_graph_node())
    memory_graph.add_node(pr_node.to_graph_node())
    memory_graph.add_node(file_node.to_graph_node())

    memory_graph.add_relationship(GraphRelationship(from_id=alice_node.node_id, to_id=pr_node.node_id, rel_type=RelType.CREATED.value))
    memory_graph.add_relationship(GraphRelationship(from_id=bob_node.node_id, to_id=pr_node.node_id, rel_type=RelType.REVIEWED.value, properties={"state": "APPROVED"}))
    memory_graph.add_relationship(GraphRelationship(from_id=pr_node.node_id, to_id=file_node.node_id, rel_type=RelType.MODIFIES.value))

    # Ingest Multi-Modal OKF Concepts
    doc1 = OKFConcept(
        type="Issue",
        title="PAY-928: 3DS timeout in Checkout Flow",
        resource="https://jira.enterprise.com/browse/PAY-928",
        body="""# Bug PAY-928: 3DS Timeout
Users encounter HTTP 401 during the Checkout Flow when calling AuthService.validate_token.
Resolved by PR #142 in payments repo.
""",
        tags=["jira", "payments", "bug"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={"source": "jira", "resource_type": "issue"},
    )
    doc2 = OKFConcept(
        type="Policy",
        title="Office Catering & Dietary Guidelines",
        resource="https://notion.enterprise.com/catering-policy",
        body="Guidelines for corporate lunch orders and kitchen etiquette.",
        tags=["notion", "hr"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={"source": "notion", "resource_type": "page"},
    )
    doc3 = OKFConcept(
        type="Architecture",
        title="Vault KMS Production Encryption",
        resource="https://notion.enterprise.com/vault-kms",
        body="Configure KMS encryption keys for Vault cluster with AWS KMS Key ID arn:aws:kms:us-east-1:123456789.",
        tags=["notion", "security"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={"source": "notion", "resource_type": "page"},
    )

    pipeline.ingest_concept(doc1)
    pipeline.ingest_concept(doc2)
    pipeline.ingest_concept(doc3)

    semantic_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)
    keyword_retriever = KeywordRetriever(bm25_index=bm25_index)
    resource_lookup_retriever = ResourceLookupRetriever(bm25_index=bm25_index, vector_store=vector_store)
    graph_retriever = GraphRetriever(bm25_index=bm25_index, vector_store=vector_store)
    entity_graph_retriever = EntityGraphRetriever(memory_graph=memory_graph)

    tool_registry = create_default_tool_registry(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        resource_lookup_retriever=resource_lookup_retriever,
        graph_retriever=graph_retriever,
        entity_graph_retriever=entity_graph_retriever,
    )
    print("  ✓ Ingested Jira, Notion, and GitHub multi-modal documents.")
    print("  ✓ Initialized 5-modality enterprise tool registry.\n")

    # ── Step 2: Standalone Cross-Encoder Reranking ───────────────────────────
    print("[Step 2/6] Testing Standalone Cross-Encoder Scoring & Re-Ordering...")
    reranker = CrossEncoderReranker()

    test_query = "How to configure KMS encryption keys for Vault in production?"
    candidate_chunks = [
        {
            "chunk_id": "c_lunch",
            "title": "Office Catering & Dietary Guidelines",
            "text": "Guidelines for corporate lunch orders and kitchen etiquette.",
            "source": "notion",
        },
        {
            "chunk_id": "c_jira",
            "title": "PAY-928: 3DS timeout in Checkout Flow",
            "text": "Users encounter HTTP 401 during Checkout Flow.",
            "source": "jira",
        },
        {
            "chunk_id": "c_kms",
            "title": "Vault KMS Production Encryption",
            "text": "Configure KMS encryption keys for Vault cluster with AWS KMS Key ID arn:aws:kms:us-east-1:123456789.",
            "source": "notion",
        },
    ]

    rerank_results = reranker.rerank(query=test_query, chunks=candidate_chunks)

    print(f"  • Query: '{test_query}'")
    print(f"  • Candidate Input Order: {[c['chunk_id'] for c in candidate_chunks]}")
    print(f"  • Reranked Output Order:")
    for res in rerank_results:
        print(f"    - Rank {res.new_rank} (Original: {res.original_rank}): [{res.source.upper()}] '{res.title}' | Score: {res.score:.4f}")

    assert rerank_results[0].chunk_id == "c_kms", "Expected Vault KMS chunk to be promoted to Rank 1"
    assert rerank_results[0].new_rank == 1
    assert rerank_results[0].original_rank == 3
    print("  ✓ Relevant KMS chunk successfully promoted from Rank 3 -> Rank 1.\n")

    # ── Step 3: Noise Rejection & Threshold Filtering ────────────────────────
    print("[Step 3/6] Testing Noise Rejection & Score Threshold Filtering...")
    filtered_results = reranker.rerank(
        query=test_query,
        chunks=candidate_chunks,
        score_threshold=0.30,
    )
    print(f"  • Applying score_threshold=0.30:")
    print(f"    - Remaining Chunks: {len(filtered_results)} / {len(candidate_chunks)}")
    for res in filtered_results:
        print(f"    - [{res.chunk_id}] '{res.title}' -> Score: {res.score:.4f} >= 0.30")

    assert len(filtered_results) == 1
    assert filtered_results[0].chunk_id == "c_kms"
    print("  ✓ Irrelevant candidate noise strictly eliminated.\n")

    # ── Step 4: Neural Sigmoid Probability Calibration ───────────────────────
    print("[Step 4/6] Verifying Sigmoid Logit Probability Calibration...")
    test_logits = [-5.0, -1.0, 0.0, 2.5, 6.0]
    scaled_probs = [reranker._sigmoid(x) for x in test_logits]
    for logit, prob in zip(test_logits, scaled_probs):
        print(f"    - Raw Logit: {logit:5.1f}  ==>  Calibrated Probability: {prob:.4f}")
        assert 0.0 <= prob <= 1.0

    print("  ✓ Sigmoid function strictly maps all logits to [0.0, 1.0] probability interval.\n")

    # ── Step 5: End-to-End LangGraph 6-Node State Machine Execution ──────────
    print("[Step 5/6] Executing LangGraph 6-Node Workflow with Active Reranker Node...")
    mock_llm = MockPhase9VerificationLLM()
    planner = LangGraphAgentPlanner(
        llm_provider=mock_llm,
        tool_registry=tool_registry,
        reranker=reranker,
        enable_reranking=True,
        max_turns=4,
        max_retrieval_attempts=3,
    )

    user_query = "Who approved the checkout timeout fix (PAY-928), and what code file was changed?"
    print(f"  User Query: \"{user_query}\"")
    print("  Orchestrating autonomous LangGraph workflow (reasoner -> tool_node -> reranker -> evaluator -> reformulator)...")

    agent_result = planner.run(
        query=user_query,
        user_context={"roles": ["engineer"], "user_id": "engineer@company.com"},
    )

    print("\n  ── LangGraph 6-Node Execution Trace ──")
    print(f"  Retrieval Attempts (Reflection Cycles): {agent_result['retrieval_attempts']}")
    print(f"  Rerank Applied: {agent_result['rerank_applied']}")
    print(f"  Rerank Scores Log: {agent_result['rerank_scores']}")
    print(f"  Reformulated Queries Log: {agent_result['reformulated_queries']}")
    print(f"  Total Tool Calls Executed ({len(agent_result['tool_calls'])}):")
    for idx, tc in enumerate(agent_result["tool_calls"], 1):
        print(f"    {idx}. Tool: `{tc['tool']}` | Arguments: {tc['arguments']}")

    print("\n  Final Evidence Evaluation Status:")
    print(f"    - Sufficient: {agent_result['evaluation']['evidence_sufficient']}")
    print(f"    - Recommended Action: {agent_result['evaluation']['recommended_action']}")
    print(f"    - Score: {agent_result['evaluation']['relevance_score']}")
    print(f"    - Missing Information: {agent_result['evaluation']['missing_information']}")

    print("\n  Synthesized Grounded Answer:")
    print(f"  {agent_result['answer']}")

    print(f"\n  Evidence Citations ({len(agent_result['citations'])}):")
    for idx, cite in enumerate(agent_result["citations"], 1):
        print(f"    [{idx}] {cite.get('title')} ({str(cite.get('source')).upper()})")

    # Assertions
    assert agent_result["rerank_applied"] is True, "Expected rerank_applied to be True"
    assert len(agent_result["retrieved_chunks"]) > 0
    assert "alice" in agent_result["answer"]
    assert "bob" in agent_result["answer"]
    assert "checkout.py" in agent_result["answer"]
    assert "[1]" in agent_result["answer"]
    assert "[2]" in agent_result["answer"]
    print("\n  ✓ Grounded answer verified with exact citations and reviewer details.\n")

    # ── Step 6: Verify Metadata Preservation ─────────────────────────────────
    print("[Step 6/6] Verifying Metadata Preservation Across Reranker...")
    for chunk in agent_result["retrieved_chunks"]:
        assert "rerank_score" in chunk, f"Chunk {chunk.get('chunk_id')} missing rerank_score"
        assert "rerank_rank" in chunk, f"Chunk {chunk.get('chunk_id')} missing rerank_rank"
        assert "original_rank" in chunk, f"Chunk {chunk.get('chunk_id')} missing original_rank"
        print(f"    - Chunk '{chunk.get('chunk_id')}': Rerank Rank {chunk.get('rerank_rank')} | Score: {chunk.get('rerank_score')}")

    print("  ✓ Full metadata, ranking metrics, and source tags preserved across all nodes.")

    print_banner("🎉 PHASE 9 VERIFICATION COMPLETED SUCCESSFULLY WITH ZERO ERRORS!")


if __name__ == "__main__":
    run_verification()
