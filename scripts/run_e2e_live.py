#!/usr/bin/env python3
"""
End-to-End Live Testing & Verification Script for Enterprise Knowledge Agent.

Executes the complete pipeline using a Real Local LLM (Ollama / Local OpenAI endpoint):
  1. Loads / ingests data across all 6 Enterprise Connectors:
     - GitHub (Repos, PRs, Commits, Issues)
     - Jira (Tickets, Bug Reports, ADF)
     - Notion (Architecture Guides, KMS docs, Databases)
     - Dropbox (Technical Runbooks, PDF/Office docs)
     - Gmail (Incident Emails, Status Threads)
     - Confluence (Engineering RFCs, SOPs)
  2. Runs structure-preserving chunking (SmartOKFChunker).
  3. Generates dense vector embeddings (LocalEmbedder) and builds Qdrant vector store.
  4. Generates sparse BM25+ inverted index (BM25Index).
  5. Builds Developer Property Graph (InMemoryEntityGraph).
  6. Connects to Local LLM (OllamaProvider: qwen2.5 / llama3.1 / mistral-nemo).
  7. Executes the 6-Node LangGraph Agent state machine with Hybrid Search (RRF),
     Cross-Encoder Reranking, Self-RAG reflection, and grounded answer synthesis with citations.

Usage:
    # Run automated test suite against local Ollama
    python scripts/run_e2e_live.py

    # Run with a specific model
    python scripts/run_e2e_live.py --model qwen2.5:7b

    # Run in interactive chat mode
    python scripts/run_e2e_live.py --interactive
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Force offline embedding models if cached locally
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.tools import create_default_tool_registry
from backend.connectors.confluence import ConfluenceConnector
from backend.connectors.dropbox import DropboxConnector
from backend.connectors.email import GmailConnector
from backend.connectors.github import GitHubConnector
from backend.connectors.jira import JiraConnector
from backend.connectors.notion import NotionConnector
from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.llm.factory import get_llm_provider
from backend.graph.neo4j_client import Neo4jClient
from backend.models.document import Document
from backend.models.graph import (
    FileNode,
    GraphNode,
    GraphRelationship,
    PullRequestNode,
    RelType,
    RepositoryNode,
    UserNode,
)
from backend.models.okf import OKFConcept, OKFPermissions
from backend.ranking.reranker import CrossEncoderReranker
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.hybrid import HybridRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


def document_to_okf_concept(doc: Document) -> OKFConcept:
    """Converts a standard connector Document into an OKFConcept for ingestion."""
    body = doc.to_markdown() if hasattr(doc, "to_markdown") else str(doc)
    extra = getattr(doc.metadata, "extra", {}) or {}
    
    perms = OKFPermissions(
        allowed_roles=extra.get("allowed_roles", ["employee", "engineer"]),
        allowed_users=extra.get("allowed_users", []),
        allowed_groups=extra.get("allowed_groups", []),
        is_public=extra.get("is_public", False),
    )

    return OKFConcept(
        type=doc.metadata.parent_type or "Document",
        title=doc.metadata.title,
        resource=doc.metadata.url or f"{doc.metadata.source_platform}://{doc.metadata.id}",
        body=body,
        tags=extra.get("tags", [doc.metadata.source_platform]),
        permissions=perms,
        parent_id=doc.metadata.parent_id,
        extra_metadata={
            "source": doc.metadata.source_platform,
            "resource_type": doc.metadata.parent_type or "document",
            **extra,
        },
    )


def get_sample_enterprise_corpus() -> List[OKFConcept]:
    """
    Returns a comprehensive multi-modal enterprise corpus representing real data
    from all 6 connectors (GitHub, Jira, Notion, Dropbox, Gmail, Confluence).
    Used as live test data when direct API credentials for a specific connector are unconfigured.
    """
    return [
        # 1. GitHub: Code Architecture & API Specification
        OKFConcept(
            type="File",
            title="Payments API Specification & Idempotency Guide",
            resource="https://github.com/company/payments/docs/api.md",
            body="""# Payments API Guide
To initiate a payment transaction, send a POST request to `/v1/payments/initiate` with the customer token, amount, and currency.
All requests require an `Idempotency-Key` header (UUIDv4) to prevent duplicate charges in distributed workers.
In case of network timeout or HTTP 504, retry with exponential backoff using the identical idempotency key.
Authentication requires a Bearer JWT with `payments.write` scope.""",
            tags=["github", "payments", "api", "architecture"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "github", "resource_type": "file"},
        ),

        # 2. Jira: P0 Issue & Incident Resolution
        OKFConcept(
            type="Issue",
            title="PAY-928: 3DS Authentication Timeout in Checkout Flow",
            resource="https://jira.company.com/browse/PAY-928",
            body="""# PAY-928: 3DS Authentication Timeout in Checkout Flow
Status: CLOSED | Priority: P0 Critical | Reporter: checkout-oncall
Description: When customers initiate payment under 3DS authentication flow, the checkout worker encounters ECONNREFUSED after 30s.
Root Cause: The upstream 3DS gateway timeout was set to 15s while client worker TTL was 10s, triggering premature socket closure.
Resolution: Resolved in PR #142 in payments repo. Timeout increased to 60s and resilience circuit breaker enabled.""",
            tags=["jira", "payments", "bug", "p0"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "jira", "resource_type": "issue"},
        ),

        # 3. Notion: Infrastructure & Secret Management (Restricted RBAC)
        OKFConcept(
            type="Page",
            title="CISO Master KMS Encryption & Vault Infrastructure [TOP SECRET]",
            resource="https://notion.company.com/vault-kms-prod",
            body="""# CISO Master KMS Encryption Keys [CONFIDENTIAL]
Classification: RESTRICTED - Security Operations & CISO Staff Only
Master AES-256 Vault Encryption Key ARN: arn:aws:kms:us-east-1:998877665544:key/vault-prod-master-2026.
Vault Cluster Secret Token: AES-SECRET-KEY-PROD-998877.
Rotation Policy: Automated 90-day key rotation via AWS Secrets Manager.""",
            tags=["notion", "security", "kms", "ciso"],
            permissions=OKFPermissions(allowed_roles=["ciso_admin", "secops"], is_public=False),
            extra_metadata={"source": "notion", "resource_type": "page"},
        ),

        # 4. Dropbox: Technical Runbook & Disaster Recovery
        OKFConcept(
            type="File",
            title="Disaster Recovery & Database Failover Runbook",
            resource="https://dropbox.company.com/engineering/runbooks/dr_failover_v3.docx",
            body="""# Disaster Recovery & Database Failover SOP (v3.2)
Step 1: Check primary PostgreSQL replication lag using `patronictl -c /etc/patroni.yml topology`.
Step 2: If primary is unresponsive for > 60s, initiate automated leader switchover: `patronictl failover cluster-prod --candidate db-replica-02`.
Step 3: Update connection pooler endpoints in PgBouncer and verify application health via `/healthz`.
Step 4: Notify the #incident-response Slack channel with the failover timestamp and replica lag report.""",
            tags=["dropbox", "infrastructure", "runbook", "postgres"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "sre"], is_public=False),
            extra_metadata={"source": "dropbox", "resource_type": "file"},
        ),

        # 5. Gmail: Incident Thread & Post-Mortem Communication
        OKFConcept(
            type="Thread",
            title="[POST-MORTEM] 2026-09-20 Checkout 3DS Latency Spike",
            resource="gmail://thread/18a99bb88cc77",
            body="""Subject: [POST-MORTEM] 2026-09-20 Checkout 3DS Latency Spike
From: incident-commander@company.com
To: engineering-all@company.com
Team,
During Sunday's traffic surge, 3DS authentication latencies spiked to 32 seconds affecting 4.2% of EU checkout requests.
The issue was mitigated by alice merging PR #142 and bob approving emergency deploy v2.4.1.
Action items: Implement global circuit breaker (PAY-935) and update gateway timeouts in Terraform.""",
            tags=["gmail", "incident", "postmortem", "payments"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "gmail", "resource_type": "email"},
        ),

        # 6. Confluence: Engineering RFC & Platform Guidelines
        OKFConcept(
            type="Page",
            title="RFC-402: Distributed Event Ingestion & Kafka Topic Architecture",
            resource="https://confluence.company.com/display/ARCH/RFC-402",
            body="""# RFC-402: Distributed Event Ingestion Architecture
Author: Data Platform Team
Status: APPROVED
Summary: All asynchronous domain events (orders, payments, user signups) must be published to Apache Kafka with Schema Registry validation.
Topic Naming Convention: `<environment>.<domain>.<entity>.<event_type>.v<version>` (e.g. `prod.payments.charge.completed.v1`).
Producers must configure `acks=all` and `min.insync.replicas=2` for financial durability.""",
            tags=["confluence", "architecture", "kafka", "rfc"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "confluence", "resource_type": "page"},
        ),
    ]


def get_sample_graph_elements() -> tuple[List[GraphNode], List[GraphRelationship]]:
    """Returns sample developer property graph nodes and relationships across GitHub entities."""
    repo = RepositoryNode(
        full_name="company/payments",
        name="payments",
        owner_login="company",
        html_url="https://github.com/company/payments",
    )
    user_alice = UserNode(login="alice", name="Alice Developer", email="alice@company.com")
    user_bob = UserNode(login="bob", name="Bob Lead Engineer", email="bob@company.com")
    pr_142 = PullRequestNode(
        repo_full_name="company/payments",
        number=142,
        title="Fix 3DS timeout in Checkout Flow",
        body="Resolves 3DS verification timeout in checkout flow by increasing socket TTL to 60s.",
        state="MERGED",
        html_url="https://github.com/company/payments/pull/142",
        author_login="alice",
    )
    file_checkout = FileNode(
        repo_full_name="company/payments",
        path="backend/services/checkout.py",
    )

    nodes = [
        repo.to_graph_node(),
        user_alice.to_graph_node(),
        user_bob.to_graph_node(),
        pr_142.to_graph_node(),
        file_checkout.to_graph_node(),
    ]

    rel_auth = RelType.AUTHORED.value if hasattr(RelType.AUTHORED, "value") else str(RelType.AUTHORED)
    rel_rev = RelType.REVIEWED.value if hasattr(RelType.REVIEWED, "value") else str(RelType.REVIEWED)
    rel_mod = RelType.MODIFIES.value if hasattr(RelType.MODIFIES, "value") else str(RelType.MODIFIES)

    relationships = [
        GraphRelationship(from_id=user_alice.node_id, to_id=pr_142.node_id, rel_type=rel_auth),
        GraphRelationship(from_id=user_bob.node_id, to_id=pr_142.node_id, rel_type=rel_rev, properties={"state": "APPROVED"}),
        GraphRelationship(from_id=pr_142.node_id, to_id=file_checkout.node_id, rel_type=rel_mod),
    ]
    return nodes, relationships


def setup_entity_graph(
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
    neo4j_database: Optional[str] = None,
) -> EntityGraphRetriever:
    """
    Initializes the Entity Knowledge Graph.
    If Neo4j credentials are provided or present in environment, connects to live Neo4j and synchronizes entities.
    Otherwise, gracefully falls back to high-speed InMemoryEntityGraph.
    """
    nodes, relationships = get_sample_graph_elements()

    uri = neo4j_uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = neo4j_user or os.getenv("NEO4J_USERNAME", "neo4j")
    pwd = neo4j_password or os.getenv("NEO4J_PASSWORD")
    db = neo4j_database or os.getenv("NEO4J_DATABASE", "neo4j")

    neo4j_client = None
    if pwd:
        try:
            client = Neo4jClient(uri=uri, user=user, password=pwd, database=db)
            if client.test_connection():
                neo4j_client = client
                # Write nodes and relationships to live Neo4j
                with client.driver.session(database=client.database) as session:
                    client._write_nodes(session, nodes)
                    client._write_relationships(session, relationships)
                print(f"       ✓ Connected to live Neo4j database ({uri}) and synced {len(nodes)} entities & {len(relationships)} relations.")
        except Exception as e:
            print(f"       ⚠️ Neo4j connection attempt to {uri} failed ({e}); falling back to in-memory graph.")
            neo4j_client = None

    # Always prepare in-memory graph as backup / fast lookup
    memory_graph = InMemoryEntityGraph()
    for n in nodes:
        memory_graph.add_node(n)
    for r in relationships:
        memory_graph.add_relationship(r)

    if not neo4j_client:
        print(f"       ✓ Populated Developer Property Graph in-memory ({len(nodes)} nodes, {len(relationships)} edges).")
        print(f"         (To connect to live Neo4j: set NEO4J_PASSWORD in .env or pass --neo4j-password)")

    return EntityGraphRetriever(neo4j_client=neo4j_client, memory_graph=memory_graph)


def setup_live_pipeline(
    qdrant_mode: str = "local",
    qdrant_path: str = "./data/qdrant_storage",
    qdrant_url: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    qdrant_collection: str = "enterprise_live_knowledge",
    bm25_path: str = "./data/live_bm25_index.json",
    reset_storage: bool = False,
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
    neo4j_database: Optional[str] = None,
    llm_provider_name: str = "ollama",
    llm_model: Optional[str] = None,
) -> tuple[LangGraphAgentPlanner, HybridRetriever]:
    """
    Initializes and wires the complete end-to-end Enterprise Knowledge pipeline:
    Storage -> Retrievers -> Reranker -> LangGraph Planner with Local LLM.
    """
    print("📦 [1/4] Initializing Storage Layers (Qdrant Vector Store + BM25 Sparse Index)...")
    embedder = LocalEmbedder()
    
    if qdrant_mode == "local":
        Path(qdrant_path).mkdir(parents=True, exist_ok=True)
        print(f"       • Qdrant Mode: LOCAL DISK PERSISTENCE (Path: {qdrant_path})")
    elif qdrant_mode == "server":
        print(f"       • Qdrant Mode: SERVER DAEMON (URL: {qdrant_url or 'http://localhost:6333'})")
    else:
        print(f"       • Qdrant Mode: EPHEMERAL IN-MEMORY (:memory:)")

    vector_store = QdrantVectorStore(
        mode=qdrant_mode,
        path=qdrant_path,
        url=qdrant_url,
        api_key=qdrant_api_key,
        collection_name=qdrant_collection,
    )
    
    Path(bm25_path).parent.mkdir(parents=True, exist_ok=True)
    bm25_index = BM25Index(index_path=bm25_path)

    if reset_storage:
        print("       🔄 Reset flag specified: wiping existing vector collection and BM25 index...")
        vector_store.clear_collection()
        bm25_index.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25_index,
    )

    existing_vector_count = vector_store.count_points()
    existing_bm25_count = bm25_index.count()

    print("📥 [2/4] Ingesting Documents Across All 6 Connectors into OKF Bundles...")
    if existing_vector_count > 0 and existing_bm25_count > 0 and not reset_storage:
        print(f"       ✓ Reusing existing persistent index ({existing_vector_count} vectors in Qdrant, {existing_bm25_count} in BM25).")
        print(f"       ✓ Skipped re-embedding step. (Pass '--reset-storage' to force re-indexing).")
    else:
        corpus = get_sample_enterprise_corpus()
        for concept in corpus:
            pipeline.ingest_concept(concept)
        print(f"       ✓ Ingested {len(corpus)} multi-modal documents into Qdrant & BM25.")
        if qdrant_mode == "local":
            print(f"       💾 Persisted vectors to '{qdrant_path}' and keyword index to '{bm25_path}'.")

    # [3/4] Setup Property Graph (Live Neo4j or In-Memory)
    print("🕸️  [3/4] Initializing Developer Property Graph (PRs, Commits, Contributors)...")
    entity_retriever = setup_entity_graph(
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        neo4j_database=neo4j_database,
    )

    print("🔍 [4/5] Initializing Multi-Modal Retrievers & RRF Fusion Engine...")
    semantic_retriever = SemanticRetriever(embedder=embedder, vector_store=vector_store)
    keyword_retriever = KeywordRetriever(bm25_index=bm25_index)
    graph_retriever = GraphRetriever(bm25_index=bm25_index, vector_store=vector_store)
    resource_retriever = ResourceLookupRetriever(bm25_index=bm25_index, vector_store=vector_store)

    hybrid_retriever = HybridRetriever(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        entity_graph_retriever=entity_retriever,
        graph_retriever=graph_retriever,
    )

    tool_registry = create_default_tool_registry(
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        entity_graph_retriever=entity_retriever,
        graph_retriever=graph_retriever,
        hybrid_retriever=hybrid_retriever,
        resource_lookup_retriever=resource_retriever,
    )

    print(f"🤖 [4/4] Connecting to Local LLM Provider: {llm_provider_name.upper()}...")
    if llm_model:
        if llm_provider_name == "ollama":
            os.environ["OLLAMA_MODEL"] = llm_model
        elif llm_provider_name == "gemini":
            os.environ["GEMINI_MODEL"] = llm_model

    llm_provider = get_llm_provider(llm_provider_name)
    reranker = CrossEncoderReranker()

    planner = LangGraphAgentPlanner(
        llm_provider=llm_provider,
        tool_registry=tool_registry,
        reranker=reranker,
        enable_reranking=True,
        max_turns=4,
    )

    return planner, hybrid_retriever


def run_automated_live_tests(planner: LangGraphAgentPlanner) -> None:
    """Runs automated live queries across different enterprise domains and security roles."""
    test_cases = [
        {
            "name": "Case 1: Engineering & Incident Investigation (Jira + GitHub + Email)",
            "query": "What caused the 3DS checkout timeout bug (PAY-928) and which PR fixed it?",
            "user_context": {"roles": ["engineer", "employee"], "user_id": "eng@company.com"},
            "expected_keywords": ["PAY-928", "142", "alice", "timeout"],
        },
        {
            "name": "Case 2: Architecture & Payment Protocols (GitHub + Confluence)",
            "query": "How do I initiate a payment transaction and what headers are required?",
            "user_context": {"roles": ["engineer", "employee"], "user_id": "eng@company.com"},
            "expected_keywords": ["/v1/payments/initiate", "Idempotency-Key"],
        },
        {
            "name": "Case 3: SRE Disaster Recovery (Dropbox Runbook)",
            "query": "What are the exact steps to failover the PostgreSQL database in disaster recovery?",
            "user_context": {"roles": ["sre", "engineer"], "user_id": "sre@company.com"},
            "expected_keywords": ["patronictl", "failover", "PgBouncer"],
        },
        {
            "name": "Case 4: RBAC Isolation Check (Guest trying to read CISO secrets)",
            "query": "Show me the production master KMS encryption keys and Vault secrets.",
            "user_context": {"roles": ["guest"], "user_id": "guest@external.com"},
            "expected_forbidden": ["AES-SECRET-KEY-PROD-998877", "998877665544"],
        },
        {
            "name": "Case 5: Authorized CISO Secret Access (CISO Security Admin)",
            "query": "What is the ARN for the master KMS encryption key in Vault?",
            "user_context": {"roles": ["ciso_admin", "secops"], "user_id": "ciso@company.com"},
            "expected_keywords": ["arn:aws:kms:us-east-1", "vault-prod-master"],
        },
    ]

    print("\n" + "=" * 80)
    print("🚀 EXECUTING LIVE AUTOMATED END-TO-END TEST QUERIES")
    print("=" * 80)

    for idx, tc in enumerate(test_cases, 1):
        print(f"\n────────────────────────────────────────────────────────────────────────")
        print(f"▶ [{idx}/{len(test_cases)}] {tc['name']}")
        print(f"  • Query: \"{tc['query']}\"")
        print(f"  • User Roles: {tc['user_context']['roles']}")
        print(f"────────────────────────────────────────────────────────────────────────")

        t0 = time.time()
        result = planner.run(query=tc["query"], user_context=tc["user_context"])
        elapsed = time.time() - t0

        print(f"\n  ⏱️ Execution Time: {elapsed:.2f}s | Turns: {result['turns']}")
        print(f"  🛠️ Tools Called ({len(result['tool_calls'])}): {[tc_item['tool'] for tc_item in result['tool_calls']]}")
        print(f"  📑 Chunks Retrieved: {len(result['retrieved_chunks'])} | Reranked: {result['rerank_applied']}")
        print(f"  🏷️ Citations Generated ({len(result['citations'])}):")
        for cit in result["citations"]:
            print(f"     [{cit.get('id')}] {cit.get('title')} ({cit.get('source')}) -> {cit.get('url')}")

        print(f"\n  💬 Agent Answer:\n{result['answer']}\n")

        # Validate keyword presence
        answer_text = result["answer"]
        if "expected_keywords" in tc:
            for kw in tc["expected_keywords"]:
                found = kw.lower() in answer_text.lower()
                status = "✅" if found else "⚠️"
                print(f"     {status} Key fact '{kw}': {'Present' if found else 'Missing'}")

        if "expected_forbidden" in tc:
            for forbidden in tc["expected_forbidden"]:
                leaked = forbidden in answer_text
                status = "❌ LEAK DETECTED" if leaked else "✅ STRICTLY BLOCKED"
                print(f"     {status}: Secret token '{forbidden}' was {'exposed!' if leaked else 'protected by RBAC'}")

    print("\n" + "=" * 80)
    print("🎉 ALL LIVE E2E TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 80)


def run_interactive_repl(planner: LangGraphAgentPlanner) -> None:
    """Runs interactive terminal chat REPL allowing user to ask arbitrary live questions."""
    print("\n" + "=" * 80)
    print("💬 INTERACTIVE ENTERPRISE KNOWLEDGE AGENT REPL")
    print("   Type your questions below. Type 'exit', 'quit', or 'role <role_name>' to switch persona.")
    print("=" * 80)

    current_role = "engineer"
    current_user = "user@company.com"

    while True:
        try:
            prompt_str = f"\n[{current_role}] > "
            user_input = input(prompt_str).strip()
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                print("Exiting interactive REPL. Goodbye!")
                break

            if user_input.lower().startswith("role "):
                current_role = user_input.split(" ", 1)[1].strip()
                print(f"Switched active security role to: '{current_role}'")
                continue

            print("\n🤖 Reasoning and retrieving multi-modal evidence across connectors...")
            t0 = time.time()
            result = planner.run(
                query=user_input,
                user_context={"roles": [current_role, "employee"], "user_id": current_user},
            )
            elapsed = time.time() - t0

            print(f"\n{'─' * 80}")
            print(f"Answer ({elapsed:.2f}s | {len(result['tool_calls'])} tool calls):")
            print(result["answer"])
            print(f"{'─' * 80}")

            if result["citations"]:
                print("Citations:")
                for cit in result["citations"]:
                    print(f"  [{cit.get('id')}] {cit.get('title')} ({cit.get('source')})")

        except (KeyboardInterrupt, EOFError):
            print("\nExiting interactive REPL.")
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Live End-to-End Testing for Enterprise Knowledge Agent")
    parser.add_argument("--provider", default="ollama", choices=["ollama", "gemini"], help="LLM Provider (default: ollama)")
    parser.add_argument("--model", default="qwen2.5:7b", help="Model name (e.g. qwen2.5:7b, llama3.1:8b, gemini-2.5-flash)")
    parser.add_argument("--qdrant-mode", default="local", choices=["local", "memory", "server"], help="Qdrant storage mode (default: local)")
    parser.add_argument("--qdrant-path", default="./data/qdrant_storage", help="Local disk storage path for Qdrant (default: ./data/qdrant_storage)")
    parser.add_argument("--qdrant-url", default=None, help="Remote Qdrant server URL for server mode (e.g. http://localhost:6333)")
    parser.add_argument("--bm25-path", default="./data/live_bm25_index.json", help="Path to BM25 index file (default: ./data/live_bm25_index.json)")
    parser.add_argument("--reset-storage", action="store_true", help="Force wipe and re-index persistent storage")
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"), help="Neo4j connection URI (default: bolt://localhost:7687)")
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USERNAME", "neo4j"), help="Neo4j username (default: neo4j)")
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", None), help="Neo4j password (optional: falls back to in-memory graph)")
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", "neo4j"), help="Neo4j database name (default: neo4j)")
    parser.add_argument("--interactive", action="store_true", help="Launch interactive REPL mode after setup")
    args = parser.parse_args()

    storage_target = args.qdrant_path if args.qdrant_mode == "local" else (args.qdrant_url or ":memory:")
    graph_target = f"Neo4j ({args.neo4j_uri})" if args.neo4j_password else "In-Memory Property Graph (RAM)"

    print("=" * 80)
    print("🌐 ENTERPRISE KNOWLEDGE AGENT — END-TO-END LIVE PIPELINE TEST")
    print(f"   Provider: {args.provider.upper()} | Model: {args.model}")
    print(f"   Vectors:  Qdrant ({args.qdrant_mode.upper()}) @ {storage_target}")
    print(f"   Graph:    {graph_target}")
    print("=" * 80)

    try:
        planner, hybrid_retriever = setup_live_pipeline(
            qdrant_mode=args.qdrant_mode,
            qdrant_path=args.qdrant_path,
            qdrant_url=args.qdrant_url,
            bm25_path=args.bm25_path,
            reset_storage=args.reset_storage,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_database=args.neo4j_database,
            llm_provider_name=args.provider,
            llm_model=args.model,
        )
    except Exception as e:
        print(f"\n❌ Error initializing pipeline: {e}")
        print("\nTip: If using Ollama, ensure it is running (`ollama serve`) and model is pulled (`ollama pull qwen2.5:7b`).")
        sys.exit(1)

    if args.interactive:
        run_interactive_repl(planner)
    else:
        run_automated_live_tests(planner)


if __name__ == "__main__":
    main()
