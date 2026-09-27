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
2. Sufficiency: Do the retrieved chunks contain ENOUGH complete facts, details, and relationship links to answer the ENTIRE question without guessing or hallucinating?
   - NOTE ON CATALOG DISCOVERY & INDEX SUMMARIES: If the retrieved chunks ONLY contain high-level catalog summaries, discovery manifests, or index metadata (chunks labeled [CATALOG DISCOVERY SUMMARY] or is_catalog=true), but lack the full document body, exact runbook steps, commit diffs, ticket descriptions, or complete facts required by the question, the evidence is INSUFFICIENT. You MUST mark `evidence_sufficient: false` and `recommended_action: "RETRIEVE_MORE"`, specifying the recommended deep retrieval tool (e.g., hybrid_search, github_entity_search, resource_lookup, keyword_search) suggested by the catalog match.
3. Gap Identification & Next Action:
   - If the evidence is completely sufficient with full document/entity content -> recommended_action = "GENERATE"
   - If the evidence is relevant but missing specific facts/links or is only at catalog/index level -> recommended_action = "RETRIEVE_MORE"
   - If the evidence is mostly irrelevant or off-topic -> recommended_action = "REFORMULATE"

4. Conflict & Supersession Awareness: If retrieved evidence chunks contain conflicting facts (e.g. older RFC timeouts vs newer PR hotfixes), note the supersession and identify the authoritative source in your reasoning.

Recommended tools when action is RETRIEVE_MORE or REFORMULATE:
- "hybrid_search": Preferred retrieval tool to find specific passages, runbooks, SOPs, and error troubleshooting across all platforms with semantic vector + BM25 ranking.
- "github_entity_search": For PR details, commit authors, code contributors, team repo access, issue-to-PR links.
- "resource_lookup": For full sequential document or specific file lookups by URL/URI or Jira ticket ID (e.g. PAY-928).
- "keyword_search": For exact error codes, ticket IDs, and specific identifiers.
- "graph_traversal": For parent-child hierarchy navigation and procedural runbook steps.
- "catalog_discovery": To explore the global master index across platforms if initial direction is completely unknown.

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
  "recommended_tool": "hybrid_search",
  "chunk_evaluations": [
    {
      "chunk_id": "chunk_1",
      "score": 0.95,
      "is_relevant": true,
      "reason": "Explains payment service ownership."
    }
  ],
  "reasoning": "Catalog summary found matching runbook, but full document body must be fetched."
}
```
"""

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        context_format: str = "standard",
    ) -> None:
        self.llm_provider = llm_provider or get_llm_provider()
        self.context_format = context_format

    def evaluate_evidence(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Message]] = None,
        current_subgoal: Optional[str] = None,
        context_format: Optional[str] = None,
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
                recommended_tool="catalog_discovery",
                chunk_evaluations=[],
                reasoning="No evidence chunks were retrieved during the previous tool execution turn.",
            )

        fmt = context_format or self.context_format
        context_str, _ = ContextBuilder.build_context(chunks, format=fmt)

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
            eval_result = EvaluationResult.from_dict(parsed)

            # Check if all chunks are catalog entries
            all_catalog = bool(chunks) and all(c.get("is_catalog") or c.get("source") == "catalog" for c in chunks)
            if all_catalog and eval_result.evidence_sufficient:
                # Force RETRIEVE_MORE if only high-level catalog summaries exist
                first_rec_tool = chunks[0].get("recommended_tool") or "resource_lookup"
                eval_result.evidence_sufficient = False
                eval_result.recommended_action = RecommendedAction.RETRIEVE_MORE.value
                eval_result.recommended_tool = eval_result.recommended_tool or first_rec_tool
                if not eval_result.missing_information:
                    eval_result.missing_information = ["Full document contents and detailed evidence needed from catalog matches."]
                eval_result.reasoning = (eval_result.reasoning or "") + " (Catalog manifest identified targets; full document retrieval required.)"

            return eval_result
        except Exception as e:
            logger.warning(f"Failed to parse LLM evaluation JSON ({e}). Raw response: {response_text[:200]}...")
            return self._heuristic_fallback(chunks, response_text)

    def _heuristic_fallback(self, chunks: List[Dict[str, Any]], raw_text: str) -> EvaluationResult:
        """Deterministic heuristic fallback when JSON parsing fails."""
        has_chunks = len(chunks) > 0
        upper_text = raw_text.upper()
        all_catalog = has_chunks and all(c.get("is_catalog") or c.get("source") == "catalog" for c in chunks)

        if all_catalog:
            action = RecommendedAction.RETRIEVE_MORE.value
            sufficient = False
            rec_tool = chunks[0].get("recommended_tool") or "resource_lookup"
            missing = ["Full document contents and detailed evidence needed from catalog matches."]
        elif "INSUFFICIENT" in upper_text or "RETRIEVE_MORE" in upper_text or not has_chunks:
            action = RecommendedAction.RETRIEVE_MORE.value
            sufficient = False
            rec_tool = "semantic_search"
            missing = ["Additional context needed."]
        elif "REFORMULATE" in upper_text:
            action = RecommendedAction.REFORMULATE.value
            sufficient = False
            rec_tool = "semantic_search"
            missing = ["Query reformulation needed."]
        else:
            action = RecommendedAction.GENERATE.value
            sufficient = True
            rec_tool = None
            missing = []

        chunk_evals = [
            ChunkRelevance(
                chunk_id=c.get("chunk_id", f"chunk_{i}"),
                score=0.85 if c.get("is_catalog") else (0.8 if sufficient else 0.5),
                is_relevant=True,
                reason="Catalog summary match." if c.get("is_catalog") else "Evaluated via fallback heuristics.",
            )
            for i, c in enumerate(chunks, 1)
        ]

        return EvaluationResult(
            relevance_score=0.85 if all_catalog else (0.8 if sufficient else 0.5),
            evidence_sufficient=sufficient,
            missing_information=missing,
            unsupported_claims=[],
            recommended_action=action,
            recommended_tool=rec_tool,
            chunk_evaluations=chunk_evals,
            reasoning=f"Heuristic fallback: {raw_text[:120]}...",
        )
