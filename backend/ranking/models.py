"""
Data Models for Cross-Encoder Reranking in Enterprise Knowledge Retrieval.

Defines structured containers for candidate chunk re-scoring, calibrated relevance metrics,
and ranking transformations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RerankResult:
    """
    Structured result of cross-encoder scoring for a single candidate chunk.

    Attributes:
        chunk_id: Unique identifier of the chunk or graph node.
        text: Normalized text content evaluated by the cross-encoder.
        score: Calibrated relevance probability in the range [0.0, 1.0].
        original_rank: 1-based rank position prior to reranking.
        new_rank: 1-based rank position post-reranking.
        title: Document or section title for source attribution.
        source: Origin platform (github, notion, jira, dropbox, gmail, confluence).
        metadata: Arbitrary metadata attributes (permissions, breadcrumbs, sequence).
        chunk_dict: Original raw chunk dictionary representation.
    """

    chunk_id: str
    text: str
    score: float
    original_rank: int
    new_rank: int
    title: Optional[str] = None
    source: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunk_dict: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes the rerank result into a standard dictionary."""
        result = dict(self.chunk_dict) if self.chunk_dict else {}
        result.update(
            {
                "chunk_id": self.chunk_id,
                "text": self.text,
                "rerank_score": round(self.score, 4),
                "original_rank": self.original_rank,
                "rerank_rank": self.new_rank,
                "title": self.title,
                "source": self.source,
                "metadata": self.metadata,
            }
        )
        return result


@dataclass
class RerankRequest:
    """
    Encapsulates inputs for a batch reranking request.

    Attributes:
        query: User query or sub-query to evaluate cross-attention against.
        chunks: List of chunk dictionaries or SmartChunk objects.
        top_k: Maximum number of highest-scoring chunks to retain.
        score_threshold: Minimum calibrated score threshold (0.0 to 1.0) to filter noise.
    """

    query: str
    chunks: List[Any]
    top_k: Optional[int] = None
    score_threshold: float = 0.0
