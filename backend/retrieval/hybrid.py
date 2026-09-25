"""
Hybrid Retrieval & Reciprocal Rank Fusion (RRF) Engine for Enterprise Knowledge.

Combines multiple disparate retrieval modalities:
1. Dense Vector Search (Qdrant) — semantic concepts & natural language intent.
2. Sparse Lexical Search (BM25) — exact keywords, issue IDs, error codes, and code tokens.
3. Property Graph Retrieval (Neo4j / In-Memory) — entity relationships, PRs, contributors, and document hierarchy.

Merges multi-modal candidate rankings using the Reciprocal Rank Fusion (RRF) algorithm:
    RRF_score(d) = sum_{m in M} ( w_m / (k + rank_m(d)) )

Features:
- Parameterized smoothing factor (k = 60 by default)
- Configurable modality weights (e.g. vector: 1.0, keyword: 1.0, graph: 0.8)
- End-to-end database-level RBAC propagation across all dispatched sub-retrievers
- Detailed fusion provenance: tracks `modalities_matched` and `ranks_per_modality`
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Union

from backend.models.security import UserSecurityContext
from backend.retrieval.entity_graph import EntityGraphRetriever
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.semantic import SemanticRetriever


def reciprocal_rank_fusion(
    ranked_lists: Dict[str, Sequence[Union[Dict[str, Any], Any]]],
    k: int = 60,
    weights: Optional[Dict[str, float]] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Combines multiple ranked lists of candidate items using Reciprocal Rank Fusion (RRF).

    Args:
        ranked_lists: Dictionary mapping modality name ('vector', 'keyword', 'graph', etc.)
                      to a sequence of ranked chunk dictionaries or objects.
        k: Smoothing constant (standard default: 60) to prevent top items from dominating.
        weights: Optional dictionary mapping modality name to importance weight (default: 1.0).
        top_k: Optional cutoff limiting the number of fused results returned.

    Returns:
        List of merged chunk dictionaries sorted descending by `rrf_score`.
        Each dictionary is annotated with `rrf_score`, `modalities_matched`, and `ranks_per_modality`.
    """
    if not ranked_lists:
        return []

    try:
        k = int(k) if k is not None else 60
    except (ValueError, TypeError):
        k = 60

    effective_weights = weights or {}
    fused_scores: Dict[str, float] = {}
    fused_items: Dict[str, Dict[str, Any]] = {}
    matched_modalities: Dict[str, List[str]] = {}
    modality_ranks: Dict[str, Dict[str, int]] = {}

    for modality, items in ranked_lists.items():
        weight = float(effective_weights.get(modality, 1.0))
        for rank_idx, item in enumerate(items, 1):
            if isinstance(item, dict):
                chunk_dict = dict(item)
            elif hasattr(item, "to_dict") and callable(item.to_dict):
                chunk_dict = item.to_dict()
            elif hasattr(item, "__dict__"):
                chunk_dict = dict(item.__dict__)
            else:
                chunk_dict = {"text": str(item)}

            # Resolve unique identifier for deduplication
            chunk_id = str(
                chunk_dict.get("chunk_id")
                or chunk_dict.get("node_id")
                or chunk_dict.get("resource_id")
                or chunk_dict.get("id")
                or f"{modality}_{rank_idx}"
            )

            # Compute RRF score increment
            rrf_increment = weight / (k + rank_idx)
            fused_scores[chunk_id] = fused_scores.get(chunk_id, 0.0) + rrf_increment

            # Track fusion metadata
            if chunk_id not in fused_items:
                fused_items[chunk_id] = chunk_dict
                matched_modalities[chunk_id] = []
                modality_ranks[chunk_id] = {}

            if modality not in matched_modalities[chunk_id]:
                matched_modalities[chunk_id].append(modality)
            modality_ranks[chunk_id][modality] = rank_idx

            # Merge or enrich text/title if previous modality had sparse representation
            if not fused_items[chunk_id].get("title") and chunk_dict.get("title"):
                fused_items[chunk_id]["title"] = chunk_dict["title"]
            if not fused_items[chunk_id].get("source") and chunk_dict.get("source"):
                fused_items[chunk_id]["source"] = chunk_dict["source"]

    # Assemble fused list sorted descending by RRF score
    sorted_chunk_ids = sorted(fused_scores.keys(), key=lambda cid: fused_scores[cid], reverse=True)

    results: List[Dict[str, Any]] = []
    for rank, chunk_id in enumerate(sorted_chunk_ids, 1):
        item = dict(fused_items[chunk_id])
        item["chunk_id"] = chunk_id
        item["rrf_score"] = round(fused_scores[chunk_id], 6)
        item["rrf_rank"] = rank
        item["modalities_matched"] = matched_modalities[chunk_id]
        item["ranks_per_modality"] = modality_ranks[chunk_id]
        results.append(item)

    if top_k is not None:
        try:
            top_k_val = int(top_k)
            if top_k_val > 0:
                results = results[:top_k_val]
        except (ValueError, TypeError):
            pass

    return results


class HybridRetriever:
    """
    Orchestrates hybrid retrieval across Vector, Keyword (BM25), and Graph engines,
    merging results via Reciprocal Rank Fusion (RRF) with database-level RBAC.
    """

    DEFAULT_K = 60
    DEFAULT_WEIGHTS = {
        "vector": 1.0,
        "keyword": 1.0,
        "graph": 0.8,
    }

    def __init__(
        self,
        semantic_retriever: Optional[SemanticRetriever] = None,
        keyword_retriever: Optional[KeywordRetriever] = None,
        entity_graph_retriever: Optional[EntityGraphRetriever] = None,
        graph_retriever: Optional[GraphRetriever] = None,
        default_k: int = DEFAULT_K,
        default_weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self.semantic_retriever = semantic_retriever
        self.keyword_retriever = keyword_retriever
        self.entity_graph_retriever = entity_graph_retriever
        self.graph_retriever = graph_retriever
        self.default_k = default_k
        self.default_weights = default_weights or dict(self.DEFAULT_WEIGHTS)

    def search(
        self,
        query: str,
        top_k: int = 10,
        modalities: Optional[List[str]] = None,
        weights: Optional[Dict[str, float]] = None,
        k: Optional[int] = None,
        user_context: Optional[Union[Dict[str, Any], UserSecurityContext]] = None,
        metadata_filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executes hybrid multi-modal retrieval and returns RRF-fused candidate chunks.

        Args:
            query: User search query or question.
            top_k: Number of fused results to return.
            modalities: List of modalities to execute (e.g. ['vector', 'keyword', 'graph']).
                        Defaults to all available configured sub-retrievers.
            weights: Dictionary of modality weights for RRF calculation.
            k: Smoothing constant for RRF (default: 60).
            user_context: Security context for RBAC enforcement across all sub-retrievers.
            metadata_filters: Optional metadata filtering constraints.

        Returns:
            List of fused candidate chunk dictionaries sorted descending by RRF score.
        """
        if not query or not query.strip():
            return []

        try:
            top_k = int(top_k) if top_k is not None else 10
        except (ValueError, TypeError):
            top_k = 10

        active_modalities = modalities or ["vector", "keyword", "graph"]
        try:
            effective_k = int(k) if k is not None else int(self.default_k)
        except (ValueError, TypeError):
            effective_k = int(self.default_k)
        effective_weights = weights or self.default_weights

        ranked_lists: Dict[str, List[Dict[str, Any]]] = {}

        # 1. Dense Vector Search
        if "vector" in active_modalities and self.semantic_retriever is not None:
            try:
                filters = metadata_filters or {}
                vector_chunks = self.semantic_retriever.search(
                    query=query,
                    top_k=top_k * 2,
                    user_context=user_context if isinstance(user_context, dict) else (user_context.to_dict() if user_context else None),
                    source=filters.get("source"),
                    resource_type=filters.get("resource_type"),
                )
                if vector_chunks:
                    ranked_lists["vector"] = vector_chunks
            except Exception:
                pass

        # 2. Sparse BM25 Keyword Search
        if "keyword" in active_modalities and self.keyword_retriever is not None:
            try:
                filters = metadata_filters or {}
                keyword_chunks = self.keyword_retriever.search(
                    query=query,
                    top_k=top_k * 2,
                    user_context=user_context if isinstance(user_context, dict) else (user_context.to_dict() if user_context else None),
                    source=filters.get("source"),
                    resource_type=filters.get("resource_type"),
                )
                if keyword_chunks:
                    ranked_lists["keyword"] = keyword_chunks
            except Exception:
                pass

        # 3. Property Graph Search (Entity Graph or Structural Graph)
        if "graph" in active_modalities:
            graph_results: List[Dict[str, Any]] = []

            # 3a. Entity Graph (PRs, commits, developers, issues)
            if self.entity_graph_retriever is not None:
                try:
                    nodes = self.entity_graph_retriever.search_nodes(
                        keyword=query,
                        limit=top_k,
                        user_context=user_context,
                    )
                    for n in nodes:
                        node_dict = n.to_dict() if hasattr(n, "to_dict") else dict(n)
                        node_id = node_dict.get("node_id") or node_dict.get("id") or str(n)
                        graph_results.append(
                            {
                                "chunk_id": node_id,
                                "title": node_dict.get("name") or node_dict.get("title") or node_dict.get("full_name") or node_id,
                                "source": "github" if "github" in node_id or "repo" in node_id or "pr" in node_id else "graph",
                                "resource_type": node_dict.get("label", "entity").lower(),
                                "text": json.dumps(node_dict, ensure_ascii=False, indent=2),
                                "metadata": node_dict,
                            }
                        )
                except Exception:
                    pass

            # 3b. Structural Hierarchy Graph
            if self.graph_retriever is not None and not graph_results:
                try:
                    child_chunks = self.graph_retriever.get_children(
                        parent_id=query,
                        user_context=user_context if isinstance(user_context, dict) else (user_context.to_dict() if user_context else None),
                    )
                    if child_chunks:
                        graph_results.extend(child_chunks)
                except Exception:
                    pass

            if graph_results:
                ranked_lists["graph"] = graph_results

        # 4. Apply Reciprocal Rank Fusion over all collected candidate lists
        fused_results = reciprocal_rank_fusion(
            ranked_lists=ranked_lists,
            k=effective_k,
            weights=effective_weights,
            top_k=top_k,
        )

        return fused_results
