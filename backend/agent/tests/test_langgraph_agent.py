"""
Test Suite for LangGraph-Orchestrated Enterprise Agent (Phase 4, 5 & 6).

Validates:
  1. LangGraph StateGraph compilation and state transitions.
  2. Multi-turn reasoning loop via LangGraph (reasoner -> tool_node -> reasoner -> generator).
  3. Multi-tool execution in a single turn (semantic_search + keyword_search).
  4. Integration with SemanticRetriever, KeywordRetriever, ResourceLookupRetriever & GraphRetriever.
  5. Multi-hop reasoning (e.g. search -> graph_traversal / resource_lookup -> answer).
  6. RBAC context propagation through LangGraph AgentState across all tools.
  7. Native LangChain BaseTool execution with Pydantic validation across all 4 modalities.
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
        return "PR #142 was authored by alice and reviewed by bob [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        if messages and messages[-1].role == MessageRole.TOOL_RESULT:
            return LLMResponse(
                content="PR #142 'Fix 3DS timeout in Checkout Flow' was created by alice and approved by bob [1]."
            )
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


class MockResourceLookupLLMForLangGraph(LLMProvider):
    """Deterministic mock LLM for testing direct resource_lookup in LangGraph."""

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/res-lookup-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        return "Based on [1], the Payments API guide specifies POST /v1/payments/initiate."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        if messages and messages[-1].role == MessageRole.TOOL_RESULT:
            return LLMResponse(
                content="According to the Payments API Guide [1], transaction initiation requires sending amount, currency, and customer_id."
            )
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
        return "The repository contains child files [1]."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        if messages and messages[-1].role == MessageRole.TOOL_RESULT:
            return LLMResponse(
                content="The repository contains documentation and API specs [1]."
            )
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
    Turn 2: Graph traversal to get all children/sections under parent wiki.
    Turn 3: Grounded final answer synthesizing all steps.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/multihop-langgraph-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        return "Full disaster recovery procedure: Step 1 drain traffic, Step 2 restart worker, Step 3 verify health [1] [2]."

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
        elif len(tool_results) == 1:
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
        else:
            # Turn 3: Final grounded answer
            return LLMResponse(
                content="The Disaster Recovery Runbook [1] requires three steps: 1) Drain ingress traffic, 2) Restart payment worker, and 3) Verify gateway health [2]."
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
            resource_lookup_retriever=self.resource_retriever,
            graph_retriever=self.graph_retriever,
            entity_graph_retriever=self.entity_retriever,
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )
        self.assertEqual(len(lc_tools), 5)
        tool_names = [t.name for t in lc_tools]
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
        self.assertEqual(result["turns"], 2)

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
        self.assertEqual(result["turns"], 2)

    def test_09_multihop_reasoning_flow(self) -> None:
        """
        Tests multi-turn, multi-hop reasoning flow:
        Turn 1: Semantic search to discover relevant documentation.
        Turn 2: Graph traversal to retrieve all child sections under the parent wiki.
        Turn 3: Grounded final answer synthesizing the multi-hop evidence.
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

        # 3. Verify final answer and turn count
        self.assertIn("Disaster Recovery Runbook", result["answer"])
        self.assertIn("Drain ingress traffic", result["answer"])
        self.assertEqual(result["turns"], 3)

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
        self.assertEqual(result["turns"], 2)


if __name__ == "__main__":
    unittest.main()
