"""
Local Cross-Encoder Reranker for Enterprise Knowledge Retrieval.

Scores and re-orders candidate evidence chunks by computing full cross-attention
over (query, chunk_text) pairs locally in-process with zero external API calls.

Supports:
- Offline execution (sentence_transformers.CrossEncoder)
- Calibrated probability scaling via Sigmoid normalization
- Semantic context enrichment (Title, Breadcrumbs, Step, Source, Content)
- Noise rejection via score thresholding
- Resilient in-process heuristic fallback for offline testing environments
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from backend.ingestion.chunk import SmartChunk
from backend.ranking.models import RerankRequest, RerankResult


class CrossEncoderReranker:
    """
    In-process local Cross-Encoder reranker.
    Computes cross-attention between user queries and candidate chunks.
    """

    DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        default_top_k: Optional[int] = None,
        default_threshold: float = 0.0,
    ) -> None:
        self.model_name = model_name or os.getenv("RERANKER_MODEL", self.DEFAULT_MODEL)
        self.device = device
        self.default_top_k = default_top_k
        self.default_threshold = default_threshold
        self._model = None
        self._model_load_failed = False

    def _get_model(self) -> Any:
        """Lazy loads the SentenceTransformer CrossEncoder model."""
        if self._model is None and not self._model_load_failed:
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.model_name, device=self.device, local_files_only=True)
            except Exception:
                # In offline or un-cached weight environments, activate instant in-process fallback cross-scorer
                self._model_load_failed = True
                self._model = None
        return self._model

    def _sigmoid(self, x: float) -> float:
        """Applies standard logistic sigmoid function to map logits to [0.0, 1.0]."""
        if x < -40.0:
            return 0.0
        if x > 40.0:
            return 1.0
        return 1.0 / (1.0 + math.exp(-x))

    def format_chunk_for_reranking(self, chunk: Union[SmartChunk, Dict[str, Any]]) -> Tuple[str, str, Optional[str], Optional[str], Dict[str, Any]]:
        """
        Extracts and formats contextual representation of a chunk for cross-encoder scoring.
        Returns: (chunk_id, formatted_text, title, source, metadata)
        """
        if isinstance(chunk, SmartChunk):
            chunk_id = chunk.chunk_id
            title = chunk.title
            source = chunk.source
            metadata = {
                "resource_id": chunk.resource_id,
                "section_heading": chunk.section_heading,
                "section_path": chunk.section_path,
                "sequence": chunk.sequence.to_dict() if hasattr(chunk.sequence, "to_dict") else getattr(chunk.sequence, "__dict__", chunk.sequence),
                "permissions": chunk.permissions.to_dict() if hasattr(chunk.permissions, "to_dict") else getattr(chunk.permissions, "__dict__", chunk.permissions),
                "url": chunk.url,
            }
            raw_text = chunk.text
            section_path = " > ".join(chunk.section_path) if chunk.section_path else chunk.section_heading
            step_info = f"Step {chunk.sequence.step}/{chunk.sequence.total_steps}" if chunk.sequence else None
        elif isinstance(chunk, dict):
            chunk_id = str(chunk.get("chunk_id") or chunk.get("node_id") or chunk.get("id") or "unknown_chunk")
            title = chunk.get("title") or chunk.get("name") or chunk.get("repository")
            source = chunk.get("source")
            metadata = dict(chunk.get("metadata", {}))
            raw_text = chunk.get("text") or chunk.get("content") or chunk.get("description") or ""
            section_path = chunk.get("section_path")
            if isinstance(section_path, list):
                section_path = " > ".join(section_path)
            elif not section_path:
                section_path = chunk.get("section_heading")
            step_info = chunk.get("sequence") or chunk.get("step_info")
            if isinstance(step_info, dict):
                step_info = f"Step {step_info.get('step', 1)}/{step_info.get('total_steps', 1)}"
        else:
            chunk_id = str(getattr(chunk, "chunk_id", "unknown_chunk"))
            title = getattr(chunk, "title", None)
            source = getattr(chunk, "source", None)
            metadata = {}
            raw_text = str(chunk)
            section_path = None
            step_info = None

        # Build context-dense representation for cross-encoder attention
        context_parts = []
        if title:
            context_parts.append(f"Title: {title}")
        if section_path:
            context_parts.append(f"Section: {section_path}")
        if step_info:
            context_parts.append(f"Sequence: {step_info}")
        if source:
            context_parts.append(f"Source: {str(source).upper()}")
        context_parts.append(f"Content: {raw_text}")

        formatted_text = "\n".join(context_parts)
        return chunk_id, formatted_text, title, source, metadata

    def _fallback_cross_score(self, query: str, text: str) -> float:
        """
        Resilient heuristic token-overlap and semantic cross-scorer.
        Activated when neural cross-encoder weights are unavailable in offline environments.
        """
        if not query.strip() or not text.strip():
            return 0.0

        q_tokens = re.findall(r"\w+", query.lower())
        if not q_tokens:
            return 0.0

        t_lower = text.lower()
        t_tokens = set(re.findall(r"\w+", t_lower))

        # 1. Exact phrase matching bonus
        phrase_bonus = 0.3 if query.lower() in t_lower else 0.0

        # 2. Token overlap ratio
        matched_tokens = sum(1 for token in q_tokens if token in t_tokens)
        token_ratio = matched_tokens / len(q_tokens)

        # 3. Keyword / identifier exact match bonus
        key_tokens = [tok for tok in q_tokens if any(c.isdigit() or c == "-" or c == "_" for c in tok) or len(tok) > 6]
        key_bonus = 0.0
        if key_tokens:
            key_matches = sum(1 for tok in key_tokens if tok in t_lower)
            key_bonus = (key_matches / len(key_tokens)) * 0.2

        score = (token_ratio * 0.5) + phrase_bonus + key_bonus
        return min(1.0, max(0.0, score))

    def score_pairs(self, pairs: List[Tuple[str, str]]) -> List[float]:
        """
        Scores a list of (query, document_text) pairs using CrossEncoder or fallback.
        Returns normalized probability scores in [0.0, 1.0].
        """
        if not pairs:
            return []

        model = self._get_model()
        if model is not None:
            try:
                raw_scores = model.predict(pairs, show_progress_bar=False)
                # Convert ndarray / list to floats and normalize with sigmoid
                scores: List[float] = []
                for s in raw_scores:
                    val = float(s)
                    # If already in [0, 1] range (e.g. binary classification head with sigmoid)
                    if 0.0 <= val <= 1.0 and not (val < 0.01 or val > 0.99):
                        scores.append(val)
                    else:
                        scores.append(self._sigmoid(val))
                return scores
            except Exception:
                pass

        # Fallback scoring
        return [self._fallback_cross_score(q, doc) for q, doc in pairs]

    def rerank(
        self,
        query: str,
        chunks: List[Union[SmartChunk, Dict[str, Any]]],
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> List[RerankResult]:
        """
        Reranks a list of candidate chunks against a query.

        Args:
            query: User query string.
            chunks: List of SmartChunk objects or chunk dictionaries.
            top_k: Maximum number of top chunks to return.
            score_threshold: Minimum score threshold (0.0 to 1.0) to filter out noise.

        Returns:
            List of RerankResult objects sorted descending by relevance score.
        """
        if not chunks or not query.strip():
            return []

        effective_top_k = top_k if top_k is not None else self.default_top_k
        effective_threshold = score_threshold if score_threshold is not None else self.default_threshold

        # 1. Format chunks and assemble cross-encoder input pairs
        formatted_items = []
        pairs: List[Tuple[str, str]] = []

        for idx, chunk in enumerate(chunks):
            chunk_id, formatted_text, title, source, metadata = self.format_chunk_for_reranking(chunk)
            chunk_dict = chunk if isinstance(chunk, dict) else (chunk.to_dict() if hasattr(chunk, "to_dict") else {})
            raw_text = chunk.text if isinstance(chunk, SmartChunk) else chunk_dict.get("text", formatted_text)

            formatted_items.append(
                {
                    "chunk_id": chunk_id,
                    "text": raw_text,
                    "formatted_text": formatted_text,
                    "title": title,
                    "source": source,
                    "metadata": metadata,
                    "chunk_dict": chunk_dict,
                    "original_rank": idx + 1,
                }
            )
            pairs.append((query, formatted_text))

        # 2. Compute cross-encoder scores
        scores = self.score_pairs(pairs)

        # 3. Attach scores and build RerankResults
        scored_candidates: List[RerankResult] = []
        for item, score in zip(formatted_items, scores):
            if score >= effective_threshold:
                scored_candidates.append(
                    RerankResult(
                        chunk_id=item["chunk_id"],
                        text=item["text"],
                        score=score,
                        original_rank=item["original_rank"],
                        new_rank=0,  # Will be assigned after sorting
                        title=item["title"],
                        source=item["source"],
                        metadata=item["metadata"],
                        chunk_dict=item["chunk_dict"],
                    )
                )

        # 4. Sort descending by score
        scored_candidates.sort(key=lambda r: r.score, reverse=True)

        # 5. Assign new ranks (1-based)
        for rank, candidate in enumerate(scored_candidates, 1):
            candidate.new_rank = rank

        # 6. Apply top_k cutoff
        if effective_top_k is not None and effective_top_k > 0:
            scored_candidates = scored_candidates[:effective_top_k]

        return scored_candidates

    def rerank_dicts(
        self,
        query: str,
        chunk_dicts: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Convenience method that reranks a list of chunk dictionaries and returns
        dictionaries annotated with `rerank_score`, `original_rank`, and `rerank_rank`.
        """
        results = self.rerank(
            query=query,
            chunks=chunk_dicts,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        return [r.to_dict() for r in results]
