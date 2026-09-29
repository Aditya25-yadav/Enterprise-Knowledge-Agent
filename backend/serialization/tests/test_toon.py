"""
Unit Tests for TOON (Token-Oriented Object Notation) Engine.
"""

import json
import pytest

from backend.generation.context_builder import ContextBuilder
from backend.ingestion.catalog_aggregator import CatalogEntry, GlobalCatalogManager
from backend.models.okf import OKFConcept, OKFPermissions
from backend.serialization.toon import (
    calculate_token_savings,
    deserialize_toon_catalog_entry,
    deserialize_toon_chunk,
    deserialize_toon_table,
    estimate_token_count,
    serialize_toon_catalog_entry,
    serialize_toon_chunk,
    serialize_toon_context,
    serialize_toon_table,
)


def test_serialize_and_deserialize_toon_chunk():
    chunk = {
        "chunk_id": "chunk_dr_v3_step2",
        "source": "dropbox",
        "domain": "SRE",
        "allowed_roles": ["employee", "engineer", "sre"],
        "resource_type": "Runbook",
        "score": 0.95,
        "title": "Disaster Recovery & Database Failover SOP",
        "section_heading": "Step 2 > Leader switchover",
        "section_path": ["Step 2", "Leader switchover"],
        "url": "https://dropbox.company.com/dr_failover.docx",
        "sequence": {"step": 2, "total_steps": 4},
        "text": "Initiate automated leader switchover: patronictl failover cluster-prod --candidate db-replica-02.",
    }

    toon_str = serialize_toon_chunk(chunk, index=1)
    assert "[#1|C:chunk_dr_v3_step2|S:dropbox|D:SRE|R:employee,engineer,sre|T:Runbook|K:0.95|P:Step 2 > Leader switchover|U:https://dropbox.company.com/dr_failover.docx|Seq:2/4]" in toon_str
    assert "# Disaster Recovery & Database Failover SOP" in toon_str
    assert "patronictl failover cluster-prod" in toon_str

    deserialized = deserialize_toon_chunk(toon_str)
    assert deserialized["chunk_id"] == "chunk_dr_v3_step2"
    assert deserialized["source"] == "dropbox"
    assert deserialized["domain"] == "SRE"
    assert deserialized["allowed_roles"] == ["employee", "engineer", "sre"]
    assert deserialized["resource_type"] == "Runbook"
    assert deserialized["score"] == 0.95
    assert deserialized["title"] == "Disaster Recovery & Database Failover SOP"
    assert deserialized["section_heading"] == "Step 2 > Leader switchover"
    assert deserialized["url"] == "https://dropbox.company.com/dr_failover.docx"
    assert deserialized["sequence"] == {"step": 2, "total_steps": 4}
    assert "patronictl failover cluster-prod" in deserialized["text"]


def test_serialize_and_deserialize_toon_catalog_entry():
    entry = CatalogEntry(
        title="PAY-928: 3DS Authentication Timeout in Checkout Flow",
        source="jira",
        resource_type="issue",
        resource_uri="https://jira.company.com/browse/PAY-928",
        domain="Payments",
        summary="Upstream 3DS gateway timeout (15s) vs client TTL (10s) causing ECONNREFUSED. Mitigated in PR #142.",
        key_entities=["PAY-928", "142", "alice", "timeout"],
        allowed_roles=["employee", "engineer"],
    )

    toon_str = serialize_toon_catalog_entry(entry, index=1)
    assert "[#1|S:jira|D:Payments|R:employee,engineer|T:issue|U:https://jira.company.com/browse/PAY-928|E:PAY-928,142,alice,timeout]" in toon_str
    assert "# PAY-928: 3DS Authentication Timeout in Checkout Flow" in toon_str
    assert "> Upstream 3DS gateway timeout" in toon_str

    deserialized = deserialize_toon_catalog_entry(toon_str)
    assert deserialized["source"] == "jira"
    assert deserialized["domain"] == "Payments"
    assert deserialized["allowed_roles"] == ["employee", "engineer"]
    assert deserialized["resource_uri"] == "https://jira.company.com/browse/PAY-928"
    assert deserialized["key_entities"] == ["PAY-928", "142", "alice", "timeout"]
    assert deserialized["title"] == "PAY-928: 3DS Authentication Timeout in Checkout Flow"
    assert "Upstream 3DS gateway timeout" in deserialized["summary"]


def test_serialize_toon_context():
    chunks = [
        {
            "chunk_id": "c1",
            "source": "jira",
            "title": "PAY-928: 3DS Timeout",
            "text": "Root cause was 15s gateway timeout.",
            "url": "https://jira.com/PAY-928",
            "retrieved_by_tool": "hybrid_search",
        },
        {
            "chunk_id": "c2",
            "source": "gmail",
            "title": "Post-Mortem Latency Spike",
            "text": "Mitigated in PR #142.",
            "url": "gmail://thread/123",
            "retrieved_by_tool": "hybrid_search",
        },
    ]

    context_str, citations = serialize_toon_context(chunks, numbered=True)
    assert "[#1|C:c1|S:jira" in context_str
    assert "[#2|C:c2|S:gmail" in context_str
    assert len(citations) == 2
    assert citations[0]["citation_index"] == 1
    assert citations[0]["title"] == "PAY-928: 3DS Timeout"
    assert citations[0]["tool"] == "hybrid_search"
    assert citations[1]["citation_index"] == 2


def test_context_builder_toon_integration():
    chunks = [
        {
            "chunk_id": "c1",
            "source": "github",
            "title": "Payments API Spec",
            "text": "Send POST to /v1/payments/initiate with Idempotency-Key header.",
            "url": "https://github.com/company/payments",
            "tags": ["payments", "api"],
        }
    ]

    std_context, std_citations = ContextBuilder.build_context(chunks, format="standard")
    assert "[1] [GITHUB] Payments API Spec" in std_context
    assert len(std_citations) == 1

    toon_context, toon_citations = ContextBuilder.build_context(chunks, format="toon")
    assert "[#1|C:c1|S:github|D:Payments" in toon_context
    assert "# Payments API Spec" in toon_context
    assert len(toon_citations) == 1
    assert toon_citations[0]["citation_index"] == 1


def test_token_savings_metric():
    raw_json = json.dumps({
        "chunk_id": "chunk_998877_long_identifier_for_testing",
        "source_platform": "dropbox_enterprise_documents",
        "domain": "Site Reliability Engineering and Infrastructure Operations",
        "allowed_roles": ["employee", "engineer", "sre", "platform_admin"],
        "breadcrumbs": ["Infrastructure", "Runbooks", "Postgres", "Disaster Recovery SOP v3.2"],
        "url": "https://dropbox.company.com/engineering/runbooks/dr_failover_v3.docx",
        "metadata": {
            "created_at": "2026-09-20T10:00:00Z",
            "updated_at": "2026-09-24T12:00:00Z",
            "author": "sre-team@company.com",
            "status": "stable",
        },
        "content": "Check primary replication lag using patronictl topology. If lag > 60s failover to replica-02.",
    }, indent=2)

    toon_repr = serialize_toon_chunk({
        "chunk_id": "chunk_998877",
        "source": "dropbox",
        "domain": "SRE",
        "allowed_roles": ["sre", "engineer"],
        "title": "Postgres DR Failover SOP",
        "section_heading": "Failover Steps",
        "url": "https://dropbox.company.com/dr.docx",
        "text": "Check primary replication lag using patronictl topology. If lag > 60s failover to replica-02.",
    }, index=1)

    metrics = calculate_token_savings(raw_json, toon_repr)
    assert metrics["standard_tokens"] > metrics["toon_tokens"]
    assert metrics["percent_saved"] >= 40.0
    assert metrics["tokens_saved"] > 0


def test_okf_concept_to_and_from_toon():
    concept = OKFConcept(
        type="Playbook",
        title="OpenSearch Reindexing SOP",
        resource="https://dropbox.company.com/opensearch_sop.pdf",
        tags=["dropbox", "opensearch", "sre"],
        permissions=OKFPermissions(allowed_roles=["sre", "engineer"]),
        body="Step 1: PUT /product_catalog_v2\nStep 2: POST /_reindex\nStep 3: POST /_aliases",
        extra_metadata={"source": "dropbox", "domain": "SRE"},
    )

    toon_str = concept.to_toon()
    assert "S:dropbox" in toon_str
    assert "T:Playbook" in toon_str
    assert "OpenSearch Reindexing SOP" in toon_str

    restored = OKFConcept.from_toon(toon_str)
    assert restored.title == "OpenSearch Reindexing SOP"
    assert restored.type == "Playbook"
    assert restored.permissions.allowed_roles == ["sre", "engineer"]
    assert "POST /_aliases" in restored.body


def test_global_catalog_manager_toon_index():
    manager = GlobalCatalogManager()
    concept = OKFConcept(
        type="File",
        title="Payments API Guide",
        resource="https://github.com/company/payments",
        tags=["github", "payments"],
        permissions=OKFPermissions(allowed_roles=["engineer"]),
        body="POST /v1/payments/initiate with Idempotency-Key",
        extra_metadata={"source": "github", "resource_type": "file"},
    )
    entry = manager.add_concept(concept)
    assert "Payments" in entry.domain

    toon_manifest = manager.generate_global_index_toon()
    assert "Enterprise Knowledge Global Master Index (TOON v0.2)" in toon_manifest
    assert "S:github" in toon_manifest
    assert "D:Payments" in toon_manifest
    assert "# Payments API Guide" in toon_manifest


def test_serialize_and_deserialize_toon_table():
    items = [
        {
            "chunk_id": "gh_142_0",
            "title": "Fix 3DS timeout in Checkout Flow",
            "source": "github",
            "author": "alice",
            "status": "MERGED",
        },
        {
            "chunk_id": "jira_928_0",
            "title": "PAY-928: 3DS Authentication Timeout",
            "source": "jira",
            "author": "checkout-oncall",
            "status": "CLOSED",
        },
    ]

    table_str = serialize_toon_table(items, name="items")
    assert table_str.startswith("items[2]{chunk_id,title,source,author,status}:")
    assert 'gh_142_0,"Fix 3DS timeout in Checkout Flow",github,alice,MERGED' in table_str
    assert 'jira_928_0,"PAY-928: 3DS Authentication Timeout",jira,checkout-oncall,CLOSED' in table_str

    name, restored_items = deserialize_toon_table(table_str)
    assert name == "items"
    assert len(restored_items) == 2
    assert restored_items[0]["chunk_id"] == "gh_142_0"
    assert restored_items[0]["title"] == "Fix 3DS timeout in Checkout Flow"
    assert restored_items[0]["source"] == "github"
    assert restored_items[0]["author"] == "alice"
    assert restored_items[0]["status"] == "MERGED"
    assert restored_items[1]["chunk_id"] == "jira_928_0"
    assert restored_items[1]["status"] == "CLOSED"

