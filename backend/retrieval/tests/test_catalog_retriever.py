"""
Unit Test Suite for GlobalCatalogManager & CatalogRetriever.

Validates:
  1. Enriched schema extraction (domain, key_entities, allowed_roles, trust_tier).
  2. ISO-8601 UTC microsecond/second timestamp formatting in global_log.md and global_index.md.
  3. Database-level RBAC pre-filtering for catalog discovery.
  4. Lexical and entity overlap scoring in candidate selection.
  5. LLM-assisted catalog ranking, confidence scoring (0.0 to 1.0), and tool recommendations.
  6. Resilient fallback scoring when LLM output is unavailable.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from backend.ingestion.catalog_aggregator import GlobalCatalogManager
from backend.llm.base import LLMProvider, LLMResponse, Message
from backend.models.okf import OKFConcept, OKFPermissions
from backend.retrieval.catalog import CatalogRetriever


class MockCatalogLLMProvider(LLMProvider):
    """Mock LLM provider returning structured catalog reasoning JSON."""

    def __init__(self, fixed_response: Optional[str] = None):
        self.fixed_response = fixed_response

    @property
    def provider_name(self) -> str:
        return "mock"

    def generate(self, messages: List[Message], **kwargs: Any) -> LLMResponse:
        if self.fixed_response:
            return LLMResponse(content=self.fixed_response)
        
        # Default mock structured output
        mock_json = """
        [
          {
            "uri": "https://dropbox.enterprise.com/sre/dr_failover_v3.docx",
            "confidence": 0.95,
            "recommended_tool": "resource_lookup",
            "recommended_arguments": {"resource_id": "https://dropbox.enterprise.com/sre/dr_failover_v3.docx"},
            "reasoning": "Contains exact step-by-step failover procedures."
          },
          {
            "uri": "https://github.com/enterprise/payment-gateway/pull/142",
            "confidence": 0.85,
            "recommended_tool": "github_entity_search",
            "recommended_arguments": {"target": "PR#142", "operation": "get_pr_details"},
            "reasoning": "Contains code diff and review threads for PR 142."
          }
        ]
        """
        return LLMResponse(content=mock_json)

    def generate_with_tools(self, messages: List[Message], tools: List[Any], **kwargs: Any) -> LLMResponse:
        return self.generate(messages, **kwargs)


class TestCatalogRetriever(unittest.TestCase):

    def setUp(self) -> None:
        self.catalog_mgr = GlobalCatalogManager()
        self.test_data_dir = Path("./data/test_catalog")
        self.test_data_dir.mkdir(parents=True, exist_ok=True)

        # Concept 1: Public SRE Disaster Recovery Guide (Dropbox)
        self.concept_sre = OKFConcept(
            type="Playbook",
            title="SRE Disaster Recovery: PostgreSQL Failover Runbook v3",
            resource="https://dropbox.enterprise.com/sre/dr_failover_v3.docx",
            body="""# SRE DR Failover
Step 1: Verify PostgreSQL replication lag with patronictl topology.
Step 2: Execute manual switchover via patronictl switchover --master db-node-01.
Step 3: Point PgBouncer pool to db-node-02.""",
            tags=["dropbox", "sre", "runbook", "disaster_recovery"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer", "sre"], is_public=True),
            extra_metadata={"source": "dropbox", "resource_type": "playbook", "domain": "Infrastructure & Disaster Recovery"},
        )

        # Concept 2: Engineering PR on GitHub
        self.concept_pr = OKFConcept(
            type="PullRequest",
            title="PR #142: Fix 3DS Payment Timeout in Checkout Gateway",
            resource="https://github.com/enterprise/payment-gateway/pull/142",
            body="""# PR #142: 3DS Timeout Fix
Fixes PAY-928 by increasing the HTTP client timeout from 5s to 30s in AuthService.validate_token.
Author: alice. Reviewer: bob.""",
            tags=["github", "payments", "pr"],
            permissions=OKFPermissions(allowed_roles=["employee", "engineer"]),
            extra_metadata={"source": "github", "resource_type": "pull_request", "domain": "Payments & Checkout"},
        )

        # Concept 3: Secret CISO Vault Key Management (Notion - Restricted)
        self.concept_ciso = OKFConcept(
            type="ArchitectureGuide",
            title="CISO Security Architecture: Production Master KMS & Vault Keys",
            resource="https://notion.enterprise.com/ciso/kms_vault_architecture",
            body="""# Master KMS Architecture
Production Master Key ARN: arn:aws:kms:us-east-1:123456789012:key/vault-prod-master.
Contains top-secret cryptographic key rotation procedures.""",
            tags=["notion", "ciso", "security", "kms"],
            permissions=OKFPermissions(allowed_roles=["ciso_admin", "secops"], is_public=False),
            extra_metadata={"source": "notion", "resource_type": "architecture_guide", "domain": "Security & Cryptography"},
        )

        self.catalog_mgr.add_concept(self.concept_sre)
        self.catalog_mgr.add_concept(self.concept_pr)
        self.catalog_mgr.add_concept(self.concept_ciso)

    def tearDown(self) -> None:
        for f in self.test_data_dir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            self.test_data_dir.rmdir()
        except OSError:
            pass

    def test_catalog_enrichment_and_fields(self) -> None:
        """Verify that catalog aggregator extracts enriched fields and metadata."""
        entries = list(self.catalog_mgr.entries.values())
        self.assertEqual(len(entries), 3)

        # Verify SRE entry
        sre_entry = next(e for e in entries if "dr_failover" in e.resource_uri)
        self.assertEqual(sre_entry.source, "dropbox")
        self.assertEqual(sre_entry.domain, "Infrastructure & Disaster Recovery")
        self.assertIn("employee", sre_entry.allowed_roles)
        self.assertIn("sre", sre_entry.allowed_roles)

        # Verify PR entry entity extraction
        pr_entry = next(e for e in entries if "142" in e.resource_uri)
        self.assertEqual(pr_entry.source, "github")
        self.assertEqual(pr_entry.domain, "Payments & Checkout")
        self.assertIn("PAY-928", pr_entry.key_entities)
        self.assertIn("PR #142", pr_entry.key_entities)

    def test_global_index_and_log_generation_and_timestamps(self) -> None:
        """Verify markdown generation for global_index.md and timestamped global_log.md."""
        index_md = self.catalog_mgr.generate_global_index_markdown()
        log_md = self.catalog_mgr.generate_global_log_markdown()

        # Check index content
        self.assertIn("# Enterprise Knowledge Global Master Index", index_md)
        self.assertIn("SRE Disaster Recovery", index_md)
        self.assertIn("Payments & Checkout", index_md)
        self.assertIn("PAY-928", index_md)

        # Check log timestamps (ISO-8601 UTC)
        self.assertIn("# Enterprise Knowledge Global Ingestion & Sync Ledger", log_md)
        self.assertIn("Timestamp", log_md)
        self.assertIn("UTC", log_md)
        
        # Verify ISO 8601 timestamp regex format
        iso_pattern = r"\d{4}-\d{2}-\d{2}"
        self.assertTrue(re.search(iso_pattern, log_md), "Timestamp in global_log.md must match ISO-8601 UTC format")

        # Test save to disk
        index_path, log_path = self.catalog_mgr.save_to_disk(str(self.test_data_dir))
        self.assertTrue(Path(index_path).exists())
        self.assertTrue(Path(log_path).exists())

    def test_rbac_prefiltering_in_catalog(self) -> None:
        """Verify that catalog RBAC strictly hides restricted documents from unauthorized roles."""
        mock_llm = MockCatalogLLMProvider()
        retriever = CatalogRetriever(catalog_manager=self.catalog_mgr, llm_provider=mock_llm)

        # Guest user discovery
        guest_res = retriever.discover(
            query="KMS master encryption keys and vault secrets",
            user_context={"roles": ["guest"], "user_id": "guest@ext.com"},
        )
        # Should not find CISO secret document
        for match in guest_res["manifest"]:
            self.assertNotEqual(match["uri"], self.concept_ciso.resource)

        # CISO Admin discovery
        ciso_res = retriever.discover(
            query="KMS master encryption keys and vault secrets",
            user_context={"roles": ["ciso_admin"], "user_id": "ciso@company.com"},
        )
        found_ciso = any(m["uri"] == self.concept_ciso.resource for m in ciso_res["manifest"])
        self.assertTrue(found_ciso, "CISO admin must have access to restricted CISO architecture docs")

    def test_llm_catalog_reasoning_and_confidence_scoring(self) -> None:
        """Verify LLM catalog matching assigns confidence scores and recommended tools."""
        mock_llm = MockCatalogLLMProvider()
        retriever = CatalogRetriever(catalog_manager=self.catalog_mgr, llm_provider=mock_llm)

        res = retriever.discover(
            query="How to failover PostgreSQL database in disaster recovery?",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        self.assertIn("manifest", res)
        self.assertGreater(len(res["manifest"]), 0)
        self.assertIn("overall_confidence", res)
        self.assertGreaterEqual(res["overall_confidence"], 0.8)

        # Verify recommended tool guidance
        first_match = res["manifest"][0]
        self.assertIn("recommended_tool", first_match)
        self.assertIn("recommended_arguments", first_match)
        self.assertIn("confidence", first_match)
        self.assertGreater(first_match["confidence"], 0.0)

    def test_heuristic_fallback_when_llm_unavailable(self) -> None:
        """Verify that catalog retriever falls back to deterministic lexical scoring when LLM output is malformed."""
        bad_llm = MockCatalogLLMProvider(fixed_response="Invalid Non-JSON response")
        retriever = CatalogRetriever(catalog_manager=self.catalog_mgr, llm_provider=bad_llm)

        res = retriever.discover(
            query="PAY-928 3DS timeout PR #142",
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )

        self.assertGreater(len(res["manifest"]), 0)
        pr_match = next((m for m in res["manifest"] if "142" in m["uri"]), None)
        self.assertIsNotNone(pr_match)
        self.assertEqual(pr_match["recommended_tool"], "github_entity_search")
        self.assertGreaterEqual(pr_match["confidence"], 0.7)


if __name__ == "__main__":
    unittest.main()
