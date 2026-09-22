"""
Unit Test Suite for EvidenceEvaluator & Self-RAG Reflection Engine (Phase 7 - Part 1).

Validates:
  1. Empty chunks return immediate RETRIEVE_MORE.
  2. Sufficient evidence returns GENERATE with high relevance.
  3. Insufficient evidence identifies specific knowledge gaps & recommended tools.
  4. Irrelevant evidence recommends REFORMULATE.
  5. JSON markdown code fences are cleanly parsed.
  6. Malformed JSON triggers graceful heuristic fallback.
"""

from __future__ import annotations

import json
import os
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

from backend.evaluation.evaluator import EvidenceEvaluator
from backend.llm.base import LLMProvider, LLMResponse, Message, ToolDefinition
from backend.models.evaluation import EvaluationResult, RecommendedAction


class MockEvaluatorLLM(LLMProvider):
    """Deterministic Mock LLM for testing EvidenceEvaluator responses."""

    def __init__(self, response_json: Optional[Dict[str, Any]] = None, raw_text: Optional[str] = None) -> None:
        self.response_json = response_json
        self.raw_text = raw_text
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/evaluator-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        if self.raw_text is not None:
            return self.raw_text
        if self.response_json is not None:
            return json.dumps(self.response_json)
        return json.dumps({
            "relevance_score": 1.0,
            "evidence_sufficient": True,
            "missing_information": [],
            "unsupported_claims": [],
            "recommended_action": "GENERATE",
            "recommended_tool": None,
            "chunk_evaluations": [],
            "reasoning": "Sufficient evidence.",
        })

    def generate_with_tools(self, messages: List[Message], tools: List[ToolDefinition]) -> Any:
        self.call_history.append(messages)
        content = self.generate(messages)
        return LLMResponse(content=content)


class TestEvidenceEvaluator(unittest.TestCase):

    def setUp(self) -> None:
        self.sample_chunks = [
            {
                "chunk_id": "chunk_payment_service",
                "title": "Payment Service Architecture",
                "source": "github",
                "url": "https://github.com/company/payments",
                "text": "Payment Service is owned and maintained by the Platform Engineering team.",
            }
        ]

    def test_01_empty_chunks_returns_retrieve_more(self) -> None:
        mock_llm = MockEvaluatorLLM()
        evaluator = EvidenceEvaluator(llm_provider=mock_llm)

        result = evaluator.evaluate_evidence(
            query="Who owns Payment Service and what projects do they support?",
            chunks=[],
        )

        self.assertFalse(result.evidence_sufficient)
        self.assertEqual(result.recommended_action, RecommendedAction.RETRIEVE_MORE.value)
        self.assertEqual(result.relevance_score, 0.0)
        self.assertEqual(len(result.missing_information), 1)
        # LLM generate should not even need to be called when chunks is empty
        self.assertEqual(len(mock_llm.call_history), 0)

    def test_02_sufficient_evidence_evaluation(self) -> None:
        mock_llm = MockEvaluatorLLM(
            response_json={
                "relevance_score": 0.98,
                "evidence_sufficient": True,
                "missing_information": [],
                "unsupported_claims": [],
                "recommended_action": "GENERATE",
                "recommended_tool": None,
                "chunk_evaluations": [
                    {
                        "chunk_id": "chunk_payment_service",
                        "score": 0.98,
                        "is_relevant": True,
                        "reason": "Directly states Platform Engineering ownership.",
                    }
                ],
                "reasoning": "The evidence directly and completely answers who owns the Payment Service.",
            }
        )
        evaluator = EvidenceEvaluator(llm_provider=mock_llm)

        result = evaluator.evaluate_evidence(
            query="Who owns Payment Service?",
            chunks=self.sample_chunks,
        )

        self.assertTrue(result.evidence_sufficient)
        self.assertEqual(result.recommended_action, "GENERATE")
        self.assertAlmostEqual(result.relevance_score, 0.98)
        self.assertEqual(len(result.chunk_evaluations), 1)

    def test_03_insufficient_evidence_detects_gap(self) -> None:
        mock_llm = MockEvaluatorLLM(
            response_json={
                "relevance_score": 0.90,
                "evidence_sufficient": False,
                "missing_information": [
                    "projects or repositories supported by Platform Engineering team"
                ],
                "unsupported_claims": [],
                "recommended_action": "RETRIEVE_MORE",
                "recommended_tool": "github_entity_search",
                "chunk_evaluations": [
                    {
                        "chunk_id": "chunk_payment_service",
                        "score": 0.95,
                        "is_relevant": True,
                        "reason": "Establishes team ownership but lacks project list.",
                    }
                ],
                "reasoning": "Ownership is established, but no evidence connects Platform Engineering to other projects.",
            }
        )
        evaluator = EvidenceEvaluator(llm_provider=mock_llm)

        result = evaluator.evaluate_evidence(
            query="Which team owns Payment Service and what other projects does that team support?",
            chunks=self.sample_chunks,
        )

        self.assertFalse(result.evidence_sufficient)
        self.assertEqual(result.recommended_action, "RETRIEVE_MORE")
        self.assertEqual(result.recommended_tool, "github_entity_search")
        self.assertIn("projects or repositories supported by Platform Engineering team", result.missing_information)

    def test_04_irrelevant_evidence_triggers_reformulate(self) -> None:
        mock_llm = MockEvaluatorLLM(
            response_json={
                "relevance_score": 0.15,
                "evidence_sufficient": False,
                "missing_information": ["Information regarding database migrations"],
                "unsupported_claims": [],
                "recommended_action": "REFORMULATE",
                "recommended_tool": "keyword_search",
                "chunk_evaluations": [],
                "reasoning": "Retrieved chunks discuss checkout UI instead of database migrations.",
            }
        )
        evaluator = EvidenceEvaluator(llm_provider=mock_llm)

        result = evaluator.evaluate_evidence(
            query="How to run database migrations for payments?",
            chunks=self.sample_chunks,
        )

        self.assertFalse(result.evidence_sufficient)
        self.assertEqual(result.recommended_action, "REFORMULATE")
        self.assertEqual(result.recommended_tool, "keyword_search")

    def test_05_json_markdown_fences_parsing(self) -> None:
        fenced_json = """```json
{
  "relevance_score": 0.92,
  "evidence_sufficient": true,
  "missing_information": [],
  "unsupported_claims": [],
  "recommended_action": "GENERATE",
  "recommended_tool": null,
  "chunk_evaluations": [],
  "reasoning": "Evidence is clear."
}
```"""
        mock_llm = MockEvaluatorLLM(raw_text=fenced_json)
        evaluator = EvidenceEvaluator(llm_provider=mock_llm)

        result = evaluator.evaluate_evidence(
            query="Who owns Payment Service?",
            chunks=self.sample_chunks,
        )

        self.assertTrue(result.evidence_sufficient)
        self.assertEqual(result.recommended_action, "GENERATE")
        self.assertAlmostEqual(result.relevance_score, 0.92)

    def test_06_heuristic_fallback_on_malformed_json(self) -> None:
        malformed_text = "I believe the evidence is INSUFFICIENT because we need more details on Platform Engineering."
        mock_llm = MockEvaluatorLLM(raw_text=malformed_text)
        evaluator = EvidenceEvaluator(llm_provider=mock_llm)

        result = evaluator.evaluate_evidence(
            query="Who owns Payment Service and what projects do they support?",
            chunks=self.sample_chunks,
        )

        self.assertFalse(result.evidence_sufficient)
        self.assertEqual(result.recommended_action, "RETRIEVE_MORE")
        self.assertIn("Heuristic fallback", result.reasoning)


if __name__ == "__main__":
    unittest.main()
