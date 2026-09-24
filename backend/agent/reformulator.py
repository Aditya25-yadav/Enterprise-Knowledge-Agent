"""
Query Reformulator Node for Self-RAG Reflection in LangGraph (Phase 7).

Transforms the user query when the EvidenceEvaluator identifies missing information,
gap dependencies, or irrelevant retrieval turns.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.generation.context_builder import ContextBuilder
from backend.llm.base import LLMProvider, Message, MessageRole
from backend.llm.factory import get_llm_provider
from backend.models.evaluation import EvaluationResult

logger = logging.getLogger(__name__)


class QueryReformulator:
    """
    Synthesizes focused, high-precision retrieval sub-queries using feedback
    from the EvidenceEvaluator (missing information, recommended tools).
    """

    SYSTEM_PROMPT = """You are an expert Query Reformulator for an Enterprise Knowledge Agent.
Your job is to generate a new, highly targeted search query to find MISSING information identified during evidence evaluation.

Context given to you:
1. The ORIGINAL user question.
2. The EVIDENCE retrieved so far (and what facts are already established).
3. The MISSING INFORMATION gaps and recommended retrieval strategy from the EvidenceEvaluator.

Your goal:
Formulate a single, concise, high-precision retrieval query specifically designed to find the missing information in the next tool execution step.

Examples:
- Original: "Which team owns Payment Service and what other projects does that team support?"
  Established facts: "Payment Service is owned by Platform Engineering."
  Missing info: ["projects supported by Platform Engineering"]
  Recommended tool: "github_entity_search"
  Reformulated query: "What projects and repositories are accessible by Platform Engineering?"

- Original: "How do I fix error ERR_PAY_TIMEOUT in checkout?"
  Established facts: "None (checkout UI docs retrieved)."
  Missing info: ["error code ERR_PAY_TIMEOUT runbook"]
  Recommended tool: "keyword_search"
  Reformulated query: "ERR_PAY_TIMEOUT"

You MUST respond strictly with a valid JSON object in the following format:
```json
{
  "reformulated_query": "Targeted search query for the next retrieval hop",
  "reasoning": "Brief explanation of how this query resolves the knowledge gap",
  "suggested_tool": "github_entity_search"
}
```
"""

    def __init__(self, llm_provider: Optional[LLMProvider] = None) -> None:
        self.llm_provider = llm_provider or get_llm_provider()

    def reformulate(
        self,
        original_query: str,
        current_evidence: List[Dict[str, Any]],
        evaluation: EvaluationResult,
        conversation_history: Optional[List[Message]] = None,
    ) -> Dict[str, Any]:
        """
        Synthesizes a targeted sub-query to address missing information.
        """
        # Build concise summary of current evidence
        context_str, _ = ContextBuilder.build_context(current_evidence)
        missing_str = (
            "\n".join(f"- {gap}" for gap in evaluation.missing_information)
            if evaluation.missing_information
            else "None specified"
        )

        user_content = f"""ORIGINAL USER QUESTION:
{original_query}

EVIDENCE RETRIEVED SO FAR ({len(current_evidence)} chunks):
{context_str}

EVALUATION FEEDBACK:
- Status: {evaluation.recommended_action}
- Missing Information Gaps:
{missing_str}
- Evaluator Reasoning: {evaluation.reasoning}
- Recommended Next Tool: {evaluation.recommended_tool or 'Not specified'}

Generate a targeted retrieval query for the next turn. Return ONLY a valid JSON object."""

        prompt_messages: List[Message] = []
        if conversation_history:
            prompt_messages.extend(conversation_history)
        prompt_messages.append(Message(role=MessageRole.USER, content=user_content))

        raw_response = self.llm_provider.generate(messages=prompt_messages)
        response_text = (
            raw_response
            if isinstance(raw_response, str)
            else getattr(raw_response, "content", str(raw_response))
        )

        return self._parse_reformulation(response_text, original_query, evaluation)

    def _parse_reformulation(
        self,
        response_text: str,
        original_query: str,
        evaluation: EvaluationResult,
    ) -> Dict[str, Any]:
        """Parses LLM output into structured reformulation result with heuristic fallback."""
        if not response_text:
            return self._fallback_reformulation(original_query, evaluation, "Empty response from LLM.")

        cleaned_text = response_text.strip()
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned_text, re.DOTALL)
        if json_match:
            cleaned_text = json_match.group(1).strip()
        elif cleaned_text.startswith("{") and cleaned_text.endswith("}"):
            cleaned_text = cleaned_text
        else:
            start_idx = cleaned_text.find("{")
            end_idx = cleaned_text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                cleaned_text = cleaned_text[start_idx : end_idx + 1]

        try:
            parsed = json.loads(cleaned_text)
            ref_query = parsed.get("reformulated_query")
            if ref_query and isinstance(ref_query, str) and ref_query.strip():
                return {
                    "reformulated_query": ref_query.strip(),
                    "reasoning": parsed.get("reasoning", "Generated by QueryReformulator."),
                    "suggested_tool": parsed.get("suggested_tool") or evaluation.recommended_tool,
                }
        except Exception as e:
            logger.warning(f"Failed to parse QueryReformulator JSON ({e}). Raw response: {response_text[:200]}")

        return self._fallback_reformulation(original_query, evaluation, response_text)

    def _fallback_reformulation(
        self,
        original_query: str,
        evaluation: EvaluationResult,
        raw_text: str,
    ) -> Dict[str, Any]:
        """Heuristic fallback creating query from missing information gaps."""
        if evaluation.missing_information:
            fallback_query = " ".join(evaluation.missing_information)
        else:
            fallback_query = original_query

        return {
            "reformulated_query": fallback_query,
            "reasoning": f"Fallback generated from evaluation missing information gaps: {raw_text[:80]}",
            "suggested_tool": evaluation.recommended_tool or "semantic_search",
        }
