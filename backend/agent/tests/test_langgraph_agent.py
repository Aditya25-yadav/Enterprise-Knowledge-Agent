"""
Test Suite for LangGraph-Orchestrated Enterprise Agent with Self-RAG Reflection (Phase 4, 5, 6 & 7).

Validates:
  1. LangGraph StateGraph compilation and 5-node state transitions (reasoner, tool_node, evaluator, reformulator, generator).
  2. Single-turn autonomous retrieval with quality evaluation.
  3. Multi-tool execution in a single turn (semantic_search + keyword_search).
  4. Integration with SemanticRetriever, KeywordRetriever, ResourceLookupRetriever & GraphRetriever.
  5. Multi-hop reasoning loop via Self-RAG reflection (evaluator -> reformulator -> reasoner -> generator).
  6. RBAC context propagation through LangGraph AgentState across all tools.
  7. Native LangChain BaseTool execution with Pydantic validation across all modalities.
  8. Graceful termination and fallback when max retrieval attempts are reached.
"""

from __future__ import annotations

import json
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
from backend.storage.qdrant_client import QdrantVectorStore


class MockGitHubEntityLLMForLangGraph(LLMProvider):
    """Deterministic mock LLM for testing github_entity_search in LangGraph."""

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/github-entity-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 1.0,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Retrieved author, reviewer, and status for PR #142.",
            })
        return "PR #142 'Fix 3DS timeout in Checkout Flow' was created by alice and approved by bob [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="github_entity_search",
                    arguments={
                        "operation": "get_pr_details",
                        "target": "#142",
                    },
                    call_id="call_gh_1",
                )
            ]
        )


class MockHybridLLMForLangGraph(LLMProvider):
    """Deterministic mock LLM for testing hybrid_search in LangGraph."""

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/hybrid-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 0.95,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Retrieved comprehensive hybrid evidence across vector and keyword modalities.",
            })
        return "To initiate a payment, invoke AuthService.charge with customer token and amount [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="hybrid_search",
                    arguments={
                        "query": "initiate payment transaction AuthService.charge",
                        "top_k": 3,
                    },
                    call_id="call_hyb_1",
                )
            ]
        )


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
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 0.95,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Evidence contains complete API endpoint details for initiating payment.",
            })
        return "To initialize a payment, call POST /v1/payments/initiate with amount and customer_id [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
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
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 1.0,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Retrieved both bug PAY-928 and payment API documentation.",
            })
        return "Bug PAY-928 describes a 3DS timeout in the checkout flow [1]. Per the API documentation [2], transactions must be initiated via POST /v1/payments/initiate."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
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


class MockResourceLookupLLMForLangGraph(LLMProvider):
    """Deterministic mock LLM for testing direct resource_lookup in LangGraph."""

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/res-lookup-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 0.95,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Fetched complete Payments API Guide document.",
            })
        return "According to the Payments API Guide [1], transaction initiation requires sending amount, currency, and customer_id."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="resource_lookup",
                    arguments={"resource_id": "https://github.com/company/payments/docs/api.md"},
                    call_id="call_lookup_1",
                )
            ]
        )


class MockGraphTraversalLLMForLangGraph(LLMProvider):
    """Deterministic mock LLM for testing graph_traversal in LangGraph."""

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/graph-traversal-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 0.9,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Retrieved child documents under repository root.",
            })
        return "The repository contains child files including Payments API Guide [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="graph_traversal",
                    arguments={
                        "operation": "get_children",
                        "target_id": "https://github.com/company/payments",
                    },
                    call_id="call_grp_1",
                )
            ]
        )


class MockMultiHopLLMForLangGraph(LLMProvider):
    """
    Deterministic mock LLM for multi-hop reasoning across multiple turns:
    Turn 1: Semantic search to locate runbook section.
    Evaluator: Inspected evidence is incomplete, suggests `graph_traversal`.
    Reformulator: Generates query to discover child documents.
    Turn 2: Graph traversal to get all child sections under parent wiki.
    Evaluator: Evidence is sufficient, suggests `GENERATE`.
    Generator: Grounded final answer synthesizing all steps.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/multihop-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""

        # Evaluation Prompt
        if "Evaluate the evidence above" in last_content:
            tool_results = [m for m in messages if m.role == MessageRole.TOOL_RESULT]
            if len(tool_results) <= 1:
                return json.dumps({
                    "relevance_score": 0.8,
                    "evidence_sufficient": False,
                    "missing_information": ["Detailed child sequence steps under engineering wiki"],
                    "recommended_action": "RETRIEVE_MORE",
                    "recommended_tool": "graph_traversal",
                    "reasoning": "Found parent runbook, but child sequence steps need to be traversed.",
                })
            else:
                return json.dumps({
                    "relevance_score": 1.0,
                    "evidence_sufficient": True,
                    "missing_information": [],
                    "recommended_action": "GENERATE",
                    "reasoning": "All disaster recovery steps gathered.",
                })

        # Query Reformulation Prompt
        if "Generate a targeted retrieval query for the next turn" in last_content:
            return json.dumps({
                "reformulated_query": "engineering wiki disaster recovery children",
                "reasoning": "Retrieve child documents under engineering wiki.",
                "suggested_tool": "graph_traversal",
            })

        # Answer Generation Prompt
        return "The Disaster Recovery Runbook [1] requires three steps: 1) Drain ingress traffic, 2) Restart payment worker, and 3) Verify gateway health [2]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        tool_results = [m for m in messages if m.role == MessageRole.TOOL_RESULT]

        if len(tool_results) == 0:
            # Turn 1: Search for runbook
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="semantic_search",
                        arguments={"query": "disaster recovery restart payment worker runbook"},
                        call_id="call_hop1_sem",
                    )
                ]
            )
        else:
            # Turn 2: Traverse graph to retrieve full child documents under engineering wiki
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="graph_traversal",
                        arguments={
                            "operation": "get_children",
                            "target_id": "https://company.notion.site/engineering",
                        },
                        call_id="call_hop2_grp",
                    )
                ]
            )


class MockSelfRAGReflectionLLM(LLMProvider):
    """
    Deterministic mock LLM specifically for testing Self-RAG reflection:
    Turn 1: Semantic search retrieves PR title, but lacks reviewer info.
    Evaluator: Returns REFORMULATE, missing PR reviewers, recommends github_entity_search.
    Reformulator: Returns 'get PR #142 details'.
    Turn 2: github_entity_search retrieves PR details.
    Evaluator: Returns GENERATE.
    Generator: Formulates answer with alice and bob.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/self-rag-reflection-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""

        if "Evaluate the evidence above" in last_content:
            tool_results = [m for m in messages if m.role == MessageRole.TOOL_RESULT]
            if len(tool_results) <= 1:
                return json.dumps({
                    "relevance_score": 0.7,
                    "evidence_sufficient": False,
                    "missing_information": ["Reviewer approval status for PR #142"],
                    "recommended_action": "REFORMULATE",
                    "recommended_tool": "github_entity_search",
                    "reasoning": "Found the PR title, but missing reviewer approvals.",
                })
            else:
                return json.dumps({
                    "relevance_score": 1.0,
                    "evidence_sufficient": True,
                    "missing_information": [],
                    "recommended_action": "GENERATE",
                    "reasoning": "Full PR metadata with author and reviewers obtained.",
                })

        if "Generate a targeted retrieval query for the next turn" in last_content:
            return json.dumps({
                "reformulated_query": "get PR #142 details",
                "reasoning": "Fetch complete PR reviewer info.",
                "suggested_tool": "github_entity_search",
            })

        return "PR #142 was authored by alice and reviewed and approved by bob [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        tool_results = [m for m in messages if m.role == MessageRole.TOOL_RESULT]

        if len(tool_results) == 0:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="semantic_search",
                        arguments={"query": "checkout timeout PR"},
                        call_id="call_sr_1",
                    )
                ]
            )
        else:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        tool_name="github_entity_search",
                        arguments={
                            "operation": "get_pr_details",
                            "target": "#142",
                        },
                        call_id="call_sr_2",
                    )
                ]
            )


class MockMaxAttemptsLLM(LLMProvider):
    """
    Deterministic mock LLM that always returns INSUFFICIENT to test max_retrieval_attempts cutoff.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/max-attempts-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        last_content = messages[-1].content if messages else ""

        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 0.3,
                "evidence_sufficient": False,
                "missing_information": ["Missing critical production architecture diagram"],
                "recommended_action": "RETRIEVE_MORE",
                "recommended_tool": "semantic_search",
                "reasoning": "Evidence is still insufficient.",
            })

        if "Generate a targeted retrieval query for the next turn" in last_content:
            return json.dumps({
                "reformulated_query": "production architecture diagram fallback",
                "reasoning": "Searching again.",
                "suggested_tool": "semantic_search",
            })

        return "Best effort answer based on available evidence: payments worker architecture [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="semantic_search",
                    arguments={"query": "payment architecture diagram"},
                    call_id="call_max_att",
                )
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
        self.resource_retriever = ResourceLookupRetriever(
            bm25_index=self.bm25_index,
            vector_store=self.vector_store,
        )
        self.graph_retriever = GraphRetriever(
            bm25_index=self.bm25_index,
            vector_store=self.vector_store,
        )
        self.memory_graph = InMemoryEntityGraph()
        self.entity_retriever = EntityGraphRetriever(memory_graph=self.memory_graph)

        # Ingest GitHub entity graph fixture
        repo_node = RepositoryNode(
            full_name="company/payments",
            name="payments",
            owner_login="company",
            html_url="https://github.com/company/payments",
            description="Core payment processing gateway and checkout services.",
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

        self.memory_graph.add_node(repo_node.to_graph_node())
        self.memory_graph.add_node(alice_node.to_graph_node())
        self.memory_graph.add_node(bob_node.to_graph_node())
        self.memory_graph.add_node(pr_node.to_graph_node())
        self.memory_graph.add_node(file_node.to_graph_node())

        self.memory_graph.add_relationship(
            GraphRelationship(
                from_id=alice_node.node_id,
                to_id=pr_node.node_id,
                rel_type=RelType.CREATED.value,
            )
        )
        self.memory_graph.add_relationship(
            GraphRelationship(
                from_id=bob_node.node_id,
                to_id=pr_node.node_id,
                rel_type=RelType.REVIEWED.value,
                properties={"state": "APPROVED"},
            )
        )
        self.memory_graph.add_relationship(
            GraphRelationship(
                from_id=pr_node.node_id,
                to_id=file_node.node_id,
                rel_type=RelType.MODIFIES.value,
            )
        )

        self.tool_registry = create_default_tool_registry(
            semantic_retriever=self.retriever,
            keyword_retriever=self.keyword_retriever,
            resource_lookup_retriever=self.resource_retriever,
            graph_retriever=self.graph_retriever,
            entity_graph_retriever=self.entity_retriever,
        )

        # Ingest Doc 1: Architecture Guide (child of https://github.com/company/payments)
        doc1 = OKFConcept(
            type="Architecture",
            title="Payments API Guide",
            resource="https://github.com/company/payments/docs/api.md",
            body="""# Payments API Guide
To initiate a transaction, send a POST request to `/v1/payments/initiate` containing the amount, currency, and customer_id. The gateway returns a client_secret token for 3DS verification.
""",
            tags=["github", "payments", "api"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={
                "source": "github",
                "resource_type": "file",
                "parent_id": "https://github.com/company/payments",
            },
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

        # Ingest Doc 3: Parent Repository
        doc3 = OKFConcept(
            type="Repository",
            title="Payments Monorepo",
            resource="https://github.com/company/payments",
            body="Central repository containing payments microservices and documentation.",
            tags=["github", "payments"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={"source": "github", "resource_type": "repository"},
        )
        self.pipeline.ingest_concept(doc3)

        # Ingest Doc 4: DR Runbook (child of engineering wiki)
        doc4 = OKFConcept(
            type="Playbook",
            title="Payment Gateway Disaster Recovery Runbook",
            resource="https://company.notion.site/dr-runbook",
            body="""# Disaster Recovery Runbook

### Step 1: Drain Ingress Traffic
Route incoming requests to the fallback secondary cluster.

### Step 2: Restart Payment Worker
Execute `systemctl restart payment-worker` on all node pools.

### Step 3: Verify Gateway Health
Query `/healthz` endpoint to confirm 200 OK status.
""",
            tags=["notion", "runbook"],
            permissions=OKFPermissions(is_public=True),
            extra_metadata={
                "source": "notion",
                "resource_type": "playbook",
                "parent_id": "https://company.notion.site/engineering",
            },
        )
        self.pipeline.ingest_concept(doc4)

    def tearDown(self) -> None:
        self.bm25_index.clear()

    def test_01_graph_compilation(self) -> None:
        """Verifies StateGraph structure with all 6 nodes compiled."""
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
        self.assertIn("reranker", nodes)
        self.assertIn("evaluator", nodes)
        self.assertIn("reformulator", nodes)
        self.assertIn("generator", nodes)

    def test_02_autonomous_langgraph_tool_loop(self) -> None:
        """Tests single-turn retrieval with evaluator quality verification and synthesis."""
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

        # 1. Verify tool was called
        self.assertGreaterEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["tool"], "semantic_search")
        self.assertEqual(result["tool_calls"][0]["arguments"]["query"], "initialize payment intent API")

        # 2. Verify chunks were retrieved
        self.assertGreater(len(result["retrieved_chunks"]), 0)

        # 3. Verify final answer and citation attribution
        self.assertIn("POST /v1/payments/initiate", result["answer"])
        self.assertIn("[1]", result["answer"])
        self.assertEqual(len(result["citations"]), len(result["retrieved_chunks"]))

        # 4. Verify evaluator validated evidence
        self.assertIsNotNone(result["evaluation"])
        self.assertTrue(result["evaluation"]["evidence_sufficient"])

    def test_03_langgraph_rbac_isolation(self) -> None:
        """Verifies database-level RBAC filtering across agent retrieval."""
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
        """Tests individual native LangChain BaseTool execution with Pydantic validation."""
        lc_tools = create_langchain_tools(
            semantic_retriever=self.retriever,
            keyword_retriever=self.keyword_retriever,
            resource_lookup_retriever=self.resource_retriever,
            graph_retriever=self.graph_retriever,
            entity_graph_retriever=self.entity_retriever,
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )
        self.assertEqual(len(lc_tools), 6)
        tool_names = [t.name for t in lc_tools]
        self.assertIn("hybrid_search", tool_names)
        self.assertIn("semantic_search", tool_names)
        self.assertIn("keyword_search", tool_names)
        self.assertIn("resource_lookup", tool_names)
        self.assertIn("graph_traversal", tool_names)
        self.assertIn("github_entity_search", tool_names)

        # Execute semantic_search directly as a LangChain tool
        sem_tool = next(t for t in lc_tools if t.name == "semantic_search")
        res_json = sem_tool.invoke({"query": "payment transaction initiation", "top_k": 2})
        self.assertIn("Payments API Guide", res_json)
        self.assertIn("/v1/payments/initiate", res_json)

        # Execute resource_lookup directly as a LangChain tool
        res_tool = next(t for t in lc_tools if t.name == "resource_lookup")
        lookup_json = res_tool.invoke({"resource_id": "https://github.com/company/payments/docs/api.md"})
        self.assertIn("Payments API Guide", lookup_json)

        # Execute graph_traversal directly as a LangChain tool
        grp_tool = next(t for t in lc_tools if t.name == "graph_traversal")
        grp_json = grp_tool.invoke({"operation": "get_children", "target_id": "https://github.com/company/payments"})
        self.assertIn("Payments API Guide", grp_json)

        # Execute github_entity_search directly as a LangChain tool
        gh_tool = next(t for t in lc_tools if t.name == "github_entity_search")
        gh_json = gh_tool.invoke({"operation": "get_pr_details", "target": "#142"})
        self.assertIn("Fix 3DS timeout", gh_json)
        self.assertIn("alice", gh_json)

    def test_05_multi_tool_execution_in_single_turn(self) -> None:
        """
        Tests multi-tool execution in a single turn (keyword_search + semantic_search).
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
        self.assertTrue(any("PAY-928" in t for t in retrieved_titles))
        self.assertTrue(any("Payments API Guide" in t for t in retrieved_titles))

        # 3. Verify grounded answer references both [1] and [2]
        self.assertIn("PAY-928", result["answer"])
        self.assertIn("[1]", result["answer"])
        self.assertIn("[2]", result["answer"])

    def test_06_native_keyword_search_langchain_tool(self) -> None:
        """
        Verifies direct execution of the native LangChain keyword_search StructuredTool.
        """
        lc_tools = create_langchain_tools(
            semantic_retriever=self.retriever,
            keyword_retriever=self.keyword_retriever,
            resource_lookup_retriever=self.resource_retriever,
            graph_retriever=self.graph_retriever,
            entity_graph_retriever=self.entity_retriever,
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )
        kw_tool = next(t for t in lc_tools if t.name == "keyword_search")
        res_json = kw_tool.invoke({"query": "PAY-928", "top_k": 1})
        self.assertIn("PAY-928", res_json)
        self.assertIn("3DS Timeout", res_json)

    def test_07_resource_lookup_in_langgraph_loop(self) -> None:
        """
        Tests LangGraph agent invoking resource_lookup tool directly to fetch
        complete stitched document evidence.
        """
        mock_llm = MockResourceLookupLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )

        result = planner.run(
            query="Fetch the full Payments API Guide documentation.",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify resource_lookup tool was called
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["tool"], "resource_lookup")
        self.assertEqual(result["tool_calls"][0]["arguments"]["resource_id"], "https://github.com/company/payments/docs/api.md")

        # 2. Verify chunks were retrieved
        self.assertGreater(len(result["retrieved_chunks"]), 0)
        self.assertTrue(any("Payments API Guide" in c.get("title", "") for c in result["retrieved_chunks"]))

        # 3. Verify final answer
        self.assertIn("Payments API Guide", result["answer"])
        self.assertIn("[1]", result["answer"])

    def test_08_graph_traversal_in_langgraph_loop(self) -> None:
        """
        Tests LangGraph agent invoking graph_traversal tool to discover child documents.
        """
        mock_llm = MockGraphTraversalLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )

        result = planner.run(
            query="What files and docs are in the Payments Monorepo?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify graph_traversal tool was called
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["tool"], "graph_traversal")
        self.assertEqual(result["tool_calls"][0]["arguments"]["operation"], "get_children")
        self.assertEqual(result["tool_calls"][0]["arguments"]["target_id"], "https://github.com/company/payments")

        # 2. Verify child chunks were retrieved
        self.assertGreater(len(result["retrieved_chunks"]), 0)
        self.assertTrue(any("Payments API Guide" in c.get("title", "") for c in result["retrieved_chunks"]))

        # 3. Verify final answer
        self.assertIn("[1]", result["answer"])

    def test_09_multihop_reasoning_flow(self) -> None:
        """
        Tests multi-turn, multi-hop reasoning flow via Self-RAG reflection:
        Turn 1: Semantic search to discover runbook.
        Evaluator: Identifies missing child steps, recommends `graph_traversal`.
        Reformulator: Synthesizes new query for child pages.
        Turn 2: Graph traversal retrieves full child runbook documents.
        Evaluator: Verifies evidence sufficiency (GENERATE).
        Generator: Synthesizes final grounded answer.
        """
        mock_llm = MockMultiHopLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=4,
        )

        result = planner.run(
            query="What is the complete Disaster Recovery procedure for our payment service?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify 2 tool calls across 2 reasoning turns
        self.assertEqual(len(result["tool_calls"]), 2)
        self.assertEqual(result["tool_calls"][0]["tool"], "semantic_search")
        self.assertEqual(result["tool_calls"][1]["tool"], "graph_traversal")

        # 2. Verify retrieved chunks from both hops
        self.assertGreater(len(result["retrieved_chunks"]), 0)
        retrieved_titles = [c.get("title") for c in result["retrieved_chunks"]]
        self.assertTrue(any("Disaster Recovery Runbook" in t for t in retrieved_titles))

        # 3. Verify final answer and turns
        self.assertIn("Disaster Recovery Runbook", result["answer"])
        self.assertIn("Drain ingress traffic", result["answer"])
        self.assertEqual(result["retrieval_attempts"], 2)

    def test_10_github_entity_search_in_langgraph_loop(self) -> None:
        """
        Tests LangGraph agent invoking github_entity_search tool to retrieve
        PR metadata, author, reviewers, and modified files in the agent reasoning loop.
        """
        mock_llm = MockGitHubEntityLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=3,
        )

        result = planner.run(
            query="Who authored and reviewed PR #142?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify github_entity_search tool was called
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["tool"], "github_entity_search")
        self.assertEqual(result["tool_calls"][0]["arguments"]["operation"], "get_pr_details")
        self.assertEqual(result["tool_calls"][0]["arguments"]["target"], "#142")

        # 2. Verify evidence was retrieved from entity graph
        self.assertGreater(len(result["retrieved_chunks"]), 0)
        self.assertTrue(
            any("142" in str(c.get("title", "")) or "Fix 3DS timeout" in str(c.get("text", ""))
                for c in result["retrieved_chunks"])
        )

        # 3. Verify final answer
        self.assertIn("PR #142", result["answer"])
        self.assertIn("alice", result["answer"])
        self.assertIn("bob", result["answer"])
        self.assertIn("[1]", result["answer"])

    def test_11_self_rag_reflection_and_reformulation_loop(self) -> None:
        """
        Explicitly validates the Self-RAG reflection state transitions:
        Turn 1: Semantic search retrieves PR title (insufficient).
        Evaluator: Emits REFORMULATE with missing reviewer info and recommends github_entity_search.
        Reformulator: Generates targeted query 'get PR #142 details'.
        Turn 2: Reasoner uses reflection guidance to execute github_entity_search.
        Evaluator: Emits GENERATE with sufficient evidence.
        Generator: Synthesizes verified answer.
        """
        mock_llm = MockSelfRAGReflectionLLM()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=4,
            max_retrieval_attempts=3,
        )

        result = planner.run(
            query="Who approved the checkout timeout fix?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify 2 retrieval attempts occurred
        self.assertEqual(result["retrieval_attempts"], 2)

        # 2. Verify query was reformulated
        self.assertEqual(len(result["reformulated_queries"]), 1)
        self.assertEqual(result["reformulated_queries"][0], "get PR #142 details")

        # 3. Verify tool sequence: semantic_search -> github_entity_search
        self.assertEqual(len(result["tool_calls"]), 2)
        self.assertEqual(result["tool_calls"][0]["tool"], "semantic_search")
        self.assertEqual(result["tool_calls"][1]["tool"], "github_entity_search")

        # 4. Verify final evaluation state
        self.assertIsNotNone(result["evaluation"])
        self.assertTrue(result["evaluation"]["evidence_sufficient"])
        self.assertEqual(result["evaluation"]["recommended_action"], "GENERATE")

        # 5. Verify final answer
        self.assertIn("alice", result["answer"])
        self.assertIn("bob", result["answer"])
        self.assertIn("[1]", result["answer"])

    def test_12_max_retrieval_attempts_fallback(self) -> None:
        """
        Verifies that if retrieval remains insufficient, the state machine
        gracefully halts after `max_retrieval_attempts` without infinite loops.
        """
        mock_llm = MockMaxAttemptsLLM()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            max_turns=5,
            max_retrieval_attempts=2,
        )

        result = planner.run(
            query="Show me the production architecture diagram for payments.",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # Should halt at max_retrieval_attempts = 2
        self.assertEqual(result["retrieval_attempts"], 2)
        self.assertIn("Best effort answer based on available evidence", result["answer"])

    def test_13_reranker_node_execution_in_langgraph_loop(self) -> None:
        """
        Verifies that candidate chunks retrieved during tool execution are
        scored, re-ordered, and annotated with rerank metadata by the `reranker` node.
        """
        mock_llm = MockLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            enable_reranking=True,
            max_turns=3,
        )

        result = planner.run(
            query="How do I initiate a payment transaction?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # Verify reranker was applied
        self.assertTrue(result["rerank_applied"])
        self.assertIsInstance(result["rerank_scores"], dict)
        self.assertGreater(len(result["rerank_scores"]), 0)

        # Verify chunks have rerank fields
        for chunk in result["retrieved_chunks"]:
            self.assertIn("rerank_score", chunk)
            self.assertIn("rerank_rank", chunk)
            self.assertIn("original_rank", chunk)

    def test_14_native_langchain_hybrid_search_tool(self) -> None:
        """
        Verifies that `hybrid_search` is registered in `create_langchain_tools()`
        and executes Reciprocal Rank Fusion returning structured JSON output.
        """
        lc_tools = create_langchain_tools(
            semantic_retriever=self.retriever,
            keyword_retriever=self.keyword_retriever,
            entity_graph_retriever=self.entity_retriever,
            graph_retriever=self.graph_retriever,
            bm25_index=self.bm25_index,
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        tool_map = {t.name: t for t in lc_tools}
        self.assertIn("hybrid_search", tool_map)

        hybrid_tool = tool_map["hybrid_search"]
        raw_output = hybrid_tool.invoke({
            "query": "checkout timeout 3DS",
            "top_k": 3,
            "k": 60,
        })

        self.assertIsInstance(raw_output, str)
        results = json.loads(raw_output)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)

        first_hit = results[0]
        self.assertIn("chunk_id", first_hit)
        self.assertIn("rrf_score", first_hit)
        self.assertIn("rrf_rank", first_hit)
        self.assertIn("modalities_matched", first_hit)
        self.assertIn("ranks_per_modality", first_hit)

    def test_15_hybrid_search_tool_in_langgraph_loop(self) -> None:
        """
        Verifies end-to-end execution of `hybrid_search` within the 6-node LangGraph
        agent state machine, confirming tool invocation, RRF fusion, and reranking.
        """
        mock_llm = MockHybridLLMForLangGraph()
        planner = LangGraphAgentPlanner(
            llm_provider=mock_llm,
            tool_registry=self.tool_registry,
            enable_reranking=True,
            max_turns=3,
        )

        result = planner.run(
            query="How do I initiate a payment transaction?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        # 1. Verify tool calls
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["tool"], "hybrid_search")

        # 2. Verify retrieved chunks and fusion metadata
        self.assertGreater(len(result["retrieved_chunks"]), 0)
        first_chunk = result["retrieved_chunks"][0]
        self.assertIn("rrf_score", first_chunk)
        self.assertIn("modalities_matched", first_chunk)

        # 3. Verify reranking was applied on top of hybrid fused candidates
        self.assertTrue(result["rerank_applied"])
        self.assertIn("rerank_score", first_chunk)

        # 4. Verify answer and citations
        self.assertIn("AuthService.charge", result["answer"])
        self.assertGreater(len(result["citations"]), 0)


if __name__ == "__main__":
    unittest.main()


