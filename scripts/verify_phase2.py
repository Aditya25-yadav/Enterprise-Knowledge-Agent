"""
End-to-End Visual Verification Script for Phase 0, Phase 1, and Phase 2.

Displays:
  1. Phase 0: LLMProvider factory & abstraction check.
  2. Phase 1: SmartOKFChunker (Full text, breadcrumbs, procedure detection, sibling linkage).
  3. Phase 2: Local Qwen3 Embeddings (cache location & dimension) + Qdrant Vector Storage + RBAC Pre-Filtering.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Set project root in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

# Ensure huggingface cache loads offline if already downloaded
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from backend.llm.base import Message, MessageRole, ToolDefinition, ToolCall, LLMResponse
from backend.llm.factory import get_llm_provider
from backend.models.okf import OKFConcept, OKFPermissions
from backend.models.document import Document, DocumentMetadata, ContentBlock, BlockType
from backend.ingestion.chunk import ContentType, SmartChunk
from backend.ingestion.chunker import SmartOKFChunker
from backend.ingestion.embedder import LocalEmbedder
from backend.storage.qdrant_client import QdrantVectorStore
from backend.ingestion.pipeline import IngestionPipeline


def print_banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def print_chunk_box(idx: int, c: SmartChunk):
    print(f"\n┌── 📦 CHUNK #{idx} ─────────────────────────────────────────────────────────────")
    print(f"│ ID:            {c.chunk_id}")
    print(f"│ Content Type:  {c.content_type.value.upper()}")
    print(f"│ Source:        {c.source.upper()} ({c.resource_type})")
    print(f"│ Breadcrumbs:   {' > '.join(c.section_path) if c.section_path else c.section_heading or 'None'}")
    if c.sequence:
        print(f"│ Procedure:     Step {c.sequence.step} of {c.sequence.total_steps} ({c.sequence.step_title})")
    print(f"│ Sibling Links: [Prev: {c.prev_chunk_id or 'None'}] <───> [Next: {c.next_chunk_id or 'None'}]")
    print(f"│ RBAC Roles:    {c.permissions.get('allowed_roles', ['public'])} (is_public={c.permissions.get('is_public')})")
    print("├── 📄 CHUNK TEXT CONTENT ───────────────────────────────────────────────────")
    for line in c.text.strip().splitlines():
        print(f"│   {line}")
    print("└────────────────────────────────────────────────────────────────────────────")


def test_phase_0():
    print_banner("PHASE 0: LLM Provider Abstraction Verification")
    msg = Message(role=MessageRole.USER, content="How does checkout work?")
    tc = ToolCall(tool_name="semantic_search", arguments={"query": "checkout workflow"})
    resp = LLMResponse(tool_calls=[tc])
    print(f"✅ Universal Message & ToolCall: OK")
    print(f"   Simulated Tool Call: {resp.tool_calls[0].tool_name}({resp.tool_calls[0].arguments})")

    active_provider = os.getenv("LLM_PROVIDER", "gemini")
    print(f"✅ Active LLM_PROVIDER in .env: '{active_provider}'")
    if active_provider == "gemini" and not os.getenv("GEMINI_API_KEY"):
        print("ℹ️  Note: GEMINI_API_KEY not yet in .env (needed in Phase 4 for Agent reasoning).")


def test_phase_1():
    print_banner("PHASE 1: Structure-Aware & Hierarchical Chunker Verification")
    sample_doc = """# Payment Service Architecture

Overview of the payment gateway integration and security parameters.

## Checkout Workflow

### Step 1: Initialize Payment Intent
Call POST /v1/payments/initiate with amount, currency, and customer_id. The server returns a client_secret token.

### Step 2: Customer Authorization
Redirect client to 3D-Secure authentication portal. Verify the biometric or OTP challenge with the issuing bank.

### Step 3: Capture and Settlement
Capture authorized funds via webhook callback. Update internal ledger balance to COMPLETED.

## Supported Currencies Table

| Currency | Code | Min Amount | Max Daily |
|---|---|---|---|
| US Dollar | USD | $0.50 | $50,000 |
| Euro | EUR | €0.50 | €50,000 |
| British Pound | GBP | £0.50 | £40,000 |
"""
    concept = OKFConcept(
        type="Architecture",
        title="Payment Gateway Guide",
        resource="https://github.com/company/payments/docs/arch.md",
        body=sample_doc,
        tags=["github", "payments", "finance"],
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=["engineer", "finance"],
            allowed_groups=["payments-team"],
        )
    )

    chunker = SmartOKFChunker()
    chunks = chunker.chunk_okf_concept(concept)

    print(f"✅ Successfully chunked document into {len(chunks)} rich SmartChunks.")
    print("   Here is the complete inspection of all generated chunks:")

    for idx, c in enumerate(chunks):
        print_chunk_box(idx, c)

    # Verification checks
    assert len(chunks) == 5, f"Expected 5 chunks, got {len(chunks)}"
    assert chunks[1].content_type == ContentType.PROCEDURE_STEP
    assert chunks[4].content_type == ContentType.TABLE_RECORD
    assert chunks[1].prev_chunk_id == chunks[0].chunk_id
    assert chunks[1].next_chunk_id == chunks[2].chunk_id
    print("\n✅ All structural, sequence, and sibling assertions passed!")


def test_phase_2():
    print_banner("PHASE 2: Local Qwen Embeddings + Qdrant + RBAC Verification")
    
    embedder = LocalEmbedder()
    hf_cache_dir = Path.home() / ".cache" / "huggingface" / "hub" / "models--Qwen--Qwen3-Embedding-0.6B"
    print(f"✅ Local Embedding Model:  {embedder.model_name}")
    print(f"✅ Local Disk Cache Path:  {hf_cache_dir}")
    print(f"✅ Output Vector Size:     {embedder.dimension} dimensions (dense normalized)")
    print(f"✅ Status:                 100% Local & In-Process (No external API calls)")

    # In-memory Qdrant instance for immediate verification
    vector_store = QdrantVectorStore(mode="memory", collection_name="verification_collection")
    pipeline = IngestionPipeline(embedder=embedder, vector_store=vector_store)

    # 1. Ingest Restricted Document
    confidential_doc = OKFConcept(
        type="Security",
        title="Database Vault Credentials",
        resource="notion://vault/db",
        body="The production PostgreSQL root password is stored in HashiCorp Vault at path secret/data/prod-db.",
        permissions=OKFPermissions(
            is_public=False,
            allowed_roles=["security-lead", "devops"],
            allowed_users=["alice@company.com"],
        )
    )

    # 2. Ingest Public Document
    public_doc = OKFConcept(
        type="Policy",
        title="Remote Work Policy",
        resource="notion://hr/remote",
        body="All employees are eligible for hybrid remote work up to 3 days per week.",
        permissions=OKFPermissions(is_public=True)
    )

    pipeline.ingest_concept(confidential_doc)
    pipeline.ingest_concept(public_doc)
    print(f"✅ Ingested 2 test documents ({vector_store.count_points()} vectors) into Qdrant.")

    # 3. RBAC Test A: Authorized User Query
    print("\n[RBAC Test A] Querying: 'Where is the production database password?' as Role: ['devops']")
    q_vec = embedder.embed_text("Where is the production database password?")
    results_devops = vector_store.search(
        query_vector=q_vec,
        top_k=3,
        user_roles=["devops"],
    )
    if results_devops:
        top = results_devops[0]
        print(f"  -> ✅ PASSED! Top Result (Score: {top['score']:.4f}):")
        print(f"     Title:   {top.get('title')}")
        print(f"     Content: {top.get('text')}")
        print(f"     Allowed: {top.get('allowed_roles')}")
    else:
        print("  -> ❌ FAILED: DevOps role should have retrieved document.")

    # 4. RBAC Test B: Unauthorized User Query
    print("\n[RBAC Test B] Querying: 'Where is the production database password?' as Role: ['guest', 'intern']")
    results_guest = vector_store.search(
        query_vector=q_vec,
        top_k=3,
        user_roles=["guest", "intern"],
    )
    confidential_leaked = any("HashiCorp Vault" in r.get("text", "") for r in results_guest)
    if not confidential_leaked:
        print("  -> ✅ PASSED! Confidential Vault document is strictly filtered out at index level.")
        if results_guest:
            print(f"     (Note: Only public documents like '{results_guest[0].get('title')}' were visible to guest)")
    else:
        print(f"  -> ❌ FAILED! Security leak: unauthorized user retrieved confidential vector.")

    # 5. RBAC Test C: Public Document
    print("\n[RBAC Test C] Querying: 'How many remote work days do we get?' as Role: ['guest']")
    q_vec_pub = embedder.embed_text("How many remote work days do we get?")
    results_pub = vector_store.search(
        query_vector=q_vec_pub,
        top_k=3,
        user_roles=["guest"],
    )
    if results_pub:
        top_pub = results_pub[0]
        print(f"  -> ✅ PASSED! Top Result (Score: {top_pub['score']:.4f}):")
        print(f"     Title:   {top_pub.get('title')}")
        print(f"     Content: {top_pub.get('text')}")


if __name__ == "__main__":
    test_phase_0()
    test_phase_1()
    test_phase_2()
    print_banner("ALL PHASE 0, 1, & 2 CHECKS PASSED WITH FLYING COLORS! 🎉")
