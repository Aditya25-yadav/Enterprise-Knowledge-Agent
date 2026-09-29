"""
Catalog Discovery & Cross-Connector Navigation Retriever (Phase 9+).

Enables Map-First / Progressive Disclosure exploration across all 6 connectors:
  1. Inspects Global Master Index (global_index.md) and Knowledge Ledger (global_log.md).
  2. Applies strict database-level RBAC pre-filtering to hide restricted documents.
  3. Uses two-stage matching (Lexical token overlap + LLM catalog reasoning).
  4. Assigns confidence scores (0.0 to 1.0) and emits targeted tool recommendations
     (resource_lookup, github_entity_search, graph_traversal, keyword_search) to guide
     the LangGraph Planner.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from backend.ingestion.catalog_aggregator import CatalogEntry, GlobalCatalogManager
from backend.llm.base import LLMProvider, Message, MessageRole
from backend.llm.factory import get_llm_provider
from backend.security.hierarchy import RoleHierarchy

logger = logging.getLogger(__name__)


@dataclass
class CatalogMatch:
    """
    A single catalog discovery match with confidence score and tool guidance.
    """
    title: str
    source: str
    resource_uri: str
    resource_type: str
    domain: str
    summary: str
    key_entities: List[str]
    confidence: float                          # 0.0 to 1.0
    recommended_tool: str                      # 'resource_lookup', 'github_entity_search', 'graph_traversal', etc.
    recommended_arguments: Dict[str, Any]
    reasoning: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["uri"] = self.resource_uri
        return d


class CatalogRetriever:
    """
    Retrieves and reasons over the global master index to provide the agent
    with a cross-connector map, confidence scores, and execution guidance.
    """

    CATALOG_REASONER_PROMPT = """You are an Enterprise Knowledge Discovery & Navigation Specialist.
Your job is to analyze a user question against a set of candidate documents from the Global Master Index across GitHub, Jira, Notion, Dropbox, Gmail, and Confluence.

For each candidate document:
1. Determine how relevant it is to answering the user question.
2. Assign a confidence score from 0.0 to 1.0.
3. Recommend the optimal specialized retrieval tool to fetch the information:
   - "hybrid_search": RECOMMENDED default for searching runbooks, procedures, specifications, and finding the most relevant chunks using semantic vectors + BM25 + reranking.
   - "resource_lookup": For full document retrieval by exact document title or Jira ticket key (e.g. 'PAY-928') when full sequential text is needed.
   - "github_entity_search": For PR details, commit authors, file contributors, or code relationships in GitHub.
   - "keyword_search": For exact error codes or specific technical identifiers.
   - "graph_traversal": For parent-child document trees or sequential workflow steps.
4. Provide the exact recommended arguments for calling that tool.

Respond STRICTLY with a valid JSON array of objects with the following schema:
```json
[
  {
    "resource_uri": "https://jira.company.com/browse/PAY-928",
    "confidence": 0.95,
    "recommended_tool": "hybrid_search",
    "recommended_arguments": {
      "query": "PAY-928 3DS authentication timeout"
    },
    "reasoning": "Contains the root cause and status for the 3DS timeout incident."
  }
]
```
"""

    def __init__(
        self,
        catalog_manager: Optional[GlobalCatalogManager] = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        self.catalog_manager = catalog_manager or GlobalCatalogManager()
        self.llm_provider = llm_provider

    def discover(
        self,
        query: str,
        user_context: Optional[Dict[str, Any]] = None,
        domain: Optional[str] = None,
        connector: Optional[str] = None,
        max_results: int = 6,
    ) -> Dict[str, Any]:
        """
        Explores the global master index, evaluates candidates, and generates
        a structured cross-connector discovery manifest with tool guidance.
        """
        user_ctx = user_context or {"roles": ["employee"], "user_id": "user@enterprise.com"}
        user_roles = user_ctx.get("roles", ["employee"])

        # 1. RBAC Pre-Filter: Retrieve only accessible entries
        accessible_entries = self.catalog_manager.get_entries_for_roles(user_roles)
        if not accessible_entries:
            return {
                "manifest": [],
                "overall_confidence": 0.0,
                "recommended_plan": [],
                "summary": "No accessible documents found in the Global Master Index for the given security roles.",
            }

        # 2. Lexical & Entity Overlap Scoring (Stage 1)
        scored_candidates = self._score_candidates(
            query=query,
            entries=accessible_entries,
            filter_domain=domain,
            filter_connector=connector,
        )

        top_candidates = scored_candidates[:max_results]
        if not top_candidates:
            # Fallback: return general accessible entries for the domain if specific search yields 0
            top_candidates = [(e, 0.5) for e in accessible_entries[:max_results]]

        # 3. LLM Catalog Reasoning & Confidence Scorer (Stage 2)
        matches = self._rank_and_guide_candidates(
            query=query,
            candidates=[c[0] for c in top_candidates],
        )

        overall_conf = max((m.confidence for m in matches), default=0.0)
        
        # Build recommended execution plan
        plan_steps = []
        for m in sorted(matches, key=lambda x: x.confidence, reverse=True):
            if m.confidence >= 0.5:
                args_json = json.dumps(m.recommended_arguments)
                plan_steps.append(f"Call `{m.recommended_tool}({args_json})` on {m.source.upper()} for '{m.title}' (Confidence: {m.confidence:.2f})")

        # Build readable summary string for Reasoner prompt
        summary_lines = [
            f"Global Master Index Discovery for '{query}':",
            f"Found {len(matches)} relevant cross-connector assets across enterprise domains.",
        ]
        for m in matches:
            args_json = json.dumps(m.recommended_arguments)
            summary_lines.append(
                f"• [{m.source.upper()}] '{m.title}' ({m.domain}) -> Recommended: `{m.recommended_tool}({args_json})` (Conf: {m.confidence:.2f})"
            )

        return {
            "query": query,
            "manifest": [m.to_dict() for m in matches],
            "overall_confidence": round(overall_conf, 3),
            "recommended_plan": plan_steps,
            "summary": "\n".join(summary_lines),
        }

    # ── Internal Candidate Scoring & LLM Reasoning ──────────────────────────

    def _score_candidates(
        self,
        query: str,
        entries: List[CatalogEntry],
        filter_domain: Optional[str] = None,
        filter_connector: Optional[str] = None,
    ) -> List[tuple[CatalogEntry, float]]:
        """Scores catalog entries against query tokens and metadata filters."""
        query_clean = query.lower()
        raw_tokens = re.findall(r"\b[a-zA-Z0-9_\-#]+\b", query_clean)
        stopwords = {"what", "is", "for", "the", "and", "in", "on", "a", "an", "to", "of", "with", "how", "do", "i", "we", "can", "our"}
        meaningful_tokens = [t for t in raw_tokens if t not in stopwords and len(t) > 1]
        if not meaningful_tokens:
            meaningful_tokens = [t for t in raw_tokens if len(t) > 1]

        scored: List[tuple[CatalogEntry, float]] = []

        for entry in entries:
            # Optional domain/connector filters
            if filter_domain and filter_domain.lower() not in entry.domain.lower():
                continue
            if filter_connector and filter_connector.lower() != entry.source.lower():
                continue

            score = 0.0
            title_lower = entry.title.lower()
            summary_lower = entry.summary.lower()
            domain_lower = entry.domain.lower()
            uri_lower = entry.resource_uri.lower()

            # 1. Direct whole-query phrase match
            if query_clean in title_lower or query_clean in summary_lower:
                score += 8.0

            # 2. Token overlap matches
            for t in meaningful_tokens:
                if t in title_lower:
                    score += 3.0
                if t in summary_lower:
                    score += 2.0
                if t in domain_lower:
                    score += 1.0
                if t in uri_lower:
                    score += 2.0

            # 3. Key entity matches (e.g. PAY-928, #142, patronictl, 60s, unresponsive)
            for entity in entry.key_entities:
                entity_clean = entity.lower()
                if entity_clean in query_clean:
                    score += 4.0
                elif any(t == entity_clean or t in entity_clean for t in meaningful_tokens if len(t) > 2):
                    score += 2.5

            if score > 0:
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _rank_and_guide_candidates(
        self,
        query: str,
        candidates: List[CatalogEntry],
    ) -> List[CatalogMatch]:
        """
        Uses LLM (or heuristic fallback) to assign confidence scores and downstream tool guidance.
        """
        if not candidates:
            return []

        # Attempt LLM reasoning if provider available
        if self.llm_provider:
            try:
                candidate_payload = [
                    {
                        "title": c.title,
                        "source": c.source,
                        "resource_uri": c.resource_uri,
                        "domain": c.domain,
                        "summary": c.summary,
                        "key_entities": c.key_entities,
                    }
                    for c in candidates
                ]

                prompt_text = f"""USER QUESTION:
{query}

CANDIDATE CATALOG ASSETS:
{json.dumps(candidate_payload, indent=2, ensure_ascii=False)}

Analyze the candidates and return a JSON array with confidence scores and recommended tools."""

                messages = [
                    Message(role=MessageRole.SYSTEM, content=self.CATALOG_REASONER_PROMPT),
                    Message(role=MessageRole.USER, content=prompt_text),
                ]

                response = self.llm_provider.generate(messages)
                resp_text = response if isinstance(response, str) else getattr(response, "content", str(response))
                parsed_llm = self._parse_llm_response(resp_text)

                if parsed_llm:
                    return self._merge_llm_guidance(candidates, parsed_llm)
            except Exception as e:
                logger.warning(f"Catalog LLM ranking failed ({e}); falling back to deterministic heuristics.")

        # Deterministic Heuristics Fallback
        return self._heuristic_guidance(query, candidates)

    def _parse_llm_response(self, text: str) -> Optional[List[Dict[str, Any]]]:
        """Extracts JSON array from LLM response."""
        cleaned = text.strip()
        json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", cleaned, re.DOTALL)
        if json_match:
            cleaned = json_match.group(1).strip()
        elif cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return None

    def _merge_llm_guidance(
        self,
        candidates: List[CatalogEntry],
        llm_guidance: List[Dict[str, Any]],
    ) -> List[CatalogMatch]:
        """Merges LLM confidence and recommended tools into CatalogMatch objects."""
        guidance_by_uri = {g.get("resource_uri"): g for g in llm_guidance if g.get("resource_uri")}
        matches: List[CatalogMatch] = []

        for c in candidates:
            g = guidance_by_uri.get(c.resource_uri, {})
            conf = float(g.get("confidence", 0.8))
            tool = g.get("recommended_tool") or self._infer_default_tool(c)
            args = g.get("recommended_arguments") or self._infer_default_arguments(c, tool)
            reason = g.get("reasoning") or f"Matched in {c.domain} catalog."

            matches.append(
                CatalogMatch(
                    title=c.title,
                    source=c.source,
                    resource_uri=c.resource_uri,
                    resource_type=c.resource_type,
                    domain=c.domain,
                    summary=c.summary,
                    key_entities=c.key_entities,
                    confidence=min(1.0, max(0.0, conf)),
                    recommended_tool=tool,
                    recommended_arguments=args,
                    reasoning=reason,
                )
            )

        return sorted(matches, key=lambda m: m.confidence, reverse=True)

    def _heuristic_guidance(
        self,
        query: str,
        candidates: List[CatalogEntry],
    ) -> List[CatalogMatch]:
        """Deterministic heuristic tool recommender and confidence scorer."""
        matches: List[CatalogMatch] = []
        q_low = query.lower()

        for c in candidates:
            # Score confidence based on token presence
            hits = sum(1 for e in c.key_entities if e.lower() in q_low)
            title_hit = any(w in c.title.lower() for w in q_low.split() if len(w) > 3)
            
            conf = 0.95 if hits >= 1 else (0.85 if title_hit else 0.70)
            tool = self._infer_default_tool(c)
            args = self._infer_default_arguments(c, tool)

            matches.append(
                CatalogMatch(
                    title=c.title,
                    source=c.source,
                    resource_uri=c.resource_uri,
                    resource_type=c.resource_type,
                    domain=c.domain,
                    summary=c.summary,
                    key_entities=c.key_entities,
                    confidence=conf,
                    recommended_tool=tool,
                    recommended_arguments=args,
                    reasoning=f"High-density match for '{c.title}' in {c.domain} domain.",
                )
            )

        return sorted(matches, key=lambda m: m.confidence, reverse=True)

    def _infer_default_tool(self, entry: CatalogEntry) -> str:
        """Infers recommended retrieval tool based on source and asset type."""
        if entry.source == "github":
            # If entry represents a PR or code repository, recommend github_entity_search
            if any(e.startswith("PR #") for e in entry.key_entities) or "pull" in entry.resource_uri:
                return "github_entity_search"
            return "hybrid_search"
        if entry.source == "jira" or entry.resource_type == "issue":
            jira_keys = [e for e in entry.key_entities if re.match(r"^[A-Z]{2,10}-\d+$", e)]
            if jira_keys:
                return "resource_lookup"
            return "hybrid_search"
        # For general documentation, runbooks, SOPs, policies across Dropbox, Notion, Confluence, Gmail
        return "hybrid_search"

    def _infer_default_arguments(self, entry: CatalogEntry, tool: str) -> Dict[str, Any]:
        """Constructs suggested tool arguments."""
        if tool == "resource_lookup":
            # For Jira issues, use Jira issue key if available
            if entry.source == "jira" or entry.resource_type == "issue":
                jira_keys = [e for e in entry.key_entities if re.match(r"^[A-Z]{2,10}-\d+$", e)]
                if jira_keys:
                    return {"resource_id": jira_keys[0]}
                return {"resource_id": entry.resource_uri or entry.title}
            # For other platform documents (Dropbox, Notion, Gmail, Confluence, GitHub files),
            # use their unique resource_uri or full title
            return {"resource_id": entry.resource_uri or entry.title}

        if tool == "github_entity_search":
            pr_keys = [e for e in entry.key_entities if e.startswith("PR #")]
            if pr_keys:
                pr_num = pr_keys[0].replace("PR #", "#")
                return {"operation": "get_pr_details", "target": pr_num}
            return {"operation": "get_repo_overview", "target": entry.title}

        if tool in ("hybrid_search", "keyword_search", "semantic_search"):
            return {"query": entry.title}

        return {"query": entry.title}

