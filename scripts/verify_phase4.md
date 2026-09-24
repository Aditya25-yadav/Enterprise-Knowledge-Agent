# Phase 4 Verification Script Guide (`scripts/verify_phase4.py`)

This document details the architecture, execution instructions, test cases, and expected outputs of the Milestone 1 (Phase 4) verification script [`scripts/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase4.py) and its test runner [`backend/agent/tests/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/tests/verify_phase4.py).

---

## 1. Purpose of the Script

`scripts/verify_phase4.py` validates the **Autonomous Agent Reasoning Loop & Retrieval Tool Calling Architecture** (Milestone 1):

```text
                  User Inquiry
                       │
                       ▼
             ┌───────────────────┐
             │   AgentPlanner    │
             │ (LLM Reasoner)    │
             └─────────┬─────────┘
                       │ Turn 1: Tool Call
                       ▼
             ┌───────────────────┐
             │   ToolRegistry    │ ◄── User Context (roles, user_id)
             │ 'semantic_search' │
             └─────────┬─────────┘
                       │
                       ▼
             ┌───────────────────┐
             │ SemanticRetriever │
             │ (Qdrant + Qwen)   │
             └─────────┬─────────┘
                       │ Evidence Chunks
                       ▼
             ┌───────────────────┐
             │  ContextBuilder   │ -> Numbered citations [1], [2]
             └─────────┬─────────┘
                       │ Turn 2: Synthesize Answer
                       ▼
             ┌───────────────────┐
             │  AnswerGenerator  │
             │ (Fact Grounding)  │
             └─────────┬─────────┘
                       │
                       ▼
       Grounded Final Answer + Citation Cards
```

---

## 2. How to Run

Ensure your virtual environment is activated, then run either command:

### Option A: Via `scripts/`
```bash
source .venv/bin/activate
python3 scripts/verify_phase4.py
```

### Option B: Via `tests/`
```bash
source .venv/bin/activate
python3 backend/agent/tests/verify_phase4.py
```

> **Note**: Runs **100% locally and offline**. No external API keys or network connection required.

---

## 3. What is Tested & Verified

### Section 1: Semantic Vector Retriever & RBAC Pre-Filtering
- **Goal**: Validate dense vector search over Qdrant using `LocalEmbedder` (`Qwen/Qwen3-Embedding-0.6B`) with strict role-based access filtering.
- **Tests**:
  1. **Authorized Query**: Role `['engineer']` searches for *"How do I start a payment?"* $\rightarrow$ **Successfully retrieves** the Payment Gateway API Guide with section breadcrumbs and source URL.
  2. **Unauthorized Query**: Role `['guest', 'intern']` searches for *"Where is the production master password?"* $\rightarrow$ **Zero confidential results returned**. Restricted Vault secrets are physically excluded at the vector database index layer.
  3. **Security Lead Query**: Role `['security-lead']` searches for the same query $\rightarrow$ **Successfully retrieves** the restricted Vault credentials.

---

### Section 2: Evidence Context Builder & Citation Cards
- **Goal**: Format retrieved knowledge chunks into numbered evidence blocks `[1]`, `[2]` for LLM prompts and extract structured citation cards for UI rendering.
- **Validation**:
  - Breadcrumb hierarchy (`Payments API Guide > Transaction Lifecycle > Step 1: Payment Initiation`).
  - Procedure step markers (`[Step 1/3]`).
  - Clean URL and source platform badges (`GITHUB`, `NOTION`).

---

### Section 3: ToolRegistry Dynamic Execution
- **Goal**: Validate that tool definitions are correctly serialized into standard JSON Schema for LLMs, and that tool execution dynamically enforces user identity (`roles`, `user_id`, `groups`).
- **Validation**:
  - `registry.get_definitions()` returns valid JSON Schema definitions for `semantic_search`.
  - `registry.execute("semantic_search", arguments, user_context)` runs the underlying retriever and returns chunks.

---

### Section 4: Grounded Answer Generation
- **Goal**: Ensure the LLM system prompt enforces fact-grounding, prevents hallucinations, and binds factual statements to bracketed citation markers `[1]`, `[2]`.
- **Validation**:
  - Synthesizes answers strictly using evidence.
  - Formats output with bracketed references.

---

### Section 5: AgentPlanner Autonomous Multi-Turn Loop
- **Goal**: Verify the end-to-end multi-turn autonomous reasoning loop:
  1. **Turn 1**: LLM planner receives user query, evaluates available tools, and returns a `ToolCall(tool_name="semantic_search", arguments={...})`.
  2. **Tool Execution**: Planner executes the tool via `ToolRegistry` with user security context and appends a `MessageRole.TOOL_RESULT` message to the history.
  3. **Turn 2**: LLM processes the tool result and formulates the final grounded answer.
  4. **Output Packaging**: Planner returns structured `AgentResult` containing answer text, audit logs, citations, and chunks.

---

### Section 6: LangGraph State Machine Orchestration (`LangGraphAgentPlanner`)
- **Goal**: Validate that the entire agent workflow can execute as a stateful, compiled **LangGraph `StateGraph`**:
  - **Nodes**: `reasoner`, `tool_node`, `generator`.
  - **Conditional Routing**: `should_continue` conditional edge checking for `tool_calls` vs direct completion.
  - **State Reducer**: Uses `AgentState` with `add_messages` reducer to accumulate conversation turns and evidence.
  - **RBAC Enforcement**: Seamlessly passes `state["user_context"]` into tool execution.

---

## 4. Visual Output Sample

```text
================================================================================
  1. SEMANTIC RETRIEVER & RBAC PRE-FILTERING
================================================================================

[Query 1] 'How do I start a payment?' (Role: ['engineer'])
  -> ✅ Top Result (Score: 0.6794): 'Payment Gateway API Reference'
     Breadcrumbs: Payments API Guide > Transaction Lifecycle > Step 1: Payment Initiation
     Source URL:  https://github.com/enterprise/payments/docs/api.md

[Query 2] 'Where is the production master password?' (Role: ['intern', 'guest'])
  -> ✅ PASSED! Vault secret was 100% pre-filtered out at the database layer.

[Query 3] 'Where is the production master password?' (Role: ['security-lead'])
  -> ✅ PASSED! Security lead retrieved: 'Production Master Secrets'

================================================================================
  2. CONTEXT BUILDER & CITATION FORMATTING
================================================================================
Formulated Grounding Evidence Context:
  [1] [HTTPS] Payment Gateway API Reference > Payments API Guide > Transaction Lifecycle > Step 1: Payment Initiation (https://github.com/enterprise/payments/docs/api.md) [Step 1/3]
  ### Step 1: Payment Initiation
  To initiate a transaction, send a POST request to `/v1/payments/initiate` with `amount`, `currency`, and `customer_id`. The gateway returns a `client_secret` token for 3DS verification.
  
  [2] [HTTPS] Payment Gateway API Reference > Payments API Guide > Transaction Lifecycle > Step 2: 3D-Secure Customer Verification (https://github.com/enterprise/payments/docs/api.md) [Step 2/3]
  ### Step 2: 3D-Secure Customer Verification
  Redirect customer to issuing bank verification challenge. Once approved, biometric confirmation token is dispatched.

Extracted Citation Cards for UI Attribution:
  📌 [1] Payment Gateway API Reference (HTTPS) -> https://github.com/enterprise/payments/docs/api.md
  📌 [2] Payment Gateway API Reference (HTTPS) -> https://github.com/enterprise/payments/docs/api.md

✅ ContextBuilder successfully builds bracketed references and structured citations!

================================================================================
  5. AUTONOMOUS AGENT REASONING LOOP (AgentPlanner)
================================================================================
User Query: 'What is the procedure to initiate a customer payment transaction in the API?'
User Security Context: roles=['engineer'], user_id='dev@enterprise.com'

─── Running Agent Autonomous Loop ─────────────────────────────

✅ Execution Finished in 2 Turns (Provider: DeterministicAgentMock)

📋 Tool Execution Audit Log:
  Turn 1: Invoked tool 'semantic_search' with args {'query': 'how to initiate payment transaction API', 'top_k': 3}
          -> Retrieved 3 evidence chunks

🤖 Final Grounded Agent Answer:
  Based on the enterprise architecture documentation [1], initiating a payment transaction requires sending a POST request to `/v1/payments/initiate` with `amount`, `currency`, and `customer_id`. The server returns a `client_secret` token which the client passes to the 3D-Secure authentication portal [1].

📚 Grounded Citations:
  [1] Payment Gateway API Reference (HTTPS)
      URL:  https://github.com/enterprise/payments/docs/api.md

✅ Autonomous reasoning loop completed with 100% precision and full provenance attribution!

================================================================================
  ALL PHASE 4 (MILESTONE 1) AGENT VERIFICATIONS PASSED! 🎉
================================================================================
```
