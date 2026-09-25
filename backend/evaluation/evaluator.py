"""
Evidence Evaluator & Self-RAG Reflection Engine (Phase 7).

Inspects retrieved knowledge chunks between retrieval and answer generation:
  1. Relevance Assessment: Evaluates whether each retrieved piece of evidence actually helps answer the query.
  2. Evidence Sufficiency: Evaluates whether the retrieved evidence contains enough facts and relationships
     to answer the user query completely without hallucination.
  3. Knowledge Gap Detection: Identifies missing information and suggests the optimal retrieval tool / strategy.
  4. LangGraph Flow Control: Emits structured EvaluationResult (GENERATE | RETRIEVE_MORE | REFORMULATE).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.generation.context_builder import ContextBuilder
from backend.llm.base import LLMProvider, Message, MessageRole
from backend.llm.factory import get_llm_provider
from backend.models.evaluation import (
    ChunkRelevance,
    EvaluationResult,
    RecommendedAction,
)

logger = logging.getLogger(__name__)


class EvidenceEvaluator:
    """
    Evaluates retrieval quality and knowledge sufficiency before generation.
    Acts as a quality control and reflection node in the LangGraph state machine.
    """

    SYSTEM_PROMPT = """You are an expert Enterprise Retrieval Evaluator & Self-RAG Critic.
Your job is to inspect retrieved evidence chunks against a user question before final answer generation.

You must rigorously evaluate three things:
1. Relevance: Does each chunk contain information relevant to the question? (score 0.0 to 1.0)
2. Sufficiency: Do the retrieved chunks contain ENOUGH complete facts and relationship links to answer the ENTIRE question without guessing or hallucinating?
3. Gap Identification & Next Action:
   - If the evidence is completely sufficient -> recommended_action = "GENERATE"
   - If the evidence is relevant but missing specific facts/links -> recommended_action = "RETRIEVE_MORE"
   - If the evidence is mostly irrelevant or off-topic -> recommended_action = "REFORMULATE"

Recommended tools when action is RETRIEVE_MORE or REFORMULATE:
- "github_entity_search": For PR details, commit authors, code contributors, team repo access, issue-to-PR links.
- "graph_traversal": For parent-child hierarchy navigation and procedural runbook steps.
- "resource_lookup": For full document or specific file lookups by URL/URI.
- "keyword_search": For exact error codes, ticket IDs (e.g. PAY-928), and specific identifiers.
- "semantic_search": For high-level conceptual questions, architectural overviews, and policy runbooks.

You MUST respond strictly with a valid JSON object in the following format:
```json
{
  "relevance_score": 0.95,
  "evidence_sufficient": false,
  "missing_information": [
    "Specific missing fact, link, or entity"
  ],
  "unsupported_claims": [],
  "recommended_action": "RETRIEVE_MORE",
  "recommended_tool": "github_entity_search",
  "chunk_evaluations": [
    {
      "chunk_id": "chunk_1",
      "score": 0.95,
      "is_relevant": true,
      "reason": "Explains payment service ownership."
    }
  ],
  "reasoning": "We identified the owning team, but we lack evidence of other projects that team supports."
}
```
"""

    def __init__(self, llm_provider: Optional[LLMProvider] = None) -> None:
        self.llm_provider = llm_provider or get_llm_provider()

    def evaluate_evidence(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Message]] = None,
        current_subgoal: Optional[str] = None,
    ) -> EvaluationResult:
        """
        Evaluates the relevance, completeness, and sufficiency of retrieved evidence.
        """
        # Edge case: No chunks retrieved
        if not chunks:
            return EvaluationResult(
                relevance_score=0.0,
                evidence_sufficient=False,
                missing_information=[query],
                unsupported_claims=[],
                recommended_action=RecommendedAction.RETRIEVE_MORE.value,
                recommended_tool="semantic_search",
                chunk_evaluations=[],
                reasoning="No evidence chunks were retrieved during the previous tool execution turn.",
            )

        context_str, _ = ContextBuilder.build_context(chunks)

        user_content = f"""USER QUESTION:
{query}
"""
        if current_subgoal:
            user_content += f"""CURRENT REASONING SUB-GOAL:
{current_subgoal}
"""

        user_content += f"""
RETRIEVED EVIDENCE CHUNKS ({len(chunks)}):
{context_str}

Evaluate the evidence above for answering the user question. Return ONLY a valid JSON object."""

        prompt_messages: List[Message] = [
            Message(role=MessageRole.SYSTEM, content=self.SYSTEM_PROMPT)
        ]
        if conversation_history:
            prompt_messages.extend(conversation_history)
        prompt_messages.append(Message(role=MessageRole.USER, content=user_content))

        raw_response = self.llm_provider.generate(messages=prompt_messages)
        response_text = raw_response if isinstance(raw_response, str) else getattr(raw_response, "content", str(raw_response))

        return self._parse_evaluation(response_text, chunks)

    def _parse_evaluation(self, response_text: str, chunks: List[Dict[str, Any]]) -> EvaluationResult:
        """Robust parser for LLM JSON output with fallback heuristics."""
        if not response_text:
            return self._heuristic_fallback(chunks, "Empty response from LLM evaluation.")

        # Extract JSON from markdown code block if present
        cleaned_text = response_text.strip()
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned_text, re.DOTALL)
        if json_match:
            cleaned_text = json_match.group(1).strip()
        elif cleaned_text.startswith("{") and cleaned_text.endswith("}"):
            cleaned_text = cleaned_text
        else:
            # Try to find first '{' and last '}'
            start_idx = cleaned_text.find("{")
            end_idx = cleaned_text.rfind("}")
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                cleaned_text = cleaned_text[start_idx : end_idx + 1]

        try:
            parsed = json.loads(cleaned_text)
            return EvaluationResult.from_dict(parsed)
        except Exception as e:
            logger.warning(f"Failed to parse LLM evaluation JSON ({e}). Raw response: {response_text[:200]}...")
            return self._heuristic_fallback(chunks, response_text)

    def _heuristic_fallback(self, chunks: List[Dict[str, Any]], raw_text: str) -> EvaluationResult:
        """Deterministic heuristic fallback when JSON parsing fails."""
        has_chunks = len(chunks) > 0
        upper_text = raw_text.upper()

        if "INSUFFICIENT" in upper_text or "RETRIEVE_MORE" in upper_text or not has_chunks:
            action = RecommendedAction.RETRIEVE_MORE.value
            sufficient = False
        elif "REFORMULATE" in upper_text:
            action = RecommendedAction.REFORMULATE.value
            sufficient = False
        else:
            action = RecommendedAction.GENERATE.value
            sufficient = True

        chunk_evals = [
            ChunkRelevance(
                chunk_id=c.get("chunk_id", f"chunk_{i}"),
                score=0.8 if sufficient else 0.5,
                is_relevant=True,
                reason="Evaluated via fallback heuristics.",
            )
            for i, c in enumerate(chunks, 1)
        ]

        return EvaluationResult(
            relevance_score=0.8 if sufficient else 0.5,
            evidence_sufficient=sufficient,
            missing_information=[] if sufficient else ["Additional context needed."],
            unsupported_claims=[],
            recommended_action=action,
            recommended_tool="semantic_search" if not sufficient else None,
            chunk_evaluations=chunk_evals,
            reasoning=f"Heuristic fallback: {raw_text[:120]}...",
        )
