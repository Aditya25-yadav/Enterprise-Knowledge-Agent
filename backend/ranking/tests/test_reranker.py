"""
Unit Tests for Local Cross-Encoder Reranker (Phase 9 - Part 1).

Tests:
1. CrossEncoderReranker initialization and parameter configuration.
2. Chunk formatting (SmartChunk & Dict) preserving structural context.
3. Score re-ordering promoting high-relevance chunks.
4. Score thresholding filtering irrelevant noise.
5. Top-k candidate truncation.
6. Edge case handling (empty query, empty candidates, single candidate).
7. `rerank_dicts` dictionary serialization and rank preservation.
8. Neural CrossEncoder mock with Sigmoid calibration.
9. Multi-modal enterprise chunk reranking (Jira, GitHub, Notion, Slack).
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.ingestion.chunk import SequenceInfo, SmartChunk
from backend.ranking.models import RerankRequest, RerankResult
from backend.ranking.reranker import CrossEncoderReranker


class TestCrossEncoderReranker(unittest.TestCase):

    def setUp(self) -> None:
        self.reranker = CrossEncoderReranker()

    def test_01_reranker_initialization(self) -> None:
        """Verify default configuration and properties."""
        self.assertEqual(self.reranker.model_name, "cross-encoder/ms-marco-MiniLM-L-6-v2")
        self.assertIsNone(self.reranker.device)
        self.assertIsNone(self.reranker.default_top_k)
        self.assertEqual(self.reranker.default_threshold, 0.0)

    def test_02_format_chunk_for_reranking(self) -> None:
        """Verify chunk text formatting preserves title, breadcrumbs, sequence, and source."""
        # Test with SmartChunk
        chunk = SmartChunk(
            chunk_id="chk_auth_01",
            resource_id="github:repo:auth_service",
            source="github",
            resource_type="file",
            text="Use OAuth2 Bearer tokens in the Authorization header.",
            title="Authentication Architecture",
            section_heading="Bearer Tokens",
            section_path=["Security", "Authentication", "Bearer Tokens"],
            sequence=SequenceInfo(step=2, total_steps=4, sequence_id="seq_auth_setup"),
        )
        chunk_id, formatted_text, title, source, metadata = self.reranker.format_chunk_for_reranking(chunk)

        self.assertEqual(chunk_id, "chk_auth_01")
        self.assertEqual(title, "Authentication Architecture")
        self.assertEqual(source, "github")
        self.assertIn("Title: Authentication Architecture", formatted_text)
        self.assertIn("Section: Security > Authentication > Bearer Tokens", formatted_text)
        self.assertIn("Sequence: Step 2/4", formatted_text)
        self.assertIn("Source: GITHUB", formatted_text)
        self.assertIn("Content: Use OAuth2 Bearer tokens", formatted_text)

        # Test with Dict
        chunk_dict = {
            "chunk_id": "chk_jira_928",
            "title": "PAY-928: Checkout timeout bug",
            "source": "jira",
            "text": "Checkout service fails with timeout under 3DS verification.",
            "section_path": ["Payments", "Bugs"],
            "sequence": {"step": 1, "total_steps": 2},
        }
        chunk_id_d, formatted_d, title_d, source_d, _ = self.reranker.format_chunk_for_reranking(chunk_dict)
        self.assertEqual(chunk_id_d, "chk_jira_928")
        self.assertEqual(title_d, "PAY-928: Checkout timeout bug")
        self.assertEqual(source_d, "jira")
        self.assertIn("Title: PAY-928: Checkout timeout bug", formatted_d)
        self.assertIn("Section: Payments > Bugs", formatted_d)
        self.assertIn("Sequence: Step 1/2", formatted_d)

    def test_03_score_reordering(self) -> None:
        """Verify that a highly relevant candidate is promoted to Rank 1 over irrelevant candidates."""
        query = "How to configure KMS encryption keys for Vault in production?"

        candidates = [
            {
                "chunk_id": "c_noisy_lunch",
                "title": "Team Lunch Menu",
                "text": "Friday pizza lunch menu and dietary preferences for the office.",
                "source": "notion",
            },
            {
                "chunk_id": "c_noisy_git",
                "title": "Git Branching Strategy",
                "text": "Standard git branching guidelines for frontend repositories.",
                "source": "github",
            },
            {
                "chunk_id": "c_relevant_kms",
                "title": "Vault KMS Key Configuration",
                "text": "Configure KMS encryption keys for production HashiCorp Vault instances using AWS KMS.",
                "source": "notion",
            },
        ]

        results = self.reranker.rerank(query=query, chunks=candidates)

        self.assertEqual(len(results), 3)
        # Verify the most relevant KMS candidate was re-ordered to rank 1
        self.assertEqual(results[0].chunk_id, "c_relevant_kms")
        self.assertEqual(results[0].new_rank, 1)
        self.assertEqual(results[0].original_rank, 3)
        self.assertGreater(results[0].score, results[1].score)
        self.assertGreater(results[0].score, results[2].score)

    def test_04_score_threshold_filtering(self) -> None:
        """Verify that candidates scoring below the threshold are filtered out."""
        query = "Kubernetes deployment rollout"

        candidates = [
            {
                "chunk_id": "c_k8s",
                "title": "Kubernetes Rollout Runbook",
                "text": "Execute kubectl rollout status deployment/web-app to monitor cluster deployment.",
                "source": "notion",
            },
            {
                "chunk_id": "c_irrelevant_1",
                "title": "Office Desk Setup",
                "text": "Ergonomic chair and desk allocation request form.",
                "source": "notion",
            },
            {
                "chunk_id": "c_irrelevant_2",
                "title": "Coffee Machine Manual",
                "text": "How to descale the espresso maker on floor 3.",
                "source": "notion",
            },
        ]

        # Use threshold to filter out the irrelevant chunks
        results = self.reranker.rerank(query=query, chunks=candidates, score_threshold=0.25)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk_id, "c_k8s")
        self.assertGreaterEqual(results[0].score, 0.25)

    def test_05_top_k_truncation(self) -> None:
        """Verify top_k parameter truncates candidate list properly."""
        query = "database migration"
        candidates = [
            {"chunk_id": f"c_{i}", "title": f"Migration doc {i}", "text": f"Database schema migration guide {i}"}
            for i in range(10)
        ]

        results = self.reranker.rerank(query=query, chunks=candidates, top_k=3)
        self.assertEqual(len(results), 3)
        self.assertEqual([r.new_rank for r in results], [1, 2, 3])

    def test_06_empty_and_edge_cases(self) -> None:
        """Verify safe handling of empty queries, empty candidates, and single candidate."""
        # Empty candidates
        self.assertEqual(self.reranker.rerank(query="test", chunks=[]), [])

        # Empty query
        self.assertEqual(self.reranker.rerank(query="   ", chunks=[{"chunk_id": "1", "text": "hello"}]), [])

        # Single candidate
        single = [{"chunk_id": "c_single", "title": "Single doc", "text": "Single text"}]
        res = self.reranker.rerank(query="Single doc", chunks=single)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].new_rank, 1)
        self.assertEqual(res[0].original_rank, 1)

    def test_07_rerank_dicts_preserves_metadata(self) -> None:
        """Verify rerank_dicts returns enriched dictionary items with rank and score fields."""
        query = "PAY-928 checkout fix"
        candidates = [
            {
                "chunk_id": "c_pr_142",
                "title": "PR #142: Fix PAY-928 checkout timeout",
                "text": "Fixes checkout 3DS timeout bug reported in PAY-928.",
                "source": "github",
                "author": "alice",
                "permissions": {"is_public": True},
            }
        ]

        dict_results = self.reranker.rerank_dicts(query=query, chunk_dicts=candidates)

        self.assertEqual(len(dict_results), 1)
        res = dict_results[0]
        self.assertEqual(res["chunk_id"], "c_pr_142")
        self.assertEqual(res["author"], "alice")
        self.assertEqual(res["permissions"], {"is_public": True})
        self.assertIn("rerank_score", res)
        self.assertEqual(res["rerank_rank"], 1)
        self.assertEqual(res["original_rank"], 1)

    def test_08_mock_cross_encoder_execution_and_sigmoid(self) -> None:
        """Verify neural cross-encoder prediction with sigmoid normalization."""
        mock_model = MagicMock()
        # Mock raw logits: [4.5 (high), -2.0 (low), 0.0 (medium)]
        mock_model.predict.return_value = [4.5, -2.0, 0.0]

        reranker = CrossEncoderReranker()
        reranker._model = mock_model

        pairs = [
            ("query", "Doc High Relevance"),
            ("query", "Doc Low Relevance"),
            ("query", "Doc Neutral Relevance"),
        ]

        scores = reranker.score_pairs(pairs)

        self.assertEqual(len(scores), 3)
        # Sigmoid(4.5) ~ 0.989, Sigmoid(-2.0) ~ 0.119, Sigmoid(0.0) = 0.5
        self.assertAlmostEqual(scores[0], 0.9890, places=3)
        self.assertAlmostEqual(scores[1], 0.1192, places=3)
        self.assertAlmostEqual(scores[2], 0.5000, places=3)

    def test_09_multimodal_enterprise_chunks(self) -> None:
        """Verify cross-encoder reranking over realistic multi-modal enterprise chunks."""
        query = "Who reviewed the checkout bug fix for PAY-928 and what file was modified?"

        candidates = [
            {
                "chunk_id": "notion_general",
                "title": "Company Travel Policy",
                "text": "Guidelines on corporate travel expenses and reimbursement rules.",
                "source": "notion",
            },
            {
                "chunk_id": "jira_pay_928",
                "title": "PAY-928: Checkout 3DS Timeout",
                "text": "Bug report: checkout timeout occurs during 3DS authentication flow.",
                "source": "jira",
            },
            {
                "chunk_id": "gh_pr_142",
                "title": "PR #142: Fix PAY-928 3DS Timeout",
                "text": "PR #142 authored by alice, reviewed and approved by bob. Modified backend/services/checkout.py to increase timeout.",
                "source": "github",
            },
        ]

        results = self.reranker.rerank(query=query, chunks=candidates)

        # The GitHub PR directly answers both who reviewed and what file was modified
        self.assertEqual(results[0].chunk_id, "gh_pr_142")
        self.assertEqual(results[0].new_rank, 1)
        self.assertGreater(results[0].score, results[1].score)
        self.assertGreater(results[1].score, results[2].score)


if __name__ == "__main__":
    unittest.main()
