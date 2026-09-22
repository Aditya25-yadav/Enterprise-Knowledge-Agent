# Phase 7: Evidence Evaluator & Self-RAG Reflection Verification

**Date:** 2026-09-22  
**Status:** ✅ Fully Implemented, Integrated, and Verified  
**Component:** `backend.evaluation.evaluator.EvidenceEvaluator`, `backend.agent.reformulator.QueryReformulator`, `backend.agent.langgraph_planner.LangGraphAgentPlanner`  

---

## 1. Overview & Architectural Transformation

Phase 7 evolves the Enterprise Knowledge Agent from a linear **"retrieve → generate"** model into an iterative, quality-controlled **Self-RAG reflection loop**:

$$\text{Retrieve} \longrightarrow \text{Inspect / Evaluate} \longrightarrow \text{Decide} \longrightarrow \begin{cases} \text{Sufficient} \longrightarrow \text{Generate Answer} \\ \text{Insufficient} \longrightarrow \text{Reformulate} \longrightarrow \text{Retrieve Again} \end{cases}$$

### Key Components

```
                   ┌─────────────┐
                   │    START    │
                   └──────┬──────┘
                          │
                          ▼
                   ┌─────────────┐
        ┌─────────►│   reasoner  │
        │          └──────┬──────┘
        │                 │
        │    [ _reasoner_routing ]
        │     /                 \
  (tool calls)             (direct answer)
      /                             \
     ▼                               ▼
┌─────────────┐                ┌─────────────┐
│  tool_node  │                │  generator  │
└──────┬──────┘                └──────┬──────┘
       │                              │
       ▼                              │
┌─────────────┐                       │
│  evaluator  │                       │
└──────┬──────┘                       │
       │                              │
[ _evaluator_routing ]                │
 /                   \                │
(insufficient)    (sufficient)        │
 /                     \              │
▼                       ▼             │
┌──────────────┐         └────────────┘
│ reformulator │                      │
└──────┬───────┘                      ▼
       │                          ┌───────┐
       └─────────────────────────►│  END  │
                                  └───────┘
```

1. **`EvidenceEvaluator` (`backend/evaluation/evaluator.py`):**
   - **Relevance Scoring:** Computes relevance scores ($0.0 \dots 1.0$) for every retrieved chunk against the user query.
   - **Sufficiency Analysis:** Determines whether all facts, relations, and context needed to answer the query are present without hallucination.
   - **Knowledge Gap Detection:** Identifies exact missing facts, entities, and relationships.
   - **Recommended Action & Tool Routing:** Emits `GENERATE`, `RETRIEVE_MORE`, or `REFORMULATE` along with the optimal next retrieval tool.

2. **`QueryReformulator` (`backend/agent/reformulator.py`):**
   - Synthesizes high-precision sub-queries specifically targeting the knowledge gaps identified by `EvidenceEvaluator`.
   - Injects structured reflection guidance into the agent's conversational state for the next reasoning turn.

3. **`LangGraphAgentPlanner` (`backend/agent/langgraph_planner.py`):**
   - 5-Node state machine (`reasoner`, `tool_node`, `evaluator`, `reformulator`, `generator`).
   - Self-RAG reflection routing with guardrails (`max_retrieval_attempts`, `max_turns`) preventing infinite loops.

---

## 2. Evaluation Data Models & State Schema

### `EvaluationResult` (`backend/models/evaluation.py`)
```python
class RecommendedAction(str, Enum):
    GENERATE = "GENERATE"
    RETRIEVE_MORE = "RETRIEVE_MORE"
    REFORMULATE = "REFORMULATE"

@dataclass
class ChunkRelevance:
    chunk_id: str
    score: float
    is_relevant: bool
    reason: str

@dataclass
class EvaluationResult:
    relevance_score: float
    evidence_sufficient: bool
    missing_information: List[str]
    unsupported_claims: List[str]
    recommended_action: str
    recommended_tool: Optional[str]
    chunk_evaluations: List[ChunkRelevance]
    reasoning: str
```

### Self-RAG `AgentState` (`backend/agent/state.py`)
```python
class AgentState(TypedDict):
    query: str
    current_query: str
    user_context: Dict[str, Any]
    messages: Annotated[List[BaseMessage], add_messages]
    retrieved_chunks: Annotated[List[Dict[str, Any]], ...]
    citations: List[Dict[str, Any]]
    answer: str
    evaluation: Optional[Dict[str, Any]]
    missing_information: List[str]
    retrieval_attempts: int
    reformulated_queries: List[str]
    turn_count: int
    error: Optional[str]
```

---

## 3. Test & Verification Summary

### Unit Tests
- `backend/evaluation/tests/test_evaluator.py`: **6/6 Passed** (Relevance assessment, sufficiency, gap detection, JSON parsing, heuristic fallbacks)
- `backend/agent/tests/test_reformulator.py`: **3/3 Passed** (Sub-query generation, gap targeting, fallback synthesis)
- `backend/agent/tests/test_langgraph_agent.py`: **12/12 Passed** (Compilation, single-turn sufficient, multi-tool, multi-hop reflection, fallback guardrails)
- `backend/retrieval/tests/test_entity_graph.py`: **13/13 Passed** (Native Cypher dispatch & in-memory graph)

### End-to-End Verification Trace (`scripts/verify_phase7.py`)
```text
================================================================================
  PHASE 7 VERIFICATION: EVIDENCE EVALUATOR & SELF-RAG REFLECTION
================================================================================

[Step 1/5] Ingesting Multi-Modal Knowledge Topology (Dual-Index + Entity Graph)...
  ✓ Ingested dual-index OKF concepts and initialized GitHub entity graph topology.
  ✓ Initialized ToolRegistry with all 5 enterprise retrieval tools.

[Step 2/5] Testing Standalone EvidenceEvaluator (Relevance & Sufficiency)...
  ✓ Incomplete Evidence Correctly Evaluated:
    - Relevance Score: 0.75
    - Evidence Sufficient: False
    - Missing Information Gaps: ['Reviewer approval status for PR #142', 'Specific backend files modified by the fix']
    - Recommended Action: REFORMULATE | Tool: github_entity_search

[Step 3/5] Testing Standalone QueryReformulator (Targeted Sub-Queries)...
  ✓ Query Reformulator Synthesized Targeted Sub-Query:
    - Reformulated Query: 'get PR #142 details'
    - Suggested Tool: 'github_entity_search'
    - Reasoning: Directly query the GitHub entity graph to obtain author, reviewer approval, and modified files.

[Step 4/5] Executing End-to-End LangGraph Self-RAG Reflection Workflow...
  User Query: "Who approved the checkout timeout fix (PAY-928), and what code file was changed?"
  Orchestrating autonomous LangGraph workflow with Quality-Control Reflection...

  ── LangGraph Self-RAG Execution Trace ──
  Retrieval Attempts (Reflection Cycles): 2
  Reformulated Queries Log: ['get PR #142 details']
  Total Tool Calls Executed (2):
    1. Tool: `semantic_search` | Arguments: {'query': 'checkout timeout bug fix'}
    2. Tool: `github_entity_search` | Arguments: {'operation': 'get_pr_details', 'target': '#142'}

  Final Evidence Evaluation Status:
    - Sufficient: True
    - Recommended Action: GENERATE
    - Score: 1.0
    - Missing Information: []

  Synthesized Grounded Answer:
  The checkout timeout issue (PAY-928) [1] was resolved by PR #142 'Fix 3DS timeout in Checkout Flow'. The PR was authored by **alice** and reviewed/approved by **bob** [2]. The modified file in this fix was `backend/services/checkout.py` [2].

  Evidence Citations (2):
    [1] PAY-928: 3DS timeout in Checkout Flow (JIRA)
    [2] Fix 3DS timeout in Checkout Flow (GITHUB)

[Step 5/5] Verifying Reflection Guardrails & Fallback Robustness...
  ✓ Heuristic fallback correctly handled malformed text output -> RETRIEVE_MORE.

================================================================================
  🎉 PHASE 7 VERIFICATION COMPLETED SUCCESSFULLY WITH ZERO ERRORS!
================================================================================
```

---

## 4. Next Roadmap Phases

- **Phase 8:** Database-Level RBAC Resolver (Fine-grained role, team, and permission resolution).
- **Phase 9:** Local Cross-Encoder Reranker (Semantic precision re-ordering prior to evaluator).
- **Phase 10:** Hybrid Search Fusion Node (Reciprocal Rank Fusion inside LangGraph).
