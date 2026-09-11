"""
End-to-End Test Suite for Structure-Aware Hierarchical & Semantic Chunker.

Validates:
  1. Breadcrumb hierarchy tracking (section_path stack).
  2. Multi-step procedure step extraction (SequenceInfo & step numbers).
  3. Bidirectional sibling chunk linkage (prev_chunk_id & next_chunk_id).
  4. Specialized strategy routing (Code, Conversation, Tables, Procedures).
  5. RBAC metadata & Qdrant payload structure.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

import unittest
from backend.ingestion.chunk import ContentType, SmartChunk, SequenceInfo
from backend.ingestion.chunker import SmartOKFChunker
from backend.models.document import Document, DocumentMetadata, ContentBlock, BlockType
from backend.models.okf import OKFConcept, OKFPermissions


class TestSmartOKFChunker(unittest.TestCase):

    def setUp(self) -> None:
        self.chunker = SmartOKFChunker(max_chunk_chars=500, min_chunk_chars=50, overlap_chars=50)

    # ── Test 1: Breadcrumb Hierarchy & Sibling Linkage ───────────────────────

    def test_breadcrumb_hierarchy_and_sibling_links(self) -> None:
        md = """# Enterprise Architecture

Overview of our platform systems.

## Security & Auth

Security policies and user management.

### OAuth 2.0 Integration

We support OAuth 2.0 with authorization code grant.
Tokens are exchanged over TLS.

### Session Management

Session cookies expire in 7 days.
"""
        concept = OKFConcept(
            type="Document",
            title="Arch Guide",
            resource="https://docs.company.com/arch",
            body=md,
            tags=["notion", "architecture"],
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["engineer"],
                allowed_groups=["platform"],
                allowed_users=["alice"]
            )
        )

        chunks = self.chunker.chunk_okf_concept(concept)
        self.assertGreaterEqual(len(chunks), 3)

        # 1. Verify Sibling links (prev_chunk_id / next_chunk_id)
        self.assertIsNone(chunks[0].prev_chunk_id)
        self.assertEqual(chunks[0].next_chunk_id, chunks[1].chunk_id)

        for i in range(1, len(chunks) - 1):
            self.assertEqual(chunks[i].prev_chunk_id, chunks[i - 1].chunk_id)
            self.assertEqual(chunks[i].next_chunk_id, chunks[i + 1].chunk_id)
            self.assertEqual(chunks[i].chunk_index, i)
            self.assertEqual(chunks[i].total_chunks, len(chunks))

        self.assertIsNone(chunks[-1].next_chunk_id)

        # 2. Verify Breadcrumb section_path
        oauth_chunk = next(c for c in chunks if "OAuth 2.0 Integration" in (c.section_heading or ""))
        self.assertIn("Enterprise Architecture", oauth_chunk.section_path)
        self.assertIn("Security & Auth", oauth_chunk.section_path)
        self.assertIn("OAuth 2.0 Integration", oauth_chunk.section_path)

        # 3. Verify Context string formatting
        ctx_str = oauth_chunk.to_context_string()
        self.assertIn("[NOTION] Arch Guide > Enterprise Architecture > Security & Auth > OAuth 2.0 Integration", ctx_str)

    # ── Test 2: Multi-Step Procedure Extraction ──────────────────────────────

    def test_procedure_sequence_extraction(self) -> None:
        md = """# Deployment Runbook

Follow these steps to deploy the service to production:

## Deployment Sequence

### Step 1: Run pre-flight health checks
Verify database connectivity and check active cluster node health.

### Step 2: Apply database migrations
Run alembic upgrade head to ensure the schema matches the release.

### Step 3: Rolling container restart
Restart web pods with 25% max unavailable window.

### Step 4: Smoke test public endpoints
Verify /health and /metrics endpoints return HTTP 200 OK.
"""
        concept = OKFConcept(
            type="Playbook",
            title="Deploy Runbook",
            resource="https://github.com/company/repo/runbook.md",
            body=md,
            tags=["github", "devops"],
        )

        chunks = self.chunker.chunk_okf_concept(concept)
        step_chunks = [c for c in chunks if c.content_type == ContentType.PROCEDURE_STEP]

        self.assertEqual(len(step_chunks), 4)

        for idx, sc in enumerate(step_chunks, 1):
            self.assertIsNotNone(sc.sequence)
            self.assertEqual(sc.sequence.step, idx)
            self.assertEqual(sc.sequence.total_steps, 4)
            self.assertIn(f"Step {idx}", sc.sequence.step_title)
            # Verify Qdrant payload serialization
            payload = sc.to_qdrant_payload()
            self.assertEqual(payload["content_type"], "procedure_step")
            self.assertEqual(payload["sequence"]["step"], idx)
            self.assertEqual(payload["sequence"]["total_steps"], 4)

    # ── Test 3: Markdown Table Preservation ──────────────────────────────────

    def test_markdown_table_classification(self) -> None:
        md = """# API Configuration

| Key | Type | Default | Description |
|---|---|---|---|
| PORT | int | 8000 | Server port |
| DEBUG | bool | false | Verbose logs |
| TIMEOUT | int | 30 | Request timeout |
"""
        concept = OKFConcept(
            type="Document",
            title="Config Specs",
            resource="notion://config",
            body=md,
        )

        chunks = self.chunker.chunk_okf_concept(concept)
        tbl_chunk = next(c for c in chunks if c.content_type == ContentType.TABLE_RECORD)
        self.assertIn("| PORT | int | 8000 |", tbl_chunk.text)
        self.assertEqual(tbl_chunk.to_qdrant_payload()["content_type"], "table_record")

    # ── Test 4: Conversation & Email Thread Chunker ──────────────────────────

    def test_conversation_thread_chunking(self) -> None:
        doc = Document(
            metadata=DocumentMetadata(
                id="msg_999",
                title="Q3 Budget Thread",
                source_platform="gmail",
                url="https://mail.google.com/mail/u/0/#inbox/999",
                extra={
                    "sender": "cfo@company.com",
                    "recipients": ["team@company.com"],
                    "thread_id": "th_1234",
                }
            ),
            blocks=[
                ContentBlock(id="b1", type=BlockType.CALLOUT, text="From: cfo@company.com\nTo: team@company.com"),
                ContentBlock(id="b2", type=BlockType.HEADING, text="Q3 Budget Approval", properties={"level": 2}),
                ContentBlock(id="b3", type=BlockType.PARAGRAPH, text="The hiring and infrastructure budget for Q3 is approved."),
            ]
        )

        chunks = self.chunker.chunk_document(doc)
        self.assertGreater(len(chunks), 0)
        c = chunks[0]
        self.assertEqual(c.source, "gmail")
        self.assertEqual(c.content_type, ContentType.CONVERSATION_THREAD)
        self.assertEqual(c.extra_metadata["sender"], "cfo@company.com")
        self.assertEqual(c.extra_metadata["thread_id"], "th_1234")

    # ── Test 5: Code Symbol Boundary Detection ───────────────────────────────

    def test_code_symbol_chunking(self) -> None:
        code = """import os
import sys

class AuthService:
    def __init__(self, key: str):
        self.key = key

    def validate_token(self, token: str) -> bool:
        return token.startswith("valid_")

def helper_tool():
    return True
"""
        concept = OKFConcept(
            type="Code",
            title="auth_service.py",
            resource="github://company/repo/auth_service.py",
            body=code,
            tags=["github", "python"],
        )

        chunks = self.chunker.chunk_okf_concept(concept)
        self.assertGreaterEqual(len(chunks), 2)
        code_chunks = [c for c in chunks if c.content_type == ContentType.CODE_SYMBOL]
        self.assertTrue(len(code_chunks) >= 2)


if __name__ == "__main__":
    unittest.main()
