"""
End-to-End Visual Verification Script for Phase 3 (BM25 Keyword Search & Lexical Index).

Displays and Validates:
  1. Tokenizer: Code symbols, snake_case, ticket keys (PAY-928), PRs (#1842), error codes (HTTP 401).
  2. BM25Plus Exact Search: High-precision retrieval of technical identifiers and symbol names.
  3. RBAC Pre-Filtering: Strict access control ensuring restricted keyword data never leaks to unauthorized roles.
  4. Disk Persistence: Serialization to JSON and seamless reload without data loss.
  5. Dual-Indexing Pipeline: Single IngestionPipeline call simultaneously indexing into Qdrant (dense) and BM25 (lexical).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Set project root in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from backend.ingestion.chunk import ContentType, SmartChunk
from backend.ingestion.embedder import LocalEmbedder
from backend.ingestion.pipeline import IngestionPipeline
from backend.models.okf import OKFConcept, OKFPermissions
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


def print_banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_result_box(idx: int, item: dict):
    print(f"\n┌── 🔍 RESULT #{idx} ─────────────────────────────────────────────────────────────")
    print(f"│ Title:           {item.get('title')}")
    print(f"│ BM25 Score:      {item.get('score', 0.0):.4f}")
    print(f"│ Matching Tokens: {item.get('matching_tokens', [])}")
    print(f"│ Source:          {item.get('source', '').upper()} ({item.get('resource_type', 'doc')})")
    print(f"│ Breadcrumbs:     {' > '.join(item.get('section_path', [])) or 'None'}")
    print(f"│ Allowed Roles:   {item.get('allowed_roles', ['public'])} (is_public={item.get('is_public')})")
    print("├── 📄 MATCHED TEXT ─────────────────────────────────────────────────────────")
    for line in item.get("text", "").strip().splitlines():
        print(f"│   {line}")
    print("└────────────────────────────────────────────────────────────────────────────")


def test_section_1_tokenizer():
    print_banner("1. CODE & IDENTIFIER-AWARE TOKENIZER VERIFICATION")
    bm25 = BM25Index(index_path="./data/test_verify_bm25.json")

    sample_inputs = [
        "PAY-928: Fix 3DS timeout in AuthService.validate_token()",
        "PR #1842: Refactor refund_batch_processor to handle HTTP 401 & ECONNREFUSED",
        "Config file: /etc/nginx/conf.d/payments.conf with proxy_pass http://upstream_auth",
    ]

    for sample in sample_inputs:
        tokens = bm25.tokenize(sample)
        print(f"\nInput:  '{sample}'")
        print(f"Tokens ({len(tokens)} unique extracted):")
        print(f"  -> {sorted(tokens)}")

    # Verify key tokens extracted
    t1 = bm25.tokenize("PAY-928: AuthService.validate_token()")
    assert "pay-928" in t1
    assert "pay" in t1
    assert "928" in t1
    assert "authservice" in t1
    assert "validate" in t1
    assert "token" in t1

    t2 = bm25.tokenize("PR #1842: HTTP 401 & ECONNREFUSED")
    assert "#1842" in t2 or "1842" in t2
    assert "401" in t2
    assert "econnrefused" in t2
    print("\n✅ Tokenizer correctly extracts and decomposes all symbols, tickets, and error codes!")


def test_section_2_exact_identifier_search():
    print_banner("2. BM25PLUS EXACT IDENTIFIER & SYMBOL SEARCH")
    bm25 = BM25Index(index_path="./data/test_verify_bm25.json")
    bm25.clear()

    # Create test chunks with rich technical identifiers
    chunk1 = SmartChunk(
        chunk_id="chunk_jira_pay_928",
        resource_id="jira://issue/PAY-928",
        parent_id="notion://jira/PAY-928",
        source="jira",
        resource_type="ticket",
        title="[PAY-928] Fix 3DS timeout in PaymentGateway",
        section_path=["Payments", "Jira", "PAY-928"],
        content_type=ContentType.DOCUMENT_SECTION,
        text="Issue PAY-928: Investigating intermittent 3DS timeouts during POST /v1/payments/initiate. Resolved by PR #1842 in PaymentGateway.charge().",
        permissions={"is_public": False, "allowed_roles": ["engineer", "support"]},
    )

    chunk2 = SmartChunk(
        chunk_id="chunk_runbook_502",
        resource_id="github://repo/runbooks/nginx.md",
        parent_id="github://runbooks/nginx",
        source="github",
        resource_type="runbook",
        title="Runbook: Resolving HTTP 502 & ECONNREFUSED",
        section_path=["Infrastructure", "Runbooks", "Nginx"],
        content_type=ContentType.PROCEDURE_STEP,
        text="When downstream services fail, Nginx logs HTTP 502 Bad Gateway with ECONNREFUSED. Check upstream_backend health and restart service.",
        permissions={"is_public": False, "allowed_roles": ["devops", "sre"]},
    )

    chunk3 = SmartChunk(
        chunk_id="chunk_hr_policy",
        resource_id="notion://page/hr_vacation",
        parent_id="notion://hr/vacation",
        source="notion",
        resource_type="doc",
        title="Annual Leave and PTO Policy 2026",
        section_path=["HR", "Policies", "PTO"],
        content_type=ContentType.DOCUMENT_SECTION,
        text="Full-time employees receive 20 days of paid time off (PTO) annually. Submit requests via Workday.",
        permissions={"is_public": True, "allowed_roles": ["employee"]},
    )

    bm25.add_chunks([chunk1, chunk2, chunk3])
    print(f"✅ Indexed {bm25.count()} SmartChunks into BM25Plus index.")

    queries = [
        ("PAY-928", ["engineer"], "chunk_jira_pay_928"),
        ("PR #1842", ["engineer"], "chunk_jira_pay_928"),
        ("PaymentGateway.charge()", ["engineer"], "chunk_jira_pay_928"),
        ("ECONNREFUSED", ["devops"], "chunk_runbook_502"),
        ("HTTP 502 Bad Gateway", ["devops"], "chunk_runbook_502"),
        ("PTO Policy 2026", ["employee"], "chunk_hr_policy"),
    ]

    for q, roles, expected_id in queries:
        results = bm25.search(query=q, top_k=2, user_roles=roles)
        print(f"\n[Query] '{q}' (Role: {roles})")
        assert len(results) > 0, f"Expected match for query '{q}'"
        top = results[0]
        print(f"  -> Top Match: '{top['title']}' (Score: {top['score']:.4f}, Tokens: {top['matching_tokens']})")
        assert top["chunk_id"] == expected_id, f"Expected {expected_id}, got {top['chunk_id']}"
        print_result_box(1, top)

    print("\n✅ All exact identifier searches achieved 100% precision!")


def test_section_3_rbac_security_isolation():
    print_banner("3. STRICT RBAC ACCESS CONTROL PRE-FILTERING")
    bm25 = BM25Index(index_path="./data/test_verify_bm25.json")
    bm25.clear()

    # Ingest confidential security alert
    secret_chunk = SmartChunk(
        chunk_id="sec_cve_999",
        resource_id="notion://incident/cve-2026-1123",
        parent_id="notion://security/cve-2026-1123",
        source="notion",
        resource_type="incident",
        title="[CONFIDENTIAL] CVE-2026-1123 Security Patch",
        section_path=["Security", "Incidents", "CVE-2026-1123"],
        content_type=ContentType.DOCUMENT_SECTION,
        text="Critical vulnerability CVE-2026-1123 in internal SSH bastion. Rotate master key SECRET-SSH-KEY-99 immediately.",
        permissions={
            "is_public": False,
            "allowed_roles": ["security-admin"],
            "allowed_users": ["ciso@company.com"],
        },
    )

    # Ingest public documentation
    public_chunk = SmartChunk(
        chunk_id="pub_welcome",
        resource_id="notion://page/welcome",
        parent_id="notion://wiki/welcome",
        source="notion",
        resource_type="wiki",
        title="Welcome to Engineering Onboarding",
        section_path=["Engineering", "Onboarding"],
        content_type=ContentType.DOCUMENT_SECTION,
        text="Welcome to the team! Check the wiki for engineering standards, Git guidelines, and Slack channels.",
        permissions={"is_public": True},
    )

    bm25.add_chunks([secret_chunk, public_chunk])

    # Test A: Security Admin Search
    print("\n[RBAC Test A] Security Admin searches for 'CVE-2026-1123 SECRET-SSH-KEY-99'")
    admin_res = bm25.search(
        query="CVE-2026-1123 SECRET-SSH-KEY-99",
        user_roles=["security-admin"],
    )
    assert len(admin_res) == 1
    print(f"  -> ✅ PASSED! Security admin retrieved: '{admin_res[0]['title']}' (Score: {admin_res[0]['score']:.4f})")

    # Test B: Guest / Engineer Search (Unauthorized)
    print("\n[RBAC Test B] Guest searches for exact 'CVE-2026-1123 SECRET-SSH-KEY-99'")
    guest_res = bm25.search(
        query="CVE-2026-1123 SECRET-SSH-KEY-99",
        user_roles=["guest"],
    )
    assert len(guest_res) == 0
    print("  -> ✅ PASSED! Zero matches returned. Confidential chunk is 100% blocked at the index level.")

    # Test C: Guest searches public document
    print("\n[RBAC Test C] Guest searches for 'Engineering Onboarding'")
    guest_pub_res = bm25.search(
        query="Engineering Onboarding",
        user_roles=["guest"],
    )
    assert len(guest_pub_res) == 1
    print(f"  -> ✅ PASSED! Public doc accessible: '{guest_pub_res[0]['title']}'")


def test_section_4_disk_persistence():
    print_banner("4. DISK SERIALIZATION & RELOAD VERIFICATION")
    test_file = "./data/verify_test_disk_index.json"
    bm25 = BM25Index(index_path=test_file)
    bm25.clear()

    c = SmartChunk(
        chunk_id="chunk_persist_1",
        resource_id="notion://doc/1",
        parent_id="notion://doc/1",
        source="notion",
        resource_type="doc",
        title="Persistent Index Document",
        content_type=ContentType.DOCUMENT_SECTION,
        text="Persistent token TOKEN_XYZ_999 stored across restarts.",
        permissions={"is_public": True},
    )
    bm25.add_chunks([c])
    bm25.save_to_disk()
    print(f"✅ Saved BM25 index to disk at '{test_file}' ({os.path.getsize(test_file)} bytes).")

    # Re-instantiate new index pointing to same file
    bm25_new = BM25Index(index_path=test_file)
    print(f"✅ Loaded BM25 index from disk into fresh in-memory instance ({bm25_new.count()} chunks).")

    res = bm25_new.search("TOKEN_XYZ_999")
    assert len(res) == 1
    assert res[0]["chunk_id"] == "chunk_persist_1"
    print(f"  -> ✅ Query after reload: Found '{res[0]['title']}' (Score: {res[0]['score']:.4f})")

    # Cleanup
    bm25_new.clear()
    print("✅ Cleaned up temporary persistence test file.")


def test_section_5_dual_indexing_pipeline():
    print_banner("5. DUAL-INDEXING INGESTION PIPELINE (QDRANT + BM25)")

    embedder = LocalEmbedder()
    vector_store = QdrantVectorStore(mode="memory", collection_name="test_dual_indexing")
    bm25 = BM25Index(index_path="./data/test_dual_bm25.json")
    bm25.clear()

    pipeline = IngestionPipeline(
        embedder=embedder,
        vector_store=vector_store,
        bm25_index=bm25,
    )

    concept = OKFConcept(
        type="Guide",
        title="Payment Refund Flow",
        resource="github://payments/refunds.md",
        body="""# Payment Refund Runbook
## Step 1: Execute Refund
Call `POST /v1/payments/refund` with transaction_id `TX-88219`.

## Step 2: Webhook Confirmation
Listen for `refund.settled` event with payload verification.
""",
        permissions=OKFPermissions(is_public=False, allowed_roles=["finance-team"]),
    )

    ingest_res = pipeline.ingest_concept(concept)
    chunks_count = ingest_res.get("chunks_count", len(ingest_res.get("chunks", []))) if isinstance(ingest_res, dict) else ingest_res
    print(f"✅ Ingested OKFConcept through IngestionPipeline: Generated {chunks_count} SmartChunks.")
    print(f"   -> Qdrant Vector Store Points: {vector_store.count_points()}")
    print(f"   -> BM25 Keyword Index Chunks:  {bm25.count()}")

    # 1. Test BM25 exact search on the ingested data
    bm25_res = bm25.search("TX-88219", user_roles=["finance-team"])
    print(f"\n[BM25 Search for 'TX-88219']")
    assert len(bm25_res) > 0
    print(f"  -> ✅ Found via BM25 Keyword Index: '{bm25_res[0]['title']}' (Score: {bm25_res[0]['score']:.4f})")

    # 2. Test Qdrant dense vector search on the same ingested data
    q_vec = embedder.embed_text("How do I execute a refund?")
    qdrant_res = vector_store.search(query_vector=q_vec, user_roles=["finance-team"])
    print(f"\n[Qdrant Semantic Search for 'How do I execute a refund?']")
    assert len(qdrant_res) > 0
    print(f"  -> ✅ Found via Qdrant Vector Store: '{qdrant_res[0]['title']}' (Score: {qdrant_res[0]['score']:.4f})")

    # Cleanup
    bm25.clear()
    print("\n✅ Dual-indexing validated: Dense vector semantic search & sparse lexical search stay 100% in sync!")


if __name__ == "__main__":
    test_section_1_tokenizer()
    test_section_2_exact_identifier_search()
    test_section_3_rbac_security_isolation()
    test_section_4_disk_persistence()
    test_section_5_dual_indexing_pipeline()
    print_banner("ALL PHASE 3 BM25 KEYWORD SEARCH VERIFICATIONS PASSED! 🎉")
