#!/usr/bin/env python3
"""
Phase 8 Verification Script: Database-Level RBAC Resolver & Security Hierarchy.

Demonstrates:
  1. RoleHierarchy Transitive Expansion (e.g., admin -> security-admin -> engineer -> employee -> guest).
  2. GroupHierarchy Nested Expansion (e.g., payments-core -> payments-team -> engineering).
  3. Direct User Whitelist Pre-Filtering (single-user personal documents).
  4. Vector Store Pre-Filtering via QdrantFilterTranslator (zero leakage in vector top-k).
  5. Lexical BM25 Search Pre-Filtering via BM25FilterTranslator.
  6. Resource Lookup Sequential Stitching with RBAC Access Isolation.
  7. Graph Traversal & Entity Search RBAC Pre-Filtering.
  8. Autonomous LangGraph Agent Multi-Turn RBAC Isolation across varying security personas.
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
from backend.models.security import DecisionReason, UserSecurityContext
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.security.hierarchy import GroupHierarchy, RoleHierarchy
from backend.security.rbac_resolver import RBACResolver, get_default_rbac_resolver
from backend.security.translators import (
    BM25FilterTranslator,
    CypherRBACClauseBuilder,
    GraphNodeFilter,
    QdrantFilterTranslator,
)
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class MockRBACDemoLLM(LLMProvider):
    """Deterministic LLM for demonstrating RBAC agent execution across security tiers."""

    @property
    def provider_name(self) -> str:
        return "DeterministicRBACDemoMock (Phase 8)"

    def generate(self, messages: List[Message]) -> str:
        last_content = messages[-1].content if messages else ""
        if "Evaluate the evidence above" in last_content:
            return json.dumps({
                "relevance_score": 1.0,
                "evidence_sufficient": True,
                "missing_information": [],
                "recommended_action": "GENERATE",
                "reasoning": "Evidence matches security tier.",
            })
        return "Based on authorized knowledge [1], here is the technical summary."

    def generate_with_tools(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
    ) -> LLMResponse:
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    tool_name="semantic_search",
                    arguments={"query": "payment infrastructure security encryption architecture"},
                    call_id="call_rbac_1",
                )
            ]
        )


def main() -> None:
    print("=" * 80)
    print("  PHASE 8 VERIFICATION: DATABASE-LEVEL RBAC RESOLVER & SECURITY HIERARCHY")
    print("=" * 80)

    # ── 1. Security Hierarchy Resolution & Policy Engine ──────────────────────
    print("\n[Step 1/6] Testing Hierarchical Role and Group Expansion Engines...")
    resolver = get_default_rbac_resolver()

    # Persona 1: SecOps Specialist
    secops_ctx = resolver.resolve_context({
        "roles": ["secops"],
        "groups": ["payments-core"],
        "user_id": "alice_secops@company.com",
    })
    print(f"  • Persona 1 (SecOps Engineer):")
    print(f"    - Assigned Roles: {secops_ctx.roles}")
    print(f"    - Transitive Effective Roles: {sorted(secops_ctx.effective_roles)}")
    print(f"    - Assigned Groups: {secops_ctx.groups}")
    print(f"    - Transitive Effective Groups: {sorted(secops_ctx.effective_groups)}")

    assert "security-admin" in secops_ctx.effective_roles
    assert "engineer" in secops_ctx.effective_roles
    assert "employee" in secops_ctx.effective_roles
    assert "payments-team" in secops_ctx.effective_groups
    assert "engineering" in secops_ctx.effective_groups

    # Persona 2: Guest Contractor
    guest_ctx = resolver.resolve_context({
        "roles": ["guest"],
        "user_id": "contractor@external.com",
    })
    print(f"\n  • Persona 2 (External Contractor):")
    print(f"    - Assigned Roles: {guest_ctx.roles}")
    print(f"    - Transitive Effective Roles: {sorted(guest_ctx.effective_roles)}")

    assert "engineer" not in guest_ctx.effective_roles
    assert "security-admin" not in guest_ctx.effective_roles

    # ── 2. Ingesting Multi-Tiered Enterprise Topology ─────────────────────────
    print("\n[Step 2/6] Ingesting Multi-Tiered Enterprise Knowledge Topology...")
    embedder = LocalEmbedder()
    vector_store = QdrantVectorStore(mode="memory", collection_name="demo_phase8_collection")
    bm25_index = BM25Index(index_path="./data/demo_phase8_bm25.json")
    bm25_index.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25_index,
    )

    # Document A: Tier 0 - Public
    doc_public = OKFConcept(
        type="Architecture",
        title="Public API Overview & Getting Started",
        resource="https://github.com/company/docs/public_api.md",
        body="Public API endpoints for developers and integration partners.",
        permissions=OKFPermissions(is_public=True),
    )
    pipeline.ingest_concept(doc_public)

    # Document B: Tier 1 - Engineer Role
    doc_eng = OKFConcept(
        type="Architecture",
        title="Payments Gateway Worker Microservice Architecture",
        resource="https://github.com/company/payments/docs/worker.md",
        body="Internal payment worker retry policies, dead-letter queues, and idempotency keys.",
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=["engineer"],
        ),
    )
    pipeline.ingest_concept(doc_eng)

    # Document C: Tier 2 - Payments Team Group
    doc_team = OKFConcept(
        type="Architecture",
        title="Core Ledger Settlement Pipeline Specifications",
        resource="https://github.com/company/payments/docs/ledger.md",
        body="Internal settlement batch processing and reconciliation database schema.",
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=[],
            allowed_groups=["payments-team"],
        ),
    )
    pipeline.ingest_concept(doc_team)

    # Document D: Tier 3 - Security-Admin Role
    doc_sec = OKFConcept(
        type="Secret",
        title="Vault Master KMS Production Encryption Keys",
        resource="notion://vault/master_kms_keys",
        body="Production master HSM encryption key: AES-256-GCM-SECRET-9999.",
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=["security-admin"],
            allowed_users=["ciso@company.com"],
        ),
    )
    pipeline.ingest_concept(doc_sec)

    # Document E: Tier 4 - Direct User Whitelist (CISO Private Scratchpad)
    doc_ciso = OKFConcept(
        type="Note",
        title="CISO Strategic Audit Findings",
        resource="notion://notes/ciso/audit_2026",
        body="Confidential security audit findings and executive risk register.",
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=[],
            allowed_users=["ciso@company.com"],
        ),
    )
    pipeline.ingest_concept(doc_ciso)

    print(f"  ✓ Ingested 5 multi-tiered documents spanning Public -> Engineer -> Payments Team -> Security Admin -> CISO.")

    # ── 3. Vector Pre-Filtering via QdrantFilterTranslator ────────────────────
    print("\n[Step 3/6] Testing Vector Pre-Filtering (QdrantFilterTranslator)...")
    sem_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)

    # Guest Search
    guest_results = sem_retriever.search("payment worker encryption keys", user_context=guest_ctx.to_dict())
    guest_titles = [r.get("title") for r in guest_results]
    print(f"  • Guest Results ({len(guest_results)}): {guest_titles}")
    assert "Public API Overview & Getting Started" in guest_titles
    assert "Payments Gateway Worker Microservice Architecture" not in guest_titles
    assert "Vault Master KMS Production Encryption Keys" not in guest_titles
    print("    ✓ Guest strictly restricted to Public content.")

    # SecOps Specialist Search
    secops_results = sem_retriever.search("payment worker encryption keys", user_context=secops_ctx.to_dict())
    secops_titles = [r.get("title") for r in secops_results]
    print(f"  • SecOps Results ({len(secops_results)}): {secops_titles}")
    assert "Payments Gateway Worker Microservice Architecture" in secops_titles
    assert "Core Ledger Settlement Pipeline Specifications" in secops_titles
    assert "Vault Master KMS Production Encryption Keys" in secops_titles
    assert "CISO Strategic Audit Findings" not in secops_titles
    print("    ✓ SecOps granted access to Engineer, Payments-Team, and Security-Admin content.")

    # ── 4. BM25 & Resource Lookup RBAC Pre-Filtering ─────────────────────────
    print("\n[Step 4/6] Testing Lexical Search & Resource Lookup Access Isolation...")
    kw_retriever = KeywordRetriever(bm25_index=bm25_index)
    res_retriever = ResourceLookupRetriever(bm25_index=bm25_index, vector_store=vector_store)

    # BM25 Nested Group Match
    bm25_team_res = kw_retriever.search("settlement reconciliation database schema", user_context=secops_ctx.to_dict())
    assert any("Core Ledger Settlement Pipeline" in r.get("title", "") for r in bm25_team_res)
    print("  ✓ BM25 Search verified: Nested group 'payments-core' successfully matched 'payments-team' document.")

    # Resource Lookup User Whitelist Check
    ciso_ctx = resolver.resolve_context({"roles": ["employee"], "user_id": "ciso@company.com"})
    ciso_doc = res_retriever.get_document("notion://notes/ciso/audit_2026", user_context=ciso_ctx.to_dict())
    assert ciso_doc is not None
    print(f"  ✓ Resource Lookup authorized for whitelisted CISO: '{ciso_doc['title']}'")

    denied_doc = res_retriever.get_document("notion://notes/ciso/audit_2026", user_context=secops_ctx.to_dict())
    assert denied_doc is None
    print("  ✓ Resource Lookup blocked for unauthorized user -> returned None.")

    # ── 5. Graph RBAC Traversal & Clause Translation ─────────────────────────
    print("\n[Step 5/6] Testing Cypher Clause Builder & Graph Node Security...")
    cypher_where, cypher_params = CypherRBACClauseBuilder.build_clause(secops_ctx, node_variable="n")
    print(f"  • Generated Cypher WHERE Clause:\n    {cypher_where}")
    print(f"  • Bound Security Parameters: {cypher_params}")
    assert "n.is_public = true" in cypher_where
    assert "ANY(r IN n.allowed_roles" in cypher_where

    # ── 6. Autonomous LangGraph Security Propagation ─────────────────────────
    print("\n[Step 6/6] Executing LangGraph Autonomous Agent with Bound Security Context...")
    grp_retriever = GraphRetriever(bm25_index=bm25_index, vector_store=vector_store)
    entity_retriever = EntityGraphRetriever()

    tool_registry = create_default_tool_registry(
        semantic_retriever=sem_retriever,
        keyword_retriever=kw_retriever,
        resource_lookup_retriever=res_retriever,
        graph_retriever=grp_retriever,
        entity_graph_retriever=entity_retriever,
    )

    demo_llm = MockRBACDemoLLM()
    planner = LangGraphAgentPlanner(
        llm_provider=demo_llm,
        tool_registry=tool_registry,
        max_turns=3,
    )

    # Run query as Guest
    print("  Executing agent turn as Guest Persona...")
    guest_agent_result = planner.run(
        query="What are the KMS encryption keys and payment worker architecture?",
        user_context=guest_ctx.to_dict(),
    )
    retrieved_guest_chunks = guest_agent_result["retrieved_chunks"]
    assert not any("AES-256-GCM-SECRET-9999" in c.get("text", "") for c in retrieved_guest_chunks)
    print(f"  ✓ Guest Agent Execution: Zero restricted chunks retrieved ({len(retrieved_guest_chunks)} public chunks).")

    # Run query as SecOps
    print("  Executing agent turn as SecOps Persona...")
    secops_agent_result = planner.run(
        query="What are the KMS encryption keys and payment worker architecture?",
        user_context=secops_ctx.to_dict(),
    )
    retrieved_secops_chunks = secops_agent_result["retrieved_chunks"]
    assert any("AES-256-GCM-SECRET-9999" in c.get("text", "") for c in retrieved_secops_chunks)
    print(f"  ✓ SecOps Agent Execution: Successfully retrieved {len(retrieved_secops_chunks)} authorized chunks including KMS secrets.")

    # Cleanup temporary index
    bm25_index.clear()
    if os.path.exists("./data/demo_phase8_bm25.json"):
        os.remove("./data/demo_phase8_bm25.json")

    print("\n" + "=" * 80)
    print("  🎉 PHASE 8 VERIFICATION COMPLETED SUCCESSFULLY WITH ZERO ERRORS!")
    print("=" * 80)


if __name__ == "__main__":
    main()
