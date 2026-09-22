"""
Data Models for Evidence Evaluation & Self-RAG Reflection (Phase 7).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


class RecommendedAction(str, Enum):
    """Action recommended by the EvidenceEvaluator."""
    GENERATE = "GENERATE"            # Evidence is sufficient -> proceed to answer generation
    RETRIEVE_MORE = "RETRIEVE_MORE"  # Evidence is relevant but incomplete -> multi-hop retrieval
    REFORMULATE = "REFORMULATE"      # Evidence is irrelevant / off-track -> reformulate query


@dataclass
class ChunkRelevance:
    """Relevance evaluation for an individual evidence chunk."""
    chunk_id: str
    score: float = 0.0               # 0.0 to 1.0
    is_relevant: bool = True
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationResult:
    """
    Structured outcome of the EvidenceEvaluator inspection loop.
    Controls conditional routing in the LangGraph agent state machine.
    """
    relevance_score: float = 1.0     # Mean relevance score of retrieved chunks (0.0 - 1.0)
    evidence_sufficient: bool = True # Whether evidence fully answers all parts of the user question
    missing_information: List[str] = field(default_factory=list) # Specific missing entities/relations
    unsupported_claims: List[str] = field(default_factory=list)  # Inferred facts lacking grounded source
    recommended_action: str = "GENERATE"                         # GENERATE | RETRIEVE_MORE | REFORMULATE
    recommended_tool: Optional[str] = None                       # Suggested tool for the next hop
    chunk_evaluations: List[ChunkRelevance] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relevance_score": self.relevance_score,
            "evidence_sufficient": self.evidence_sufficient,
            "missing_information": self.missing_information,
            "unsupported_claims": self.unsupported_claims,
            "recommended_action": self.recommended_action,
            "recommended_tool": self.recommended_tool,
            "chunk_evaluations": [c.to_dict() for c in self.chunk_evaluations],
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvaluationResult":
        chunk_evals = [
            ChunkRelevance(**c) if isinstance(c, dict) else c
            for c in data.get("chunk_evaluations", [])
        ]
        return cls(
            relevance_score=float(data.get("relevance_score", 1.0)),
            evidence_sufficient=bool(data.get("evidence_sufficient", True)),
            missing_information=list(data.get("missing_information", [])),
            unsupported_claims=list(data.get("unsupported_claims", [])),
            recommended_action=str(data.get("recommended_action", "GENERATE")),
            recommended_tool=data.get("recommended_tool"),
            chunk_evaluations=chunk_evals,
            reasoning=str(data.get("reasoning", "")),
        )
