# Phase 10: Hybrid Search Fusion Node & Reciprocal Rank Fusion (RRF) in LangGraph

**Date:** 2026-09-22  
**Status:** ✅ Fully Implemented, Integrated, and Verified  
**Component:** `backend.retrieval.hybrid.HybridRetriever`, `backend.retrieval.hybrid.reciprocal_rank_fusion`, `backend.agent.tools.ToolRegistry`, `backend.agent.langchain_tools.create_langchain_tools`, `backend.agent.langgraph_planner.LangGraphAgentPlanner`

---

## 1. Overview & Architectural Motivation

In enterprise knowledge environments, user queries range across distinct informational modalities:
1. **Conceptual / High-Level Queries:** "How do we handle idempotency in payments?" (Best served by Dense Vector Search in Qdrant).
2. **Exact Technical Tokens & Identifiers:** "PAY-928", "#142", `AuthService.charge`, `ECONNREFUSED` (Best served by Sparse BM25+ Lexical Search).
3. **Entity Relationships & Provenance:** "Who reviewed the PR that fixed the checkout timeout?" (Best served by Property Graph & Neo4j/In-Memory Traversal).

Instead of forcing the LLM planner to make multiple disjoint tool calls for queries containing both conceptual topics and exact identifiers, **Phase 10 introduces the unified `HybridRetriever` and `hybrid_search` tool powered by Reciprocal Rank Fusion (RRF)**.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               User Multi-Modal Query                                   │
│            "Fix 3DS checkout timeout PAY-928 payments initiate idempotency"             │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        HybridRetriever (backend/retrieval/hybrid.py)                   │
│   ┌───────────────────────────┬───────────────────────────┬──────────────────────────┐ │
│   │    Dense Vector Search    │    Sparse BM25 Search     │    Property Graph Search │ │
│   │    (Qdrant Embeddings)    │      (BM25+ Index)        │      (Entity Graph)      │ │
│   └─────────────┬─────────────┴─────────────┬─────────────┴────────────┬─────────────┘ │
│                 │ (rank_vec)                │ (rank_kw)                │ (rank_grp)    │
│                 ▼                           ▼                          ▼               │
│   ┌──────────────────────────────────────────────────────────────────────────────────┐ │
│   │                     Reciprocal Rank Fusion (RRF) Engine                          │ │
│   │                                                                                  │ │
│   │               RRF_score(d) = ∑  w_m / (k + rank_m(d))                            │ │
│   │                             m∈M                                                  │ │
│   │                                                                                  │ │
│   │     • k = 60 (smoothing factor)                                                  │ │
│   │     • Multi-modality boosting & deduplication                                    │ │
│   │     • Full provenance: modalities_matched, ranks_per_modality                    │ │
│   │     • End-to-end database-level RBAC propagation                                 │ │
│   └─────────────────────────────────────────┬────────────────────────────────────────┘ │
└─────────────────────────────────────────────┼──────────────────────────────────────────┘
                                              │
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                             LangGraph 6-Node State Machine                             │
│       START ──► reasoner ──► tool_node (hybrid_search) ──► reranker ──► evaluator     │
│                                                                              │         │
│                                           generator ◄────────────────────────┘         │
│                                               │                                        │
│                                               ▼                                        │
│                                     Grounded & Cited Answer                            │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Mathematical Formulation & Rank Fusion Invariant

Reciprocal Rank Fusion (RRF) calculates the cumulative relevance of document $d$ across a set of retrieval modalities $M$:

$$\text{RRF\_score}(d) = \sum_{m \in M} \frac{w_m}{k + \text{rank}_m(d)}$$

Where:
- $\text{rank}_m(d) \in \{1, 2, \dots, N\}$ is the 1-based rank position of document $d$ within modality $m$'s candidate list.
- $k \ge 0$ is the smoothing constant (standard default $k = 60$), preventing early rank positions from completely dominating.
- $w_m \in \mathbb{R}^+$ is the importance weight assigned to modality $m$ (default: `vector: 1.0`, `keyword: 1.0`, `graph: 0.8`).

### Key Properties of RRF:
1. **Scale Invariance:** RRF operates purely on relative rank indices, eliminating score calibration mismatches between bounded cosine similarities ($[0.0, 1.0]$) and unbounded lexical BM25 scores ($[0, \infty)$).
2. **Multi-Modality Consensus Boosting:** Candidates appearing in multiple modalities (e.g. top in Vector AND top in BM25) receive additive reciprocal score boosts, guaranteeing higher final ranks than items matched by only a single modality.
3. **Resilience to Single-Source Outliers:** Highly noisy scores in one engine cannot corrupt the fused ranking because each modality contributes at most $\frac{w_m}{k + 1}$.

---

## 3. Core Components Implemented

### 1. Hybrid Retrieval & RRF Module (`backend/retrieval/hybrid.py`)
- **`reciprocal_rank_fusion(ranked_lists, k=60, weights=None, top_k=None)`**:
  - Deduplicates items across vector, keyword, and graph candidate lists.
  - Computes exact RRF scores with configurable smoothing $k$ and modality weights $w_m$.
  - Attaches rich fusion provenance metadata: `rrf_score`, `rrf_rank`, `modalities_matched`, and `ranks_per_modality`.
- **`HybridRetriever`**:
  - Unified multi-modal orchestrator coordinating `SemanticRetriever`, `KeywordRetriever`, `EntityGraphRetriever`, and `GraphRetriever`.
  - Propagates user security context (`UserSecurityContext` or dict) for database-level RBAC filtering across all underlying stores.
  - Supports modality subset selection (e.g. `modalities=['vector', 'keyword']`) and metadata filters (`source`, `resource_type`).

### 2. Agent Tool Registry Integration (`backend/agent/tools.py`)
- Registered `hybrid_search` tool definition with schema parameters (`query`, `modalities`, `source`, `resource_type`, `top_k`, `k`).
- Registered `handle_hybrid_search` execution handler binding RBAC context to `HybridRetriever.search()`.

### 3. Native LangChain Tool Integration (`backend/agent/langchain_tools.py`)
- Added Pydantic schema `HybridSearchInput`.
- Created `hybrid_tool` (`StructuredTool.from_function`) in `create_langchain_tools()`.
- Returned `hybrid_search` alongside `semantic_search`, `keyword_search`, `resource_lookup`, `graph_traversal`, and `github_entity_search`.

### 4. Agent State Machine & System Prompt Integration (`backend/agent/`)
- Updated `SYSTEM_INSTRUCTION` in `LangGraphAgentPlanner` and `AgentPlanner` positioning `hybrid_search` as the preferred general multi-modal search tool.
- Verified end-to-end execution of `hybrid_search` within the 6-node LangGraph loop (`START` $\to$ `reasoner` $\to$ `tool_node` $\to$ `reranker` $\to$ `evaluator` $\to$ `generator`).

---

## 4. Test & Verification Summary

### Unit Tests
- `backend/retrieval/tests/test_hybrid.py`: **7/7 Passed** ✅
  1. `test_01_rrf_scoring_math`: Mathematical RRF score computation.
  2. `test_02_rrf_custom_weights_and_k`: Modality weighting and parameter tuning.
  3. `test_03_rrf_metadata_and_provenance_preservation`: Modality tracking and metadata preservation.
  4. `test_04_hybrid_retriever_multi_modal_dispatch`: Multi-modal dispatch across vector and BM25.
  5. `test_05_hybrid_retriever_rbac_isolation`: Database-level RBAC filtering across all engines.
  6. `test_06_hybrid_retriever_with_entity_graph`: GitHub entity graph fusion.
  7. `test_07_hybrid_retriever_empty_and_edge_cases`: Edge case and empty query handling.
- `backend/agent/tests/test_langgraph_agent.py`: **15/15 Passed** ✅
  - Includes `test_14_native_langchain_hybrid_search_tool` and `test_15_hybrid_search_tool_in_langgraph_loop`.
- Full System Test Suite: **94/94 Passed** ✅

### End-to-End Verification Trace (`scripts/verify_phase10.py`)
```text
================================================================================
🚀 ENTERPRISE KNOWLEDGE AGENT - PHASE 10 VERIFICATION
   Hybrid Search Fusion Node & Reciprocal Rank Fusion (RRF)
================================================================================

[1/5] Verifying Reciprocal Rank Fusion (RRF) Math & Modality Fusion...
  • Total input candidates across 3 modalities: 8
  • Deduplicated fused candidates: 5
    1. [doc_checkout_flow] score=0.032522 matched=['vector', 'keyword'] ranks={'vector': 2, 'keyword': 1}
    2. [doc_payments_api] score=0.032266 matched=['vector', 'keyword'] ranks={'vector': 1, 'keyword': 3}
    3. [issue_pay_928] score=0.032258 matched=['keyword', 'graph'] ranks={'keyword': 2, 'graph': 2}
    4. [pr_142] score=0.016393 matched=['graph'] ranks={'graph': 1}
    5. [doc_auth_guide] score=0.015873 matched=['vector'] ranks={'vector': 3}
  ✅ RRF score computation and multi-modality boosting validated.

[2/5] Setting up Ingestion Pipeline & Multi-Modal HybridRetriever...
  • Retrieved 2 hybrid candidates.
    1. [jira:__issue_PAY-928#sec_0] title='PAY-928: 3DS Authentication Timeout in Checkout Flow' rrf_score=0.032787
    2. [https:__github_com_company_payments_docs_api_md#sec_0] title='Payments API Guide' rrf_score=0.016129
  ✅ Multi-modal hybrid search correctly retrieved and fused candidate chunks.

[3/5] Verifying Database-Level RBAC Pre-Filtering in Hybrid Search...
  • Guest results count: 0
  • CISO results count: 1
  ✅ Strict database-level RBAC isolation enforced across all hybrid search modalities.

[4/5] Verifying Native LangChain `hybrid_search` StructuredTool...
  • Registered LangChain tools (6): ['hybrid_search', 'semantic_search', 'keyword_search', 'resource_lookup', 'graph_traversal', 'github_entity_search']
  • Top hybrid hit: [https:__github_com_company_payments_docs_api_md#sec_0] title='Payments API Guide' score=0.032787 matched=['vector', 'keyword']
  ✅ Native LangChain hybrid_search StructuredTool verified.

[5/5] Verifying 6-Node LangGraph Agent Execution with Hybrid Search & Reranking...

  • LangGraph Execution Results:
    - Turns executed: 1
    - Tools called: ['hybrid_search']
    - Chunks retrieved: 2
    - Rerank applied: True
    - Citations: 2
    - Final Answer:
      "To resolve the 3DS checkout timeout issue (tracked in PAY-928), PR #142 by alice was merged [1]. Payment transactions must be initiated via POST /v1/payments/initiate with an idempotency key [2]."

================================================================================
🎉 ALL PHASE 10 VERIFICATIONS PASSED SUCCESSFULLY! (Exit Code 0)
================================================================================
```
