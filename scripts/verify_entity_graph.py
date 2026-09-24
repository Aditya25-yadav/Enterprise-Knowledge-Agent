#!/usr/bin/env python3
"""
Verification Script: Generalized Entity Graph & Developer Intelligence in LangGraph.

Demonstrates:
  1. Generalized Property Graph Engine (InMemoryEntityGraph & Neo4j compatibility).
  2. Generalized Graph Operations:
     - get_entity: Direct entity node lookup.
     - get_neighbors: Directional multi-hop BFS expansion with relation filters.
     - search_nodes: Label, property, and text search across the property graph.
     - find_path: Shortest path discovery between arbitrary graph entities.
  3. GitHub Developer Intelligence Domain Shortcuts:
     - get_pr_details: PR metadata, author, reviewers, and modified files.
     - get_user_activity: Developer 360 view (PRs, commits, reviews, issues).
     - get_file_contributors: Code ownership, commit history, and touched PRs.
     - get_issue_details: Issue state, reporter, assignees, labels, and closing PRs.
     - get_team_overview: Team members and repository access permissions.
  4. Native LangChain 5-Tool Suite (semantic, keyword, resource_lookup, graph_traversal, github_entity_search).
  5. LangGraph Autonomous State Machine Reasoning Loop with Developer Intelligence.
"""

from __future__ import annotations

import json
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
from backend.models.graph import (
    CommitNode,
    FileNode,
    GraphRelationship,
    IssueNode,
    LabelNode,
    NodeLabel,
    PullRequestNode,
    RelType,
    RepositoryNode,
    TeamNode,
    UserNode,
)
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class MockEntityGraphLangGraphLLM(LLMProvider):
    """
    Deterministic mock LLM demonstrating multi-hop reasoning combining
    unstructured documents (semantic_search) and developer graph intelligence (github_entity_search).
    
    Turn 1: Semantic search to locate Payments API Guide.
    Turn 2: GitHub Entity Search (get_pr_details) to find who authored and reviewed the 3DS fix.
    Turn 3: Grounded final answer synthesizing both documentation and GitHub graph intelligence.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "DeterministicEntityGraphAgentMock"

    def generate(self, messages: List[Message]) -> str:
        return "Grounded answer synthesized with citations [1] [2]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)

        tool_results = [m for m in messages if m.role == MessageRole.TOOL_RESULT]
        if len(tool_results) == 0:
            # Turn 1: Search payments documentation
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="semantic_search",
                        arguments={"query": "payment intent initiate 3DS verification timeout"},
                        call_id="call_hop1_sem",
                    )
                ]
            )
        elif len(tool_results) == 1:
            # Turn 2: Lookup PR #142 details to discover author, reviewers, and code changes
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="github_entity_search",
                        arguments={
                            "operation": "get_pr_details",
                            "target": "#142",
                        },
                        call_id="call_hop2_gh",
                    )
                ]
            )
        else:
            # Turn 3: Final grounded answer
            return LLMResponse(
                content=(
                    "Per the Payments API Guide [1], transactions are initiated via POST `/v1/payments/initiate`. "
                    "To resolve the 3DS verification timeout in the checkout flow, PR #142 ('Fix 3DS timeout in Checkout Flow') "
                    "was authored by @alice and reviewed/approved by @bob [2], modifying `backend/services/checkout.py`."
                )
            )


def main() -> None:
    print("=" * 80)
    print("Enterprise Knowledge Agent - Generalized Entity Graph Verification")
    print("=" * 80)

    # 1. Setup in-memory indexes and vector storage
    print("\n[Step 1] Initializing Vector Store, BM25 Index, and Entity Graph...")
    embedder = LocalEmbedder()
    vector_store = QdrantVectorStore(mode="memory", collection_name="verify_entity_graph")
    bm25_index = BM25Index(index_path="./data/verify_entity_graph_bm25.json")
    bm25_index.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25_index,
    )

    # Ingest OKF Documentation
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
    pipeline.ingest_concept(doc1)

    # 2. Build Rich GitHub Entity Graph
    print("\n[Step 2] Building Rich GitHub Entity Graph Topology...")
    memory_graph = InMemoryEntityGraph()
    entity_retriever = EntityGraphRetriever(memory_graph=memory_graph)

    repo = RepositoryNode(
        full_name="company/payments",
        name="payments",
        owner_login="company",
        html_url="https://github.com/company/payments",
        description="Core payment processing gateway and checkout services.",
    )
    alice = UserNode(login="alice", name="Alice Dev", email="alice@company.com")
    bob = UserNode(login="bob", name="Bob Lead", email="bob@company.com")
    carol = UserNode(login="carol", name="Carol SRE", email="carol@company.com")
    team = TeamNode(org_login="company", slug="core-payments", name="Core Payments Team")
    pr142 = PullRequestNode(
        repo_full_name="company/payments",
        number=142,
        title="Fix 3DS timeout in Checkout Flow",
        body="Resolves 3DS verification timeout in checkout flow by increasing TTL.",
        state="MERGED",
        html_url="https://github.com/company/payments/pull/142",
        author_login="alice",
    )
    issue928 = IssueNode(
        repo_full_name="company/payments",
        number=928,
        title="3DS timeout in Checkout Flow",
        state="closed",
        author_login="carol",
        body="Customers report HTTP 401 when 3DS token expires prematurely.",
    )
    commit = CommitNode(
        repo_full_name="company/payments",
        sha="a1b2c3d4e5f67890",
        message="fix(checkout): increase 3DS token timeout to 300s",
        author_login="alice",
    )
    file_checkout = FileNode(
        repo_full_name="company/payments",
        path="backend/services/checkout.py",
    )
    label_bug = LabelNode(
        repo_full_name="company/payments",
        name="bug",
        color="d73a4a",
    )

    for n in [repo, alice, bob, carol, team, pr142, issue928, commit, file_checkout, label_bug]:
        memory_graph.add_node(n.to_graph_node())

    # Add Relationships
    memory_graph.add_relationship(GraphRelationship(from_id=alice.node_id, to_id=pr142.node_id, rel_type=RelType.CREATED.value))
    memory_graph.add_relationship(GraphRelationship(from_id=bob.node_id, to_id=pr142.node_id, rel_type=RelType.REVIEWED.value, properties={"state": "APPROVED"}))
    memory_graph.add_relationship(GraphRelationship(from_id=pr142.node_id, to_id=file_checkout.node_id, rel_type=RelType.MODIFIES.value))
    memory_graph.add_relationship(GraphRelationship(from_id=pr142.node_id, to_id=issue928.node_id, rel_type=RelType.CLOSES.value))
    memory_graph.add_relationship(GraphRelationship(from_id=alice.node_id, to_id=commit.node_id, rel_type=RelType.AUTHORED.value))
    memory_graph.add_relationship(GraphRelationship(from_id=commit.node_id, to_id=file_checkout.node_id, rel_type=RelType.MODIFIES.value))
    memory_graph.add_relationship(GraphRelationship(from_id=alice.node_id, to_id=team.node_id, rel_type=RelType.MEMBER_OF.value))
    memory_graph.add_relationship(GraphRelationship(from_id=bob.node_id, to_id=team.node_id, rel_type=RelType.MEMBER_OF.value))
    memory_graph.add_relationship(GraphRelationship(from_id=team.node_id, to_id=repo.node_id, rel_type=RelType.HAS_ACCESS_TO.value))
    memory_graph.add_relationship(GraphRelationship(from_id=issue928.node_id, to_id=label_bug.node_id, rel_type=RelType.TAGGED_WITH.value))

    print(f"  Nodes indexed: {len(memory_graph.nodes)}")
    print(f"  Relationships indexed: {len(memory_graph.relationships)}")

    # 3. Test Generalized Graph Operations
    print("\n[Step 3] Testing Generalized Graph Primitives...")
    
    # 3.1 get_entity
    entity = entity_retriever.search("get_entity", "#142")
    print(f"  [get_entity #142] Title: {entity.get('title')} (State: {entity.get('state')})")
    assert entity.get("number") == 142

    # 3.2 get_neighbors (Directional BFS)
    neighbors = entity_retriever.search("get_neighbors", pr142.node_id, {"direction": "both"})
    print(f"  [get_neighbors PR #142] Found {len(neighbors)} adjacent nodes across relationships.")
    assert len(neighbors) >= 3

    # 3.3 find_path
    path = entity_retriever.search("find_path", alice.node_id, {"end_id": file_checkout.node_id})
    assert path is not None and len(path) >= 2
    path_hops = " -> ".join(f"[{step.get('rel', 'START')}] {step.get('node_id')}" for step in path)
    print(f"  [find_path alice -> checkout.py] Found path ({len(path)-1} hops): {path_hops}")

    # 4. Test Domain-Specific Shortcuts
    print("\n[Step 4] Testing Domain Developer Intelligence Shortcuts...")
    
    # 4.1 get_pr_details
    pr_details = entity_retriever.search("get_pr_details", "#142")
    print(f"  [get_pr_details #142]:")
    print(f"    - Author: @{pr_details.get('author')}")
    print(f"    - Reviewers: {pr_details.get('reviewers')}")
    print(f"    - Modified Files: {pr_details.get('modified_files')}")
    print(f"    - Closed Issues: {pr_details.get('closed_issues')}")
    assert pr_details.get("author") == "alice"
    assert "bob" in pr_details.get("reviewers", [])

    # 4.2 get_file_contributors
    file_contrib = entity_retriever.search("get_file_contributors", "backend/services/checkout.py")
    print(f"  [get_file_contributors checkout.py] Authors: {file_contrib.get('authors')}, PRs: {file_contrib.get('pull_requests')}")
    assert "alice" in file_contrib.get("authors", [])

    # 4.3 get_user_activity
    user_act = entity_retriever.search("get_user_activity", "alice")
    print(f"  [get_user_activity alice] PRs authored: {user_act.get('authored_prs_count')}, Commits: {user_act.get('authored_commits_count')}")
    assert user_act.get("authored_prs_count") >= 1

    # 5. Verify Native LangChain 5-Tool Suite
    print("\n[Step 5] Verifying Native LangChain 5-Tool Suite...")
    semantic_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)
    keyword_retriever = KeywordRetriever(bm25_index=bm25_index)
    resource_retriever = ResourceLookupRetriever(bm25_index=bm25_index, vector_store=vector_store)
    graph_retriever = GraphRetriever(bm25_index=bm25_index, vector_store=vector_store)

    lc_tools = create_langchain_tools(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        resource_lookup_retriever=resource_retriever,
        graph_retriever=graph_retriever,
        entity_graph_retriever=entity_retriever,
        user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
    )
    print(f"  Registered {len(lc_tools)} LangChain StructuredTools:")
    for t in lc_tools:
        print(f"    - {t.name}: {t.description[:60]}...")
    assert len(lc_tools) == 5

    # 6. Verify LangGraph Autonomous Multi-Hop Reasoning Loop
    print("\n[Step 6] Running LangGraph Multi-Hop Reasoning with Developer Intelligence...")
    tool_registry = create_default_tool_registry(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        resource_lookup_retriever=resource_retriever,
        graph_retriever=graph_retriever,
        entity_graph_retriever=entity_retriever,
    )

    mock_llm = MockEntityGraphLangGraphLLM()
    planner = LangGraphAgentPlanner(
        llm_provider=mock_llm,
        tool_registry=tool_registry,
        max_turns=4,
    )

    user_query = "How do we initialize payments and who resolved the 3DS verification timeout in checkout?"
    result = planner.run(
        query=user_query,
        user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
    )

    print("\n" + "-" * 60)
    print(f"Query: {user_query}")
    print(f"Reasoning Turns Executed: {result['turns']}")
    print(f"Tool Calls Recorded ({len(result['tool_calls'])}):")
    for idx, tc in enumerate(result["tool_calls"], 1):
        print(f"  - [{idx}] {tc['tool']}({tc['arguments']})")

    print(f"\nRetrieved Evidence Chunks ({len(result['retrieved_chunks'])}):")
    for idx, c in enumerate(result["retrieved_chunks"], 1):
        print(f"  [{idx}] [{c.get('source')}] {c.get('title')} ({c.get('chunk_id')})")

    print("\nSynthesized Grounded Answer:")
    print(result["answer"])

    print("\nCitations:")
    for cit in result["citations"]:
        print(f"  [{cit['index']}] {cit['title']} -> {cit['chunk_id']}")
    print("-" * 60)

    # Validations
    assert result["turns"] == 3
    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][0]["tool"] == "semantic_search"
    assert result["tool_calls"][1]["tool"] == "github_entity_search"
    assert "POST `/v1/payments/initiate`" in result["answer"] or "POST /v1/payments/initiate" in result["answer"]
    assert "PR #142" in result["answer"]
    assert "@alice" in result["answer"] or "alice" in result["answer"]
    assert "@bob" in result["answer"] or "bob" in result["answer"]
    assert "[1]" in result["answer"]
    assert "[2]" in result["answer"]

    # Cleanup
    bm25_index.clear()
    print("\n" + "=" * 80)
    print("SUCCESS: Generalized Entity Graph & LangGraph Integration Verified (100% Passed)!")
    print("=" * 80)


if __name__ == "__main__":
    main()
