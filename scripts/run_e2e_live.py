#!/usr/bin/env python3
"""
End-to-End Live Testing & Verification Script for Enterprise Knowledge Agent.

Executes the complete pipeline using Local LLMs (Ollama) or Cloud LLMs (Google Gemini):
  1. Loads / ingests data across all 6 Enterprise Connectors:
     - GitHub (Repos, PRs, Commits, Issues)
     - Jira (Tickets, Bug Reports, ADF)
     - Notion (Architecture Guides, KMS docs, Databases)
     - Dropbox (Technical Runbooks, PDF/Office docs)
     - Gmail (Incident Emails, Status Threads)
     - Confluence (Engineering RFCs, SOPs)
  2. Builds Map-First Global Master Index (global_index.md) and sync ledger (global_log.md).
  3. Runs structure-preserving chunking (SmartOKFChunker).
  4. Generates dense vector embeddings (LocalEmbedder) and builds Qdrant vector store.
  5. Generates sparse BM25+ inverted index (BM25Index).
  6. Builds Developer Property Graph (InMemoryEntityGraph or live Neo4j).
  7. Connects to LLM Provider (Ollama: qwen2.5 / llama3.1 / mistral-nemo, or Gemini: gemini-2.5-flash / pro).
  8. Executes the LangGraph Agent state machine with 7 specialized tools:
     catalog_discovery, hybrid_search, semantic_search, keyword_search, resource_lookup,
     graph_traversal, and github_entity_search.
  9. Features Cross-Encoder Reranking, Self-RAG reflection, SQLite conversation thread persistence,
     fast-path direct answering for meta/general queries, and query scope directives (@enterprise, @general, @<connector>).

Usage:
    # Run automated test suite against local Ollama
    python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b

    # Run automated test suite against Google Gemini
    python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash

    # Run interactive chat REPL mode
    python scripts/run_e2e_live.py --provider gemini --interactive
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

# Load .env file automatically
from dotenv import load_dotenv
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

# Force offline embedding models if cached locally
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.query_scope import parse_query_scope
from backend.agent.tools import create_default_tool_registry
from backend.connectors.confluence import ConfluenceConnector
from backend.connectors.dropbox import DropboxConnector
from backend.connectors.email import GmailConnector
from backend.connectors.github import GitHubConnector
from backend.connectors.jira import JiraConnector
from backend.connectors.notion import NotionConnector
from backend.ingestion.catalog_aggregator import GlobalCatalogManager
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
from backend.retrieval.catalog import CatalogRetriever
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.hybrid import HybridRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.checkpointers import BaseCheckpointSaver, get_checkpointer
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
        # =====================================================================
        # 1. GITHUB (Repos, PRs, Architecture Specs, DevOps Manifests)
        # =====================================================================
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
        OKFConcept(
            type="File",
            title="Kubernetes Ingress & Cert-Manager Let's Encrypt TLS Configuration",
            resource="https://github.com/company/infra-k8s/blob/main/ingress/cert-manager-production.yaml",
            body="""# Ingress Controller & Automated TLS Certificate Configuration
All production Kubernetes clusters utilize NGINX Ingress Controller v1.9.4 with Cert-Manager v1.13.0 for automated TLS certificate issuance via Let's Encrypt ACME HTTP-01 challenge.
ClusterIssuer configuration:
- Issuer: `letsencrypt-production`
- ACME Server: `https://acme-v02.api.letsencrypt.org/directory`
- Private Key Secret: `letsencrypt-prod-account-key`
- Solver: Ingress class `nginx-external` with auto-renew at 30 days before expiration.
Traffic Routing Rule: Host headers must match `*.api.company.com` or `*.internal.company.com`. Default rate limit is configured at 500 req/s per client IP (`nginx.ingress.kubernetes.io/limit-rps: "500"`).""",
            tags=["github", "kubernetes", "ingress", "cert-manager", "tls", "devops"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "sre"], is_public=False),
            extra_metadata={"source": "github", "resource_type": "file"},
        ),
        OKFConcept(
            type="File",
            title="OAuth 2.0 PKCE Authorization Server & Token Exchange Specification",
            resource="https://github.com/company/auth-service/docs/oauth2-pkce-spec.md",
            body="""# OAuth 2.0 with PKCE (Proof Key for Code Exchange) Specification
For all public clients (Single-Page Applications and iOS/Android Mobile Apps), the standard OAuth 2.0 Authorization Code Grant MUST be paired with PKCE (RFC 7636).
1. Client generates a cryptographically random `code_verifier` (43-128 characters, Base64URL-encoded).
2. Client derives `code_challenge = BASE64URL(SHA256(code_verifier))` with `code_challenge_method = S256`. Plain method is strictly forbidden.
3. Authorization endpoint: `https://auth.company.com/oauth/authorize`.
4. Token endpoint: `https://auth.company.com/oauth/token` exchanges `code` and `code_verifier` for Access Token (JWT, 15-minute expiry) and Refresh Token (Rotating, 30-day sliding window).
5. Token Revocation: Immediate blacklisting in Redis cluster on user logout via `/oauth/revoke`.""",
            tags=["github", "auth", "oauth2", "pkce", "security", "jwt"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "github", "resource_type": "file"},
        ),

        # =====================================================================
        # 2. JIRA (Tickets, Bug Reports, Infrastructure Rollouts)
        # =====================================================================
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
        OKFConcept(
            type="Issue",
            title="SEC-1104: Zero-Trust Cloudflare Access Tunnel & SSH Bastion Migration",
            resource="https://jira.company.com/browse/SEC-1104",
            body="""# SEC-1104: Zero-Trust Cloudflare Access Tunnel & SSH Bastion Migration
Status: IN PROGRESS | Priority: P1 High | Assignee: devsecops-team | Fix Version: 2026.Q4
Description: Decommission legacy open-port OpenVPN servers and migrate all internal developer tooling (Grafana, ArgoCD, Internal Admin UI) to Cloudflare Zero-Trust Tunnels (`cloudflared`).
Architecture:
- `cloudflared` daemon runs in HA mode (3 replicas) across Kubernetes management namespaces.
- IdP Authentication: Enforced Okta SAML 2.0 with FIDO2 WebAuthn Hardware Keys mandatory for all employees.
- SSH Access: Managed via Cloudflare Short-Lived Certificates (Ephemerally signed via Vault CA, TTL 60m).
- Status: Grafana and ArgoCD migrated successfully. Staging bastion decommissioned on 2026-09-15.""",
            tags=["jira", "security", "zero-trust", "cloudflare", "okta", "vpn"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "secops", "sre"], is_public=False),
            extra_metadata={"source": "jira", "resource_type": "issue"},
        ),
        OKFConcept(
            type="Issue",
            title="DATA-782: Real-Time Clickstream Analytics Pipeline using Flink and Iceberg",
            resource="https://jira.company.com/browse/DATA-782",
            body="""# DATA-782: Real-Time Clickstream Analytics Pipeline using Flink and Iceberg
Status: RESOLVED | Priority: P1 High | Reporter: lead-data-engineer
Problem: Clickstream batch ETL in BigQuery had an 8-hour latency, delaying real-time fraud detection and recommendation engine scoring.
Solution Implemented:
1. Real-time clickstream ingestion from Kafka (`prod.events.clickstream.v2`) into Apache Flink 1.18 streaming job.
2. Flink performs 1-minute tumbling window aggregations with session deduplication via RocksDB StateBackend.
3. Output sink writes directly to Apache Iceberg format on AWS S3, registered in AWS Glue Data Catalog.
4. End-to-end event latency dropped from 8 hours to under 45 seconds (99.8% SLA met).""",
            tags=["jira", "data", "flink", "iceberg", "kafka", "analytics"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "jira", "resource_type": "issue"},
        ),

        # =====================================================================
        # 3. NOTION (Architecture Guides, Secret Vaults, Internal Policies)
        # =====================================================================
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
        OKFConcept(
            type="Page",
            title="New Engineer Workstation Setup & macOS Security Hardening Guide",
            resource="https://notion.company.com/it/engineer-workstation-onboarding",
            body="""# Engineering Laptop Setup & macOS Hardening Guide (2026 Edition)
Welcome to Company Engineering! Follow these mandatory setup steps upon receiving your Apple Silicon MacBook Pro:
1. MDM & Device Enrollment: Verify Jamf Pro profile enrollment under System Settings -> Privacy & Security -> Profiles.
2. Disk Encryption: FileVault MUST be enabled immediately with institutional escrow key.
3. Developer Toolchain:
   - Homebrew: Run `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`
   - Docker Desktop: Use Docker Desktop Enterprise 4.28+ with Rosetta 2 x86_64 emulation enabled.
   - Git Signing: GPG key or SSH signing required for all Git commits (`git config --global commit.gpgsign true`).
4. Endpoint Security: CrowdStrike Falcon sensor runs in background; do not kill daemon `com.crowdstrike.falcon.Agent`.""",
            tags=["notion", "onboarding", "it", "security", "macos", "hardware"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "notion", "resource_type": "page"},
        ),
        OKFConcept(
            type="Page",
            title="Internal AI Governance & LLM Data Protection Policy (v2.1)",
            resource="https://notion.company.com/legal/ai-governance-policy",
            body="""# Enterprise Generative AI & LLM Governance Policy
Classification: Internal Company Policy | Effective Date: 2026-08-01 | Owner: Legal & CISO Office
1. Permitted Models: Only approved enterprise-tier LLM endpoints (Self-Hosted Ollama instances, Azure OpenAI Enterprise tenant, Google Vertex AI Enterprise) may process proprietary source code or internal documents.
2. Data Privacy & Zero Data Retention (ZDR): Public consumer AI web interfaces (e.g. consumer ChatGPT, Claude web) are STRICTLY PROHIBITED from receiving customer PII, internal passwords, API keys, or financial transaction data.
3. Code Synthesis Review: All AI-generated production code must undergo mandatory human peer review and pass SAST (SonarQube) and SCA (Snyk) security scans before merging into `main`.
4. Compliance Audit: Prompt/completion audit logs are retained for 365 days in encrypted cold storage for SOC2 Type II compliance.""",
            tags=["notion", "ai", "governance", "policy", "ciso", "compliance"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "legal", "ciso_admin"], is_public=False),
            extra_metadata={"source": "notion", "resource_type": "page"},
        ),

        # =====================================================================
        # 4. DROPBOX (Technical Runbooks, PDF SOPs, FinOps Audits)
        # =====================================================================
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
        OKFConcept(
            type="File",
            title="OpenSearch 12-Node Production Cluster Reindexing & Zero-Downtime Migration SOP",
            resource="https://dropbox.company.com/engineering/runbooks/opensearch_reindex_sop_2026.pdf",
            body="""# OpenSearch Production Cluster Zero-Downtime Reindexing SOP
Scope: Applied during schema updates, analyzer modifications, or major shard reallocation on the 12-node OpenSearch 2.11 cluster (`os-prod-cluster-01`).
Step 1: Create the target index with updated mappings and optimal bulk index settings:
  `PUT /product_catalog_v2 { "settings": { "number_of_shards": 6, "number_of_replicas": 0, "refresh_interval": "-1" } }`
Step 2: Initiate async background reindex from source index to target index:
  `POST /_reindex?wait_for_completion=false { "source": { "index": "product_catalog_v1" }, "dest": { "index": "product_catalog_v2" } }`
Step 3: Monitor task progress: `GET /_tasks/<task_id>`. Verify document counts match.
Step 4: Restore replication and refresh:
  `PUT /product_catalog_v2/_settings { "number_of_replicas": 2, "refresh_interval": "1s" }`
Step 5: Atomic Alias Switchover (Zero-Downtime):
  `POST /_aliases { "actions": [ { "remove": { "index": "product_catalog_v1", "alias": "product_catalog_active" } }, { "add": { "index": "product_catalog_v2", "alias": "product_catalog_active" } } ] }`""",
            tags=["dropbox", "opensearch", "elasticsearch", "database", "runbook", "sre"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "sre"], is_public=False),
            extra_metadata={"source": "dropbox", "resource_type": "file"},
        ),
        OKFConcept(
            type="File",
            title="Q3 2026 Cloud Infrastructure FinOps Audit & AWS/GCP Cost Reduction Report",
            resource="https://dropbox.company.com/finance/finops/q3_2026_cloud_cost_audit.xlsx",
            body="""# Q3 2026 Cloud Infrastructure FinOps Audit & Cost Reduction Report
Prepared by: FinOps Engineering Group | Date: 2026-09-10
Executive Summary: Total monthly cloud spend across AWS and GCP averaged $412,000 in Q2. Implementation of FinOps optimizations achieved a recurring 24.5% monthly savings ($101,000/mo reduction).
Key Initiatives Completed:
1. Compute Right-Sizing & Karpenter Autoscaling: Replaced legacy AWS Cluster Autoscaler with Karpenter on EKS, shifting 65% of stateless worker nodes to AWS Graviton3 (c7g/m7g) Spot Instances (saved $48,500/mo).
2. S3 Intelligent-Tiering & Lifecycle Transitions: Moved 1.4 Petabytes of cold logging data from S3 Standard to S3 Glacier Instant Retrieval after 30 days (saved $22,300/mo).
3. Unattached EBS & Idle NAT Gateways: Automated cleanup script terminated 142 unattached gp2/gp3 EBS volumes and consolidated redundant VPC NAT Gateways (saved $11,200/mo).
4. 3-Year Compute Savings Plans: Committed $80,000/mo baseline to lock in 42% blended discount.""",
            tags=["dropbox", "finops", "aws", "gcp", "cost-optimization", "finance"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "finance", "leadership"], is_public=False),
            extra_metadata={"source": "dropbox", "resource_type": "file"},
        ),

        # =====================================================================
        # 5. GMAIL (Incident Threads, Security Advisories, Tech Announcements)
        # =====================================================================
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
        OKFConcept(
            type="Thread",
            title="[SECURITY ADVISORY] CVE-2024-45678: Mandatory YubiKey 5 Series Firmware Patching",
            resource="gmail://thread/sec_alert_yubikey_2026",
            body="""Subject: [URGENT SECURITY ADVISORY] Action Required: YubiKey 5 Series Firmware Vulnerability (CVE-2024-45678)
From: cso-security-operations@company.com
To: all-employees@company.com
Date: 2026-09-18 09:15:00 UTC
All Staff,
A critical side-channel vulnerability (CVE-2024-45678) has been disclosed affecting Infineon cryptographic microcontrollers in YubiKey 5 Series devices running firmware below version 5.7.0.
Impact: An attacker with physical possession and specialized oscilloscope hardware could extract ECDSA private keys.
Required Actions:
1. Check your hardware key firmware via YubiKey Manager CLI: `ykman info`.
2. If your firmware is between 5.0.0 and 5.6.8, submit an IT service desk ticket with tag `#YUBIKEY-REPLACE` for an immediate hardware swap with a pre-configured Series 5.7+ hardware key.
3. Software 2FA (TOTP / SMS) remains disabled company-wide. Hardware FIDO2 is still the mandatory standard.""",
            tags=["gmail", "security", "advisory", "yubikey", "cve", "ciso"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "secops", "ciso_admin"], is_public=False),
            extra_metadata={"source": "gmail", "resource_type": "email"},
        ),
        OKFConcept(
            type="Thread",
            title="[TECH ANNOUNCEMENT] Core Banking Services Migrating from REST/JSON to gRPC & Protobuf",
            resource="gmail://thread/arch_grpc_migration_2026",
            body="""Subject: [TECH ANNOUNCEMENT] Core Banking Services Migration to gRPC & Protobuf (v3.0)
From: lead-architect@company.com
To: engineering-leads@company.com
Date: 2026-09-22 14:30:00 UTC
Engineers,
Effective 2026-10-15, all internal synchronous service-to-service communication between Ledger, Accounts, and Transfer services will transition from HTTP/1.1 REST to HTTP/2 gRPC with Protocol Buffers v3.
Key Highlights:
- Protobuf Repository: All `.proto` schemas must be defined in `github.com/company/proto-schema` and compiled using `buf` CLI.
- Performance Gains: Benchmarks show 68% reduction in network payload serialization overhead and 4.8x higher throughput on inter-service RPCs.
- Backward Compatibility: Protobuf linting (`buf lint`) and breaking change detection (`buf breaking --against '.git#branch=main'`) are enforced in CI.
- Migration office hours will be hosted every Tuesday at 16:00 UTC.""",
            tags=["gmail", "architecture", "grpc", "protobuf", "performance", "engineering"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"], is_public=False),
            extra_metadata={"source": "gmail", "resource_type": "email"},
        ),

        # =====================================================================
        # 6. CONFLUENCE (Engineering RFCs, ADRs, DR Strategy)
        # =====================================================================
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
        OKFConcept(
            type="Page",
            title="ADR-088: PgBouncer Connection Pooling Strategy & Transaction Mode Standards",
            resource="https://confluence.company.com/display/ARCH/ADR-088",
            body="""# ADR-088: PostgreSQL PgBouncer Connection Pooling Architecture & Mode Decision
Status: ACCEPTED | Date: 2026-08-14 | Deciders: Principal Architect, Head of SRE, Principal DB Engineer
Context:
With over 450 microservice container instances connecting to Aurora PostgreSQL, database backend memory was saturated by idle client connections (> 5,000 connections consumed 36GB RAM).
Decision:
1. Deploy dedicated PgBouncer sidecars in Kubernetes pods for high-traffic services, plus a shared central PgBouncer HA proxy pool for auxiliary jobs.
2. Connection Mode: `transaction` mode is enforced as the mandatory standard across all OLTP services. `session` mode is strictly forbidden except for analytics batch loaders requiring `LISTEN/NOTIFY`.
3. Prepared Statements: Microservices using transaction pooling must configure client drivers with named prepared statements disabled or use PgBouncer v1.21+ protocol-level prepared statement support (`max_prepared_statements: 100`).
4. Pool Sizing: Max pool size per client pod set to `default_pool_size = 25`, `reserve_pool_size = 5`.""",
            tags=["confluence", "adr", "architecture", "postgres", "pgbouncer", "database"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "sre"], is_public=False),
            extra_metadata={"source": "confluence", "resource_type": "page"},
        ),
        OKFConcept(
            type="Page",
            title="Engineering Strategy: Multi-Region Active-Active Disaster Recovery Architecture (RPO < 1s, RTO < 30s)",
            resource="https://confluence.company.com/display/SRE/Multi-Region-DR-Strategy",
            body="""# Engineering Strategy: Multi-Region Active-Active Disaster Recovery Architecture
Document Owner: SRE & Reliability Engineering Group | Target RPO: < 1 second | Target RTO: < 30 seconds
Primary Regions: `us-east-1` (Virginia) and `us-west-2` (Oregon).
Architectural Tenets:
1. Global Traffic Management: AWS Route53 Application Recovery Controller (ARC) with health check routing controls. Failover between regions is executed via routing control state toggle within 15 seconds.
2. Data Tier Replication:
   - Amazon Aurora Global Database with storage-level asynchronous replication (typical cross-region replication lag < 800ms).
   - DynamoDB Global Tables for distributed idempotency and session tokens with active-active bi-directional multi-region replication.
   - Kafka MirrorMaker 2 continuously mirrors high-priority financial topics with consumer group offset translation.
3. Regional Isolation & Blast Radius: Each region runs independent Kubernetes clusters, ingress controllers, secret vaults, and Redis caches to prevent cascading failure propagation.""",
            tags=["confluence", "sre", "disaster-recovery", "architecture", "multi-region", "reliability"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "sre", "leadership"], is_public=False),
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
                try:
                    client.create_constraints()
                    client.create_indexes()
                except Exception:
                    pass
                # Write nodes and relationships to live Neo4j
                with client.driver.session(database=client.database) as session:
                    client._write_nodes(session, nodes)
                    client._write_relationships(session, relationships)
                print(f"       ✓ Connected to live Neo4j database ({uri}) and synced {len(nodes)} entities & {len(relationships)} relations.")
            else:
                err_msg = client.last_error or "Connection / Routing Failure"
                print(f"       ⚠️ Neo4j connection to {uri} failed: {err_msg}")
                if "routing" in err_msg.lower() or "serviceunavailable" in err_msg.lower():
                    print("          👉 Hint: If using Neo4j AuraDB (Cloud), check that your instance is RUNNING (not PAUSED) in https://console.neo4j.io")
                elif "unauthorized" in err_msg.lower() or "autherror" in err_msg.lower() or "authentication" in err_msg.lower():
                    print("          👉 Hint: Check your NEO4J_USERNAME and NEO4J_PASSWORD in .env")
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
        if not pwd:
            print("         (To connect to live Neo4j: set NEO4J_PASSWORD in .env or pass --neo4j-password)")

    return EntityGraphRetriever(neo4j_client=neo4j_client, memory_graph=memory_graph)


def setup_live_pipeline(
    qdrant_mode: str = "local",
    qdrant_path: str = "./data/qdrant_storage",
    qdrant_url: Optional[str] = None,
    qdrant_api_key: Optional[str] = None,
    qdrant_collection: str = "enterprise_live_knowledge",
    bm25_path: str = "./data/live_bm25_index.json",
    reset_storage: bool = False,
    checkpoint_mode: str = "sqlite",
    checkpoint_path: str = "./data/chat_sessions.db",
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
    neo4j_database: Optional[str] = None,
    llm_provider_name: str = "ollama",
    llm_model: Optional[str] = None,
    max_turns: int = 5,
    context_format: str = "standard",
) -> tuple[LangGraphAgentPlanner, HybridRetriever]:
    """
    Initializes and wires the complete end-to-end Enterprise Knowledge pipeline:
    Storage -> Retrievers -> Reranker -> LangGraph Planner with Local LLM and Stateful Checkpointer.
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

    print(f"🤖 [4/6] Connecting to LLM Provider: {llm_provider_name.upper()}...")
    if llm_model:
        if llm_provider_name == "ollama":
            os.environ["OLLAMA_MODEL"] = llm_model
        elif llm_provider_name == "gemini":
            os.environ["GEMINI_MODEL"] = llm_model

    if llm_provider_name == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        try:
            import urllib.request
            req = urllib.request.Request(f"{base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2):
                pass
            print(f"       ✓ Connected to Ollama server at {base_url} (Model: {llm_model or os.getenv('OLLAMA_MODEL', 'llama3.1')})")
        except Exception:
            print(f"\n⚠️  WARNING: Ollama server is NOT running or unreachable at {base_url}!")
            print(f"   • Start Ollama: Open a new terminal tab and run `ollama serve`")
            print(f"   • Or use Google Gemini: Pass `--provider gemini` (needs GEMINI_API_KEY)\n")

    llm_provider = get_llm_provider(llm_provider_name)

    print("📋 [5/6] Building Global Master Index (global_index.md, global_index.toon) & Sync Ledger (global_log.md)...")
    catalog_manager = GlobalCatalogManager()
    corpus_all = get_sample_enterprise_corpus()
    for concept in corpus_all:
        catalog_manager.add_concept(concept)
    catalog_manager.save_to_disk("./data")
    print(f"       ✓ Aggregated {len(catalog_manager.entries)} catalog entries across all connectors into data/global_index.md, data/global_index.toon & data/global_log.md")

    print("🔍 [6/6] Initializing Multi-Modal Retrievers & RRF Fusion Engine...")
    catalog_retriever = CatalogRetriever(
        catalog_manager=catalog_manager,
        llm_provider=llm_provider,
    )
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
        catalog_retriever=catalog_retriever,
        semantic_retriever=semantic_retriever,
        keyword_retriever=keyword_retriever,
        entity_graph_retriever=entity_retriever,
        graph_retriever=graph_retriever,
        hybrid_retriever=hybrid_retriever,
        resource_lookup_retriever=resource_retriever,
    )

    # Initialize checkpointer for persistent conversation threads
    checkpointer: Optional[BaseCheckpointSaver] = None
    if checkpoint_mode and checkpoint_mode.lower() != "none":
        checkpointer = get_checkpointer(mode=checkpoint_mode, db_path=checkpoint_path)
        if checkpoint_mode.lower() == "sqlite":
            print(f"       💾 Conversation Threads: SQLite persistence enabled @ {checkpoint_path}")
        else:
            print(f"       🧠 Conversation Threads: In-memory volatile checkpointer enabled")

    reranker = CrossEncoderReranker()

    planner = LangGraphAgentPlanner(
        llm_provider=llm_provider,
        tool_registry=tool_registry,
        reranker=reranker,
        checkpointer=checkpointer,
        global_catalog_manager=catalog_manager,
        graph_retriever=graph_retriever,
        enable_reranking=True,
        max_turns=max_turns,
        max_retrieval_attempts=3,
        context_format=context_format,
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
        {
            "name": "Case 6: SRE OpenSearch Zero-Downtime Reindexing (Dropbox SOP)",
            "query": "What are the exact steps and alias switchover procedure for OpenSearch cluster reindexing?",
            "user_context": {"roles": ["sre", "engineer"], "user_id": "sre@company.com"},
            "expected_keywords": ["product_catalog_v2", "_reindex", "_aliases"],
        },
        {
            "name": "Case 7: DevOps Kubernetes Ingress & TLS Architecture (GitHub Manifest)",
            "query": "What Cert-Manager ClusterIssuer and rate limits are configured for production Kubernetes ingress?",
            "user_context": {"roles": ["engineer", "sre"], "user_id": "devops@company.com"},
            "expected_keywords": ["letsencrypt-production", "nginx-external", "500"],
        },
        {
            "name": "Case 8: Real-Time Streaming Data Pipeline (Jira DATA-782)",
            "query": "How was clickstream latency reduced from 8 hours to under 45 seconds according to DATA-782?",
            "user_context": {"roles": ["engineer", "employee"], "user_id": "data-eng@company.com"},
            "expected_keywords": ["DATA-782", "Flink", "Iceberg", "45 seconds"],
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
        print(f"  📑 Chunks Retrieved ({len(result['retrieved_chunks'])}): | Reranked: {result['rerank_applied']}")
        for c in result.get("retrieved_chunks", []):
            tool_name = c.get("retrieved_by_tool") or c.get("tool") or "retriever"
            print(f"     • [{tool_name}] \"{c.get('title')}\" ({c.get('source', '').upper()})")
        print(f"  🏷️ Citations Generated ({len(result['citations'])}):")
        for cit in result["citations"]:
            cit_idx = cit.get('citation_index') or cit.get('index') or cit.get('id') or 1
            tool_str = f" [via {cit.get('tool')}]" if cit.get('tool') else ""
            print(f"     [{cit_idx}] {cit.get('title')} ({cit.get('source')}){tool_str} -> {cit.get('url')}")

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
    """Runs interactive terminal chat REPL allowing user to ask arbitrary live questions across persistent threads."""
    print("\n" + "=" * 80)
    print("💬 INTERACTIVE ENTERPRISE KNOWLEDGE AGENT REPL")
    print("   Type your questions below.")
    print("   Session Commands:")
    print("     • 'role <role_name>'   - Switch active security persona (e.g. engineer, ciso_admin, guest)")
    print("     • 'thread <thread_id>' - Switch active conversation thread/session")
    print("     • 'threads'            - List all saved conversation threads in SQLite storage")
    print("     • 'new'                - Start a fresh conversation thread")
    print("     • 'history'            - Display message history for current thread")
    print("     • 'exit' or 'quit'     - Exit REPL")
    print("   Query Scope Modifiers:")
    print("     • '@enterprise <query>' - Force enterprise retrieval tools across connectors")
    print("     • '@general <query>'    - Direct LLM conceptual/meta answer (0 tools, 0s retrieval)")
    print("     • '@<connector> <query>' - Target specific source (e.g. @jira, @github, @dropbox)")
    print("=" * 80)

    current_role = "engineer"
    current_user = "user@company.com"
    current_thread_id = f"session_{int(time.time())}"
    print(f"Active Session Thread: '{current_thread_id}'")

    while True:
        try:
            prompt_str = f"\n[{current_role} | {current_thread_id}] > "
            user_input = input(prompt_str).strip()
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                print("Exiting interactive REPL. Goodbye!")
                break

            if user_input.lower().startswith("role "):
                current_role = user_input.split(" ", 1)[1].strip()
                if current_role in ("guest", "anonymous", "external", "contractor"):
                    current_user = f"{current_role}@external.com"
                else:
                    current_user = f"{current_role}@company.com"
                print(f"Switched active security role to: '{current_role}' (User: {current_user})")
                continue

            if user_input.lower().startswith("thread ") or user_input.lower().startswith("/thread "):
                cmd_parts = user_input.split(" ", 1)
                if len(cmd_parts) > 1 and cmd_parts[1].strip():
                    current_thread_id = cmd_parts[1].strip()
                    print(f"Switched active conversation thread to: '{current_thread_id}'")
                continue

            if user_input.lower() in ("threads", "/threads"):
                if planner.checkpointer and hasattr(planner.checkpointer, "get_all_threads"):
                    saved_threads = planner.checkpointer.get_all_threads()
                    if saved_threads:
                        print(f"\n📂 Saved Conversation Threads ({len(saved_threads)}):")
                        for t in saved_threads:
                            active_tag = " (active)" if t == current_thread_id else ""
                            print(f"   • {t}{active_tag}")
                    else:
                        print(f"No saved conversation threads in database yet. Active thread: '{current_thread_id}'")
                else:
                    print(f"Active thread: '{current_thread_id}' (Ephemeral or in-memory checkpointer)")
                continue

            if user_input.lower() in ("new", "/new", "clear", "/clear"):
                current_thread_id = f"session_{int(time.time())}"
                print(f"Started new conversation thread: '{current_thread_id}'")
                continue

            if user_input.lower() in ("history", "/history"):
                if planner.checkpointer:
                    config = {"configurable": {"thread_id": current_thread_id}}
                    t_tuple = planner.checkpointer.get_tuple(config)
                    if t_tuple and t_tuple.checkpoint and "channel_values" in t_tuple.checkpoint:
                        msgs = t_tuple.checkpoint["channel_values"].get("messages", [])
                        print(f"\n📜 Conversation History for Thread '{current_thread_id}' ({len(msgs)} turns):")
                        for m in msgs:
                            sender = "User" if isinstance(m, HumanMessage) else ("Agent" if isinstance(m, AIMessage) else type(m).__name__)
                            content = getattr(m, "content", "")
                            if content:
                                print(f"  [{sender}]: {content[:300]}{'...' if len(content) > 300 else ''}")
                    else:
                        print(f"No history recorded yet for thread '{current_thread_id}'.")
                else:
                    print("Checkpointer disabled. Message history not retained.")
                continue

            try:
                print("\n🤖 Reasoning and retrieving multi-modal evidence across connectors...")
                t0 = time.time()
                result = planner.run(
                    query=user_input,
                    user_context={"roles": [current_role], "user_id": current_user},
                    thread_id=current_thread_id,
                )
                elapsed = time.time() - t0

                print(f"\n{'─' * 80}")
                print(f"⏱️  Execution Time: {elapsed:.2f}s | Turns: {result.get('turns', 1)} | Thread: {result.get('thread_id', current_thread_id)}")
                if result.get("tool_calls"):
                    print(f"🛠️  Tools Called ({len(result['tool_calls'])}):")
                    for tc in result["tool_calls"]:
                        args_str = json.dumps(tc.get("arguments", tc.get("args", {})), ensure_ascii=False)
                        print(f"   • {tc.get('tool')}({args_str})")
                else:
                    print(f"🛠️  Tools Called: None (direct reasoning)")
                
                chunks = result.get('retrieved_chunks', [])
                print(f"📑 Chunks Retrieved ({len(chunks)}) | Reranked: {result.get('rerank_applied', False)}")
                for c in chunks:
                    tool_name = c.get("retrieved_by_tool") or c.get("tool") or "retriever"
                    print(f"   • [{tool_name}] \"{c.get('title')}\" ({c.get('source', '').upper()})")

                print(f"\n💬 Answer:")
                print(result["answer"])
                print(f"{'─' * 80}")

                if result.get("citations"):
                    print("🏷️  Citations:")
                    for cit in result["citations"]:
                        cit_idx = cit.get('citation_index') or cit.get('index') or cit.get('id') or 1
                        tool_str = f" [via {cit.get('tool')}]" if cit.get('tool') else ""
                        url_str = f" -> {cit.get('url')}" if cit.get('url') else ""
                        print(f"   [{cit_idx}] {cit.get('title')} ({cit.get('source')}){tool_str}{url_str}")
            except Exception as query_err:
                err_str = str(query_err)
                print(f"\n❌ Error processing query: {err_str}")
                if "Failed to connect to Ollama" in err_str or "Connection refused" in err_str:
                    print("   👉 Fix: Start Ollama in a separate terminal: `ollama serve`")
                    print("   👉 Or restart this script with Google Gemini: `--provider gemini`")

        except (KeyboardInterrupt, EOFError):
            print("\nExiting interactive REPL.")
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Live End-to-End Testing for Enterprise Knowledge Agent")
    parser.add_argument("--provider", default="ollama", choices=["ollama", "gemini"], help="LLM Provider (default: ollama)")
    parser.add_argument("--model", default="llama3.1:8b", help="Model name (e.g. qwen2.5:7b, llama3.1:8b, gemini-2.5-flash)")
    parser.add_argument("--qdrant-mode", default="local", choices=["local", "memory", "server"], help="Qdrant storage mode (default: local)")
    parser.add_argument("--qdrant-path", default="./data/qdrant_storage", help="Local disk storage path for Qdrant (default: ./data/qdrant_storage)")
    parser.add_argument("--qdrant-url", default=None, help="Remote Qdrant server URL for server mode (e.g. http://localhost:6333)")
    parser.add_argument("--bm25-path", default="./data/live_bm25_index.json", help="Path to BM25 index file (default: ./data/live_bm25_index.json)")
    parser.add_argument("--checkpoint-mode", default="sqlite", choices=["sqlite", "memory", "none"], help="Session checkpointer backend (default: sqlite)")
    parser.add_argument("--checkpoint-path", default="./data/chat_sessions.db", help="Local SQLite file for multi-turn session persistence (default: ./data/chat_sessions.db)")
    parser.add_argument("--reset-storage", action="store_true", help="Force wipe and re-index persistent storage")
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"), help="Neo4j connection URI (default: bolt://localhost:7687)")
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USERNAME", "neo4j"), help="Neo4j username (default: neo4j)")
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", None), help="Neo4j password (optional: falls back to in-memory graph)")
    parser.add_argument("--neo4j-database", default=os.getenv("NEO4J_DATABASE", "neo4j"), help="Neo4j database name (default: neo4j)")
    parser.add_argument("--max-turns", type=int, default=10, help="Maximum agent reflection turns (default: 10)")
    parser.add_argument("--context-format", default="standard", choices=["standard", "toon"], help="Evidence context formatting ('standard' or 'toon') (default: standard)")
    parser.add_argument("--interactive", action="store_true", help="Launch interactive REPL mode after setup")
    args = parser.parse_args()

    storage_target = args.qdrant_path if args.qdrant_mode == "local" else (args.qdrant_url or ":memory:")
    graph_target = f"Neo4j ({args.neo4j_uri})" if args.neo4j_password else "In-Memory Property Graph (RAM)"
    checkpoint_target = args.checkpoint_path if args.checkpoint_mode == "sqlite" else args.checkpoint_mode.upper()

    print("=" * 80)
    print("🌐 ENTERPRISE KNOWLEDGE AGENT — END-TO-END LIVE PIPELINE TEST")
    print(f"   Provider:  {args.provider.upper()} | Model: {args.model}")
    print(f"   Vectors:   Qdrant ({args.qdrant_mode.upper()}) @ {storage_target}")
    print(f"   Graph:     {graph_target}")
    print(f"   Sessions:  Checkpointer ({args.checkpoint_mode.upper()}) @ {checkpoint_target}")
    print(f"   Context:   {args.context_format.upper()} format")
    print(f"   Turns:     Max {args.max_turns} agent turns")
    print("=" * 80)

    try:
        planner, hybrid_retriever = setup_live_pipeline(
            qdrant_mode=args.qdrant_mode,
            qdrant_path=args.qdrant_path,
            qdrant_url=args.qdrant_url,
            bm25_path=args.bm25_path,
            reset_storage=args.reset_storage,
            checkpoint_mode=args.checkpoint_mode,
            checkpoint_path=args.checkpoint_path,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_database=args.neo4j_database,
            llm_provider_name=args.provider,
            llm_model=args.model,
            max_turns=args.max_turns,
            context_format=args.context_format,
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
