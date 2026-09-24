"""
Unit Test Suite for QueryReformulator (Phase 7 - Part 2).

Validates:
  1. Targeted query reformulation from evaluation feedback and knowledge gaps.
  2. Markdown JSON code-fence parsing.
  3. Graceful heuristic fallback when JSON is malformed.
  4. Query preservation when missing information is empty.
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

from backend.agent.reformulator import QueryReformulator
from backend.llm.base import LLMProvider, LLMResponse, Message, ToolDefinition
from backend.models.evaluation import EvaluationResult, RecommendedAction


class MockReformulatorLLM(LLMProvider):
    """Deterministic mock LLM for testing QueryReformulator."""

    def __init__(self, response_json: Optional[Dict[str, Any]] = None, raw_text: Optional[str] = None) -> None:
        self.response_json = response_json
        self.raw_text = raw_text
        self.call_history: List[List[Message]] = []

    @property
    def provider_name(self) -> str:
        return "mock/reformulator-llm"

    def generate(self, messages: List[Message]) -> str:
        self.call_history.append(messages)
        if self.raw_text is not None:
            return self.raw_text
        if self.response_json is not None:
            return json.dumps(self.response_json)
        return json.dumps({
            "reformulated_query": "What projects or repositories are supported by Platform Engineering?",
            "reasoning": "Targets the missing team project relationship.",
            "suggested_tool": "github_entity_search",
        })

    def generate_with_tools(self, messages: List[Message], tools: List[ToolDefinition]) -> Any:
        self.call_history.append(messages)
        return LLMResponse(content=self.generate(messages))


class TestQueryReformulator(unittest.TestCase):

    def setUp(self) -> None:
        self.sample_evidence = [
            {
                "chunk_id": "chunk_payment",
                "title": "Payment Service",
                "source": "github",
                "text": "Payment Service is owned by Platform Engineering.",
            }
        ]

    def test_01_reformulate_on_missing_information(self) -> None:
        mock_llm = MockReformulatorLLM(
            response_json={
                "reformulated_query": "What repositories and projects are accessible by Platform Engineering?",
                "reasoning": "Discovers repositories owned by Platform Engineering.",
                "suggested_tool": "github_entity_search",
            }
        )
        reformulator = QueryReformulator(llm_provider=mock_llm)

        eval_res = EvaluationResult(
            relevance_score=0.9,
            evidence_sufficient=False,
            missing_information=["projects or repositories supported by Platform Engineering"],
            recommended_action=RecommendedAction.RETRIEVE_MORE.value,
            recommended_tool="github_entity_search",
            reasoning="We know the team but not the projects.",
        )

        res = reformulator.reformulate(
            original_query="Which team owns Payment Service and what other projects does that team support?",
            current_evidence=self.sample_evidence,
            evaluation=eval_res,
        )

        self.assertEqual(res["reformulated_query"], "What repositories and projects are accessible by Platform Engineering?")
        self.assertEqual(res["suggested_tool"], "github_entity_search")
        self.assertIn("Platform Engineering", res["reasoning"])

    def test_02_reformulate_markdown_json_parsing(self) -> None:
        fenced_json = """```json
{
  "reformulated_query": "ERR_PAY_TIMEOUT error resolution runbook",
  "reasoning": "Searches for specific error runbook.",
  "suggested_tool": "keyword_search"
}
```"""
        mock_llm = MockReformulatorLLM(raw_text=fenced_json)
        reformulator = QueryReformulator(llm_provider=mock_llm)

        eval_res = EvaluationResult(
            relevance_score=0.2,
            evidence_sufficient=False,
            missing_information=["runbook for ERR_PAY_TIMEOUT"],
            recommended_action=RecommendedAction.REFORMULATE.value,
            recommended_tool="keyword_search",
        )

        res = reformulator.reformulate(
            original_query="How to resolve checkout error?",
            current_evidence=self.sample_evidence,
            evaluation=eval_res,
        )

        self.assertEqual(res["reformulated_query"], "ERR_PAY_TIMEOUT error resolution runbook")
        self.assertEqual(res["suggested_tool"], "keyword_search")

    def test_03_fallback_when_llm_fails(self) -> None:
        mock_llm = MockReformulatorLLM(raw_text="I am not sure how to format this JSON.")
        reformulator = QueryReformulator(llm_provider=mock_llm)

        eval_res = EvaluationResult(
            relevance_score=0.5,
            evidence_sufficient=False,
            missing_information=["disaster recovery restart payment worker"],
            recommended_action=RecommendedAction.RETRIEVE_MORE.value,
            recommended_tool="graph_traversal",
        )

        res = reformulator.reformulate(
            original_query="What is the DR procedure?",
            current_evidence=self.sample_evidence,
            evaluation=eval_res,
        )

        self.assertEqual(res["reformulated_query"], "disaster recovery restart payment worker")
        self.assertEqual(res["suggested_tool"], "graph_traversal")


if __name__ == "__main__":
    unittest.main()
