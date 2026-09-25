#!/usr/bin/env python3
"""
Phase 6 Verification Script: Graph & Resource Lookup Tools with LangGraph Multi-Hop Reasoning.

Demonstrates:
  1. Direct canonical resource lookup (ResourceLookupRetriever) with multi-chunk sequential stitching.
  2. Parent-child hierarchy navigation (GraphRetriever.get_children).
  3. Horizontal bidirectional sibling context expansion (GraphRetriever.get_neighbors).
  4. Procedure & runbook sequence assembly (GraphRetriever.get_full_sequence).
  5. Strict database-level Role-Based Access Control (RBAC) across all graph hops.
  6. Native LangChain 4-Tool suite (semantic_search, keyword_search, resource_lookup, graph_traversal).
  7. Multi-hop LangGraph autonomous reasoning loop (Turn 1: search -> Turn 2: graph -> Turn 3: synthesis).
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
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class MockMultiHopDemoLLM(LLMProvider):
    """
    Deterministic LLM for demonstrating 3-turn multi-hop reasoning in LangGraph:
      Turn 1: Semantic search to discover relevant documentation.
      Turn 2: Graph traversal (get_children) to expand all child sections under parent wiki.
      Turn 3: Grounded answer synthesis referencing multi-source evidence citations.
    """

    def __init__(self) -> None:
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "DeterministicMultiHopAgentMock (Phase 6)"

    def generate(self, messages: List[Message]) -> str:
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            tool_msgs = [m for m in messages if m.role == MessageRole.TOOL_RESULT]
            if len(tool_msgs) < 2:
                return json.dumps({
                    "relevance_score": 0.5,
                    "evidence_sufficient": False,
                    "missing_information": ["Complete list of child sections under parent wiki"],
                    "recommended_action": "CONTINUE",
                    "recommended_tool": "graph_traversal",
                    "reasoning": "Need to traverse graph children.",
                })
            return json.dumps({
                "relevance_score": 0.98,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Sufficient runbook and graph hierarchy evidence retrieved.",
            })

        if "Generate a targeted retrieval query" in last_content:
            return json.dumps({
                "reformulated_query": "https://company.notion.site/engineering",
                "reasoning": "Target child pages under engineering wiki.",
                "suggested_tool": "graph_traversal",
            })

        return (
            "The Disaster Recovery Runbook [1] specifies three mandatory operational steps for payment outages: "
            "1) Drain ingress traffic to the secondary failover cluster, "
            "2) Restart payment-worker processes across all nodes, and "
            "3) Verify gateway health via `/healthz` HTTP 200 response [2]."
        )

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
                        arguments={"query": "disaster recovery failover restart payment worker runbook"},
                        call_id="call_hop1_sem",
                    )
                ]
            )
        elif len(tool_results) == 1:
            # Turn 2: Traverse graph to discover all documentation under the engineering wiki
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
            # Turn 3: Grounded synthesis with citations
            return LLMResponse(
                content=(
                    "The Disaster Recovery Runbook [1] specifies three mandatory operational steps for payment outages: "
                    "1) Drain ingress traffic to the secondary failover cluster, "
                    "2) Restart payment-worker processes across all nodes, and "
                    "3) Verify gateway health via `/healthz` HTTP 200 response [2]."
                )
            )


def main() -> None:
    print("=" * 80)
    print("  PHASE 6 VERIFICATION: GRAPH TOOLS, RESOURCE LOOKUP & MULTI-HOP LANGGRAPH")
    print("=" * 80)

    # ── 1. Pipeline Setup & Ingestion ─────────────────────────────────────────
    print("\n[Step 1/5] Ingesting Multi-Modal Structured Knowledge Topology...")

    embedder = LocalEmbedder()
    vector_store = QdrantVectorStore(mode="memory", collection_name="demo_phase6_collection")
    bm25_index = BM25Index(index_path="./data/demo_phase6_bm25.json")
    bm25_index.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25_index,
    )

    # Ingest Parent Repo
    repo_doc = OKFConcept(
        type="Repository",
        title="Payments Monorepo",
        resource="https://github.com/company/payments",
        body="Central repository containing payments microservices, documentation, and infrastructure.",
        tags=["github", "payments"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={"source": "github", "resource_type": "repository"},
    )
    pipeline.ingest_concept(repo_doc)

    # Ingest Multi-Section API Specification (Child 1 of Repo)
    api_doc = OKFConcept(
        type="Architecture",
        title="Payments API Specification",
        resource="https://github.com/company/payments/docs/api.md",
        body="""# Payments API Specification

## Section 1: Overview
The Payments API handles credit card and 3DS payment intents for web and mobile clients.

## Section 2: Initiation
Send POST /v1/payments/initiate with amount, currency, and customer_id. The gateway returns client_secret.

## Section 3: Webhook Verification
Listen for payment.succeeded events and verify cryptographic HMAC signature.
""",
        tags=["github", "api", "architecture"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={
            "source": "github",
            "resource_type": "file",
            "parent_id": "https://github.com/company/payments",
        },
    )
    api_chunks = pipeline.ingest_concept(api_doc)

    # Ingest Core Engine File (Child 2 of Repo)
    engine_doc = OKFConcept(
        type="File",
        title="Payment Gateway Engine",
        resource="https://github.com/company/payments/src/engine.py",
        body="""# Payment Engine Implementation
class PaymentGatewayEngine:
    def process_transaction(self, tx_id: str):
        \"\"\"Executes financial transaction dispatch.\"\"\"
        pass
""",
        tags=["github", "code"],
        permissions=OKFPermissions(is_public=True),
        extra_metadata={
            "source": "github",
            "resource_type": "file",
            "parent_id": "https://github.com/company/payments",
        },
    )
    pipeline.ingest_concept(engine_doc)

    # Ingest Multi-Step Disaster Recovery Runbook (Child of Engineering Wiki)
    runbook_doc = OKFConcept(
        type="Playbook",
        title="Payment Gateway Disaster Recovery Runbook",
        resource="https://company.notion.site/dr-runbook",
        body="""# Disaster Recovery Runbook

### Step 1: Drain Ingress Traffic
Route incoming traffic to the secondary failover cluster immediately.

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
    runbook_chunks = pipeline.ingest_concept(runbook_doc)

    # Ingest Confidential Master KMS Key Document
    secret_doc = OKFConcept(
        type="Secret",
        title="Vault Master KMS Keys",
        resource="notion://vault/master",
        body="""# Vault Master Key
Production AES-256-GCM master encryption key: AES-256-GCM-SECRET-9999.
Rotation Schedule: Every 90 days.
""",
        tags=["notion", "security"],
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=["security-admin"],
            allowed_users=["ciso@company.com"],
        ),
        extra_metadata={
            "source": "notion",
            "resource_type": "page",
            "parent_id": "https://company.notion.site/engineering",
        },
    )
    pipeline.ingest_concept(secret_doc)

    print(f"  ✓ Ingested 5 OKF Documents across GitHub and Notion.")
    print(f"  ✓ Indexed {len(bm25_index.chunks_map)} linked SmartChunks into Dual-Index (Qdrant + BM25).")

    # ── 2. Direct Resource Lookup & Multi-Chunk Document Stitching ────────────
    print("\n[Step 2/5] Testing ResourceLookupRetriever (Sequential Stitching & RBAC)...")
    res_retriever = ResourceLookupRetriever(bm25_index=bm25_index, vector_store=vector_store)

    doc_res = res_retriever.get_document(
        resource_id="https://github.com/company/payments/docs/api.md",
        user_context={"roles": ["engineer"]},
    )
    assert doc_res is not None, "Failed to retrieve full document by URI"
    assert doc_res["is_complete"], "Document should report is_complete = True"
    assert "Section 1: Overview" in doc_res["stitched_text"]
    assert "Section 2: Initiation" in doc_res["stitched_text"]
    assert "Section 3: Webhook Verification" in doc_res["stitched_text"]

    print(f"  ✓ Reconstructed Document: '{doc_res['title']}' ({doc_res['resource_id']})")
    print(f"    - Total Chunks: {doc_res['total_chunks']} | Complete: {doc_res['is_complete']}")
    print(f"    - Section Outlines: {doc_res['section_paths']}")

    # RBAC check on Resource Lookup
    guest_lookup = res_retriever.get_document(
        resource_id="notion://vault/master",
        user_context={"roles": ["guest"]},
    )
    assert guest_lookup is None, "Guest should NOT be able to look up restricted secret doc"

    admin_lookup = res_retriever.get_document(
        resource_id="notion://vault/master",
        user_context={"roles": ["security-admin"]},
    )
    assert admin_lookup is not None, "Security Admin should be authorized"
    print(f"  ✓ RBAC Pre-Filtering verified (Guest denied -> None, Security-Admin authorized).")

    # ── 3. Graph Topology Traversal ───────────────────────────────────────────
    print("\n[Step 3/5] Testing GraphRetriever (Hierarchy Navigation & Sibling Expansion)...")
    grp_retriever = GraphRetriever(bm25_index=bm25_index, vector_store=vector_store)

    # 3a. Parent-Child Hierarchy (get_children)
    children = grp_retriever.get_children(
        parent_id="https://github.com/company/payments",
        user_context={"roles": ["engineer"]},
    )
    child_uris = [c["resource_id"] for c in children]
    assert "https://github.com/company/payments/docs/api.md" in child_uris
    assert "https://github.com/company/payments/src/engine.py" in child_uris
    print(f"  ✓ Parent-Child Navigation (`get_children`): Found {len(children)} child files under repository:")
    for c in children:
        print(f"    - [{c.get('content_type', 'file')}] {c.get('title')} ({c.get('resource_id')})")

    # 3b. Horizontal Sibling Expansion (get_neighbors)
    step2_chunk_payload = next(p for p in bm25_index.chunks_map.values() if "Step 2" in p.get("text", ""))
    step2_chunk_id = step2_chunk_payload["chunk_id"]
    neighbors = grp_retriever.get_neighbors(
        chunk_id=step2_chunk_id,
        window_before=1,
        window_after=1,
        user_context={"roles": ["engineer"]},
    )
    neighbor_texts = [n["text"] for n in neighbors]
    assert any("Step 1" in t for t in neighbor_texts), "Expected preceding Step 1"
    assert any("Step 2" in t for t in neighbor_texts), "Expected center Step 2"
    assert any("Step 3" in t for t in neighbor_texts), "Expected succeeding Step 3"
    print(f"  ✓ Horizontal Sibling Expansion (`get_neighbors`): Recovered 3-step continuous context window around Step 2.")

    # 3c. Sequence Assembly (get_full_sequence)
    seq_info = step2_chunk_payload.get("sequence")
    if seq_info and isinstance(seq_info, dict) and "sequence_id" in seq_info:
        full_seq = grp_retriever.get_full_sequence(
            sequence_id=seq_info["sequence_id"],
            user_context={"roles": ["engineer"]},
        )
        print(f"  ✓ Procedural Sequence Assembly (`get_full_sequence`): Assembled {len(full_seq)} ordered steps.")

    # 3d. RBAC in Graph Traversal
    eng_wiki_children = grp_retriever.get_children(
        parent_id="https://company.notion.site/engineering",
        user_context={"roles": ["engineer"]},
    )
    eng_wiki_uris = [c["resource_id"] for c in eng_wiki_children]
    assert "https://company.notion.site/dr-runbook" in eng_wiki_uris
    assert "notion://vault/master" not in eng_wiki_uris, "Secret doc must be hidden from engineer"
    print(f"  ✓ Graph Traversal RBAC verified (Confidential child omitted for engineer role).")

    # ── 4. Native LangChain 4-Tool Suite ──────────────────────────────────────
    print("\n[Step 4/5] Testing Native LangChain BaseTool Suite (All 4 Modalities)...")
    sem_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)
    kw_retriever = KeywordRetriever(bm25_index=bm25_index)

    lc_tools = create_langchain_tools(
        semantic_retriever=sem_retriever,
        keyword_retriever=kw_retriever,
        resource_lookup_retriever=res_retriever,
        graph_retriever=grp_retriever,
        user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
    )
    assert len(lc_tools) == 6
    tool_names = [t.name for t in lc_tools]
    print(f"  ✓ Registered {len(lc_tools)} LangChain StructuredTools: {tool_names}")

    # Invoke resource_lookup via LangChain tool invoke
    res_tool = next(t for t in lc_tools if t.name == "resource_lookup")
    lookup_output = res_tool.invoke({"resource_id": "https://github.com/company/payments/docs/api.md"})
    assert "Payments API Specification" in lookup_output
    print(f"  ✓ LangChain resource_lookup output successfully verified.")

    # ── 5. Autonomous Multi-Hop LangGraph Agent Execution ─────────────────────
    print("\n[Step 5/5] Executing Multi-Hop LangGraph Agent Execution Loop...")
    tool_registry = create_default_tool_registry(
        semantic_retriever=sem_retriever,
        keyword_retriever=kw_retriever,
        resource_lookup_retriever=res_retriever,
        graph_retriever=grp_retriever,
    )

    mock_llm = MockMultiHopDemoLLM()
    planner = LangGraphAgentPlanner(
        llm_provider=mock_llm,
        tool_registry=tool_registry,
        max_turns=4,
    )

    query = "What is the complete Disaster Recovery procedure for our payment service?"
    print(f"  User Query: \"{query}\"")
    print("  Orchestrating autonomous LangGraph multi-hop reasoning...")

    result = planner.run(
        query=query,
        user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
    )

    print("\n  ── LangGraph Multi-Hop Execution Trace ──")
    print(f"  Total Turns: {result['turns']}")
    print(f"  Tool Calls Recorded ({len(result['tool_calls'])}):")
    for idx, tc in enumerate(result["tool_calls"], 1):
        print(f"    {idx}. Tool: '{tc['tool']}' | Args: {tc['arguments']}")

    print(f"  Retrieved Chunks Count: {len(result['retrieved_chunks'])}")
    print(f"\n  Final Synthesized Answer:\n  {result['answer']}")

    print(f"\n  Evidence Citations ({len(result['citations'])}):")
    for c in result["citations"]:
        c_num = c.get("index") or c.get("citation_index") or "?"
        print(f"    [{c_num}] {c['title']} ({c['source']}) — URL: {c.get('url', '')}")

    assert len(result["tool_calls"]) == 2, "Expected 2 tool calls across multiple turns"
    assert result["tool_calls"][0]["tool"] == "semantic_search"
    assert result["tool_calls"][1]["tool"] == "graph_traversal"
    assert result["turns"] in (2, 3)
    assert "[1]" in result["answer"]
    assert "[2]" in result["answer"]

    # Cleanup temporary test index
    bm25_index.clear()
    if os.path.exists("./data/demo_phase6_bm25.json"):
        os.remove("./data/demo_phase6_bm25.json")

    print("\n" + "=" * 80)
    print("  🎉 PHASE 6 VERIFICATION COMPLETED SUCCESSFULLY WITH ZERO ERRORS!")
    print("=" * 80)


if __name__ == "__main__":
    main()
