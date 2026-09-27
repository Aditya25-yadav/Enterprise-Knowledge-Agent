"""
Global Catalog Manager & Knowledge Aggregator (Phase 9+).

Aggregates concepts across all 6 enterprise connectors (GitHub, Jira, Notion, Dropbox,
Gmail, Confluence) into unified, enriched Master Catalog files:
  1. global_index.md: High-density knowledge catalog categorized by Business Domain,
     Key Entity Tokens, RBAC permissions, and Trust Tiers.
  2. global_log.md: Append-only chronological audit ledger with microsecond/second
     ISO-8601 UTC timestamps, SHA-256 hashes, sync batch IDs, and status.

Enables Map-First / Progressive Disclosure navigation for the Enterprise Knowledge Agent.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from backend.models.okf import OKFConcept, OKFPermissions


@dataclass
class CatalogEntry:
    """
    Enriched catalog metadata for a single enterprise knowledge asset.
    """
    title: str
    source: str                                # 'github', 'jira', 'notion', 'dropbox', 'gmail', 'confluence'
    resource_type: str                         # 'file', 'issue', 'page', 'runbook', 'email_thread', 'rfc'
    resource_uri: str                          # Canonical URI or direct URL
    domain: str                                # 'Payments', 'Infrastructure', 'Security', 'Architecture', etc.
    summary: str                               # Concise 1-2 sentence description
    key_entities: List[str] = field(default_factory=list) # Extracted tokens (PR #s, Jira keys, symbols)
    allowed_roles: List[str] = field(default_factory=lambda: ["employee"])
    allowed_users: List[str] = field(default_factory=list)
    is_public: bool = False
    trust_tier: str = "human-reviewed"
    status: str = "stable"
    last_modified: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    content_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_toon(self, index: Optional[int] = None) -> str:
        """Serializes this catalog entry to compact TOON format."""
        from backend.serialization.toon import serialize_toon_catalog_entry
        return serialize_toon_catalog_entry(self, index=index)


@dataclass
class CatalogLogEvent:
    """
    Detailed audit and synchronization event in the global knowledge ledger.
    """
    timestamp: str                             # Exact ISO-8601 UTC timestamp
    platform: str                              # Connector platform
    action: str                                # 'INGESTION', 'UPDATE', 'SYNC', 'ROTATION'
    resource_uri: str
    title: str
    content_hash: str
    actor: str = "process:global-sync/v1.0"
    status: str = "SUCCESS"
    details: Optional[str] = None
    sync_batch_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class GlobalCatalogManager:
    """
    Maintains, updates, and serializes the global master index and sync ledger
    across all enterprise data sources.
    """

    DEFAULT_INDEX_PATH = "./data/global_index.md"
    DEFAULT_LOG_PATH = "./data/global_log.md"

    def __init__(
        self,
        index_path: Optional[str] = None,
        log_path: Optional[str] = None,
    ) -> None:
        self.index_path = index_path or self.DEFAULT_INDEX_PATH
        self.log_path = log_path or self.DEFAULT_LOG_PATH
        self.entries: Dict[str, CatalogEntry] = {}       # resource_uri -> CatalogEntry
        self.log_events: List[CatalogLogEvent] = []

    # ── Concept Ingestion & Entity Enrichment ────────────────────────────────

    def add_concept(
        self,
        concept: OKFConcept,
        domain: Optional[str] = None,
        sync_batch_id: Optional[str] = None,
    ) -> CatalogEntry:
        """
        Extracts rich metadata, infers business domain and key entities,
        and registers the concept into the master catalog and audit log.
        """
        uri = concept.resource or f"concept:{concept.type.lower()}:{concept.title or 'untitled'}"
        title = concept.title or "Untitled Document"
        source = self._infer_source(concept)
        resource_type = concept.type.lower()
        
        # 1. Infer Business Domain
        inferred_domain = domain or self._infer_domain(concept)
        
        # 2. Extract Key Entity Tokens
        key_entities = self._extract_key_entities(concept)
        
        # 3. Resolve Permissions & Trust
        perms = concept.permissions if isinstance(concept.permissions, OKFPermissions) else OKFPermissions()
        allowed_roles = list(perms.allowed_roles) if perms.allowed_roles else ["employee"]
        allowed_users = list(perms.allowed_users) if perms.allowed_users else []
        is_public = perms.is_public
        
        # 4. Multi-line enriched summary description
        summary = concept.description or ""
        if not summary and concept.body:
            # Clean markdown formatting and extract first meaningful lines / operational steps
            raw_lines = [l.strip().lstrip("#").strip() for l in concept.body.strip().splitlines() if l.strip()]
            joined_summary = " ".join(raw_lines[:5])
            joined_summary = re.sub(r"\s+", " ", joined_summary).strip()
            summary = joined_summary[:320] + ("..." if len(joined_summary) > 320 else "")
            
        # 5. Timestamps & Hash
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        last_mod = concept.updated_at or concept.created_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        c_hash = concept.content_hash or hashlib.sha256((concept.body or "").encode("utf-8")).hexdigest()

        entry = CatalogEntry(
            title=title,
            source=source,
            resource_type=resource_type,
            resource_uri=uri,
            domain=inferred_domain,
            summary=summary,
            key_entities=key_entities,
            allowed_roles=allowed_roles,
            allowed_users=allowed_users,
            is_public=is_public,
            trust_tier=concept.trust_tier,
            status=concept.status,
            last_modified=last_mod,
            content_hash=c_hash,
        )

        self.entries[uri] = entry

        # Record event in global log
        batch_id = sync_batch_id or datetime.datetime.now(datetime.timezone.utc).strftime("batch_%Y%m%d_%H%M%S")
        log_event = CatalogLogEvent(
            timestamp=now_iso,
            platform=source.capitalize(),
            action="INGESTION" if uri not in self.entries else "UPDATE",
            resource_uri=uri,
            title=title,
            content_hash=c_hash,
            actor=concept.generated.by if concept.generated else f"process:{source}-sync/v1.0",
            status="SUCCESS",
            details=f"Domain: {inferred_domain} | Entities: {len(key_entities)}",
            sync_batch_id=batch_id,
        )
        self.log_events.append(log_event)

        return entry

    def add_concepts(
        self,
        concepts: List[OKFConcept],
        sync_batch_id: Optional[str] = None,
    ) -> List[CatalogEntry]:
        """Batch registers multiple concepts."""
        batch_id = sync_batch_id or datetime.datetime.now(datetime.timezone.utc).strftime("batch_%Y%m%d_%H%M%S")
        return [self.add_concept(c, sync_batch_id=batch_id) for c in concepts]

    # ── Inference Helpers ────────────────────────────────────────────────────

    def _infer_source(self, concept: OKFConcept) -> str:
        """Infers source platform name from concept tags or resource URI."""
        if concept.tags:
            for t in concept.tags:
                t_low = t.lower()
                if t_low in ("github", "jira", "notion", "dropbox", "gmail", "confluence"):
                    return t_low
        res = (concept.resource or "").lower()
        if "github" in res:
            return "github"
        if "jira" in res:
            return "jira"
        if "notion" in res:
            return "notion"
        if "dropbox" in res:
            return "dropbox"
        if "gmail" in res or "mail.google" in res:
            return "gmail"
        if "confluence" in res:
            return "confluence"
        return "general"

    def _infer_domain(self, concept: OKFConcept) -> str:
        """Categorizes document into an enterprise business/technical domain."""
        text = f"{concept.title or ''} {concept.body or ''} {' '.join(concept.tags or [])}".lower()
        
        if any(k in text for k in ("payment", "checkout", "3ds", "charge", "refund", "currency", "idempotency")):
            return "Payments & Checkout"
        if any(k in text for k in ("kms", "vault", "ciso", "secret", "encryption", "aes-256", "security", "jwt", "yubikey", "pkce", "cloudflare", "okta")):
            return "Security & Cryptography"
        if any(k in text for k in ("postgres", "failover", "patronictl", "pgbouncer", "disaster recovery", "database", "sre", "runbook", "opensearch", "reindex", "aurora", "kubernetes", "ingress")):
            return "Infrastructure & Disaster Recovery"
        if any(k in text for k in ("kafka", "event", "rfc", "topic", "schema registry", "producer", "consumer", "flink", "iceberg", "grpc", "protobuf")):
            return "Data Platform & Messaging"
        if any(k in text for k in ("incident", "post-mortem", "outage", "latency spike", "oncall", "alert")):
            return "Incident Response & Reliability"
            
        return "Core Engineering & Platform"

    def _extract_key_entities(self, concept: OKFConcept) -> List[str]:
        """Extracts technical tokens, issue IDs, PR numbers, and code symbols."""
        text = f"{concept.title or ''}\n{concept.body or ''}"
        entities: Set[str] = set()

        # 1. Jira Issue Keys (e.g. PAY-928, INFRA-102, SEC-1104, DATA-782)
        jira_matches = re.findall(r"\b[A-Z]{2,10}-\d+\b", text)
        entities.update(jira_matches)

        # 2. GitHub PR / Issue references (e.g. PR #142, #142)
        pr_matches = re.findall(r"(?:PR\s*#?|#)(\d+)\b", text, re.IGNORECASE)
        for pr in pr_matches:
            entities.add(f"PR #{pr}")

        # 3. CVEs, RFCs, and ADRs (e.g. CVE-2024-45678, RFC-402, ADR-088)
        cve_matches = re.findall(r"\bCVE-\d{4}-\d+\b", text, re.IGNORECASE)
        entities.update(cve_matches)
        rfc_matches = re.findall(r"\b(?:RFC|ADR)-?\d+\b", text, re.IGNORECASE)
        entities.update(rfc_matches)

        # 4. API Endpoints & Routes (e.g. /v1/payments/initiate, /healthz, /_aliases)
        endpoint_matches = re.findall(r"/(?:v\d+/)?(?:[a-zA-Z0-9_\-]+/)*[a-zA-Z0-9_\-]+", text)
        entities.update([e for e in endpoint_matches if len(e) > 3][:5])

        # 5. Technical Frameworks, CLIs & Infrastructure Symbols
        known_symbols = (
            "patronictl", "PgBouncer", "Kafka", "Idempotency-Key", "AES-256", "Vault", "3DS",
            "Flink", "Iceberg", "OpenSearch", "Elasticsearch", "Cert-Manager", "PKCE", "OAuth 2.0",
            "YubiKey", "FIDO2", "gRPC", "Protobuf", "Karpenter", "Aurora", "Cloudflare", "Okta",
            "Jamf", "FileVault", "NGINX", "PostgreSQL", "BigQuery", "S3", "Redis", "SonarQube",
            "unresponsive", "failover", "switchover", "reindex", "ClusterIssuer", "SOP"
        )
        for symbol in known_symbols:
            if symbol.lower() in text.lower():
                entities.add(symbol)

        # 6. Extract author / login if mentioned
        for user in ("alice", "bob", "checkout-oncall", "incident-commander", "lead-architect", "lead-data-engineer"):
            if user in text.lower():
                entities.add(user)

        return sorted(list(entities))

    # ── Markdown Serialization ───────────────────────────────────────────────

    def generate_global_index_markdown(self) -> str:
        """
        Renders the complete global master index into an enriched, structured Markdown document.
        """
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "# Enterprise Knowledge Global Master Index (v0.2)",
            f"> Generated: `{now_str}` | Total Assets: `{len(self.entries)}`",
            "",
            "This catalog provides an enterprise-wide topology map across GitHub, Jira, Notion, Dropbox, Gmail, and Confluence.",
            "",
        ]

        # Group entries by Domain
        domains: Dict[str, List[CatalogEntry]] = {}
        for entry in self.entries.values():
            domains.setdefault(entry.domain, []).append(entry)

        for domain_name in sorted(domains.keys()):
            lines.append(f"## Domain: {domain_name}")
            lines.append("")
            for entry in sorted(domains[domain_name], key=lambda e: e.title):
                lines.append(f"- **Title**: {entry.title}")
                lines.append(f"  - **Source**: `{entry.source.upper()}` | **Type**: `{entry.resource_type}`")
                lines.append(f"  - **Resource**: `{entry.resource_uri}`")
                if entry.summary:
                    lines.append(f"  - **Summary**: {entry.summary}")
                if entry.key_entities:
                    entities_str = ", ".join(f"`{e}`" for e in entry.key_entities[:8])
                    lines.append(f"  - **Key Entities**: {entities_str}")
                roles_str = ", ".join(entry.allowed_roles)
                lines.append(f"  - **Allowed Roles**: `[{roles_str}]` | **Trust Tier**: `{entry.trust_tier}` | **Status**: `{entry.status}`")
                lines.append(f"  - **Last Modified**: `{entry.last_modified}`")
                lines.append("")

        return "\n".join(lines)

    def generate_global_index_toon(self) -> str:
        """
        Renders the complete global master index into high-density TOON format,
        compressing manifest metadata to minimize prompt tokens.
        """
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            f"# Enterprise Knowledge Global Master Index (TOON v0.2) | Generated: {now_str}",
            f"> Total Assets: {len(self.entries)}",
            "",
        ]
        for idx, entry in enumerate(sorted(self.entries.values(), key=lambda e: (e.domain, e.title)), 1):
            lines.append(entry.to_toon(index=idx))
            lines.append("")

        return "\n".join(lines).strip()

    def generate_domain_topology_toon(self) -> str:
        """
        Renders an ultra-compact Domain Topology Map in TOON Tabular Array format,
        collapsing large numbers of assets into fixed-size domain summaries (<=200-300 tokens).
        """
        from backend.serialization.toon import serialize_toon_table

        domains: Dict[str, Dict[str, Any]] = {}
        for entry in self.entries.values():
            d = entry.domain or "Core Engineering"
            if d not in domains:
                domains[d] = {
                    "domain": d,
                    "doc_count": 0,
                    "connectors": set(),
                    "top_entities": set(),
                }
            domains[d]["doc_count"] += 1
            if entry.source:
                domains[d]["connectors"].add(entry.source)
            for entity in (entry.key_entities or [])[:3]:
                domains[d]["top_entities"].add(entity)

        rows = []
        for d_name, d_data in sorted(domains.items()):
            rows.append({
                "domain": d_name,
                "doc_count": d_data["doc_count"],
                "connectors": ",".join(sorted(d_data["connectors"])),
                "primary_topics": ", ".join(list(d_data["top_entities"])[:5]) or d_name,
            })

        table_str = serialize_toon_table(rows, name="domains", fields=["domain", "doc_count", "connectors", "primary_topics"])
        return f"[ENTERPRISE DOMAIN TOPOLOGY (TOON v0.2)]\n{table_str}"

    def get_adaptive_global_manifest(self, max_tokens: int = 400) -> str:
        """
        Returns an adaptive global manifest suitable for zero-shot prompt injection.
        - Uses detailed document-level TOON table if corpus <= 30 docs and <= max_tokens.
        - Automatically collapses into Domain Topology Map for larger corpus.
        """
        from backend.serialization.toon import estimate_token_count

        if len(self.entries) <= 30:
            doc_manifest = self.generate_global_index_toon()
            if estimate_token_count(doc_manifest) <= max_tokens:
                return doc_manifest

        return self.generate_domain_topology_toon()

    def generate_global_log_markdown(self) -> str:
        """
        Renders the global sync and update ledger with precise microsecond/second UTC ISO timestamps.
        """
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "# Enterprise Knowledge Global Ingestion & Sync Ledger",
            f"> Last Audit Run: `{now_str}` | Total Events: `{len(self.log_events)}`",
            "",
        ]

        # Group by sync batch or display in reverse chronological order
        batches: Dict[str, List[CatalogLogEvent]] = {}
        for event in reversed(self.log_events):
            batch_key = event.sync_batch_id or "default_batch"
            batches.setdefault(batch_key, []).append(event)

        for batch_id, events in batches.items():
            first_ts = events[0].timestamp if events else now_str
            lines.append(f"## Sync Batch: `{batch_id}` ({first_ts})")
            for ev in events:
                lines.append(f"- **Timestamp**: `{ev.timestamp}` | **Platform**: `{ev.platform}` | **Action**: `{ev.action}`")
                lines.append(f"  - **Resource**: `{ev.resource_uri}`")
                lines.append(f"  - **Title**: {ev.title}")
                lines.append(f"  - **Content Hash**: `{ev.content_hash[:16]}...`")
                lines.append(f"  - **Actor**: `{ev.actor}` | **Status**: `{ev.status}`")
                if ev.details:
                    lines.append(f"  - **Details**: {ev.details}")
                lines.append("")

        return "\n".join(lines)

    def save_to_disk(
        self,
        index_path: Optional[str] = None,
        log_path: Optional[str] = None,
        save_toon: bool = True,
    ) -> tuple[str, str]:
        """Writes global_index.md, global_log.md, and optionally global_index.toon to disk."""
        if index_path and (Path(index_path).is_dir() or not index_path.endswith(".md")):
            target_dir = Path(index_path)
            target_dir.mkdir(parents=True, exist_ok=True)
            target_index = str(target_dir / "global_index.md")
            target_log = str(target_dir / "global_log.md") if not log_path else log_path
            target_toon = str(target_dir / "global_index.toon")
        else:
            target_index = index_path or self.index_path
            target_log = log_path or self.log_path
            target_toon = str(Path(target_index).with_suffix(".toon"))

        Path(target_index).parent.mkdir(parents=True, exist_ok=True)
        Path(target_log).parent.mkdir(parents=True, exist_ok=True)

        with open(target_index, "w", encoding="utf-8") as f:
            f.write(self.generate_global_index_markdown())

        with open(target_log, "w", encoding="utf-8") as f:
            f.write(self.generate_global_log_markdown())

        if save_toon:
            with open(target_toon, "w", encoding="utf-8") as f:
                f.write(self.generate_global_index_toon())

        return target_index, target_log

    def get_entries_for_roles(self, user_roles: List[str]) -> List[CatalogEntry]:
        """Filters catalog entries accessible by the given user roles."""
        from backend.security.hierarchy import RoleHierarchy

        hierarchy = RoleHierarchy()
        expanded_roles = hierarchy.expand_roles(user_roles)

        accessible: List[CatalogEntry] = []
        for entry in self.entries.values():
            if entry.is_public:
                accessible.append(entry)
                continue

            # Check if any allowed role of this entry is in user's expanded roles
            if any(role.lower() in expanded_roles for role in entry.allowed_roles):
                accessible.append(entry)

        return accessible
