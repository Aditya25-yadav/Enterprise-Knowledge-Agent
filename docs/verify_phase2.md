# Phase 0–2 Verification Script Guide (`scripts/verify_phase2.py`)

This document details the architecture, execution instructions, test cases, and expected outputs of the all-in-one verification script [`scripts/verify_phase2.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase2.py).

---

## 1. Purpose of the Script

`scripts/verify_phase2.py` validates the foundational layers of the **Enterprise Knowledge Agent** before building the autonomous Agent reasoning loop:

```text
[ Phase 0 ] LLMProvider Abstraction (Gemini & Ollama Base Types)
     │
     ▼
[ Phase 1 ] Structure-Aware Chunker (SmartChunk, Breadcrumbs, Procedures, Tables, Sibling Links)
     │
     ▼
[ Phase 2 ] Local Qwen Embeddings (1024-dim) + Qdrant Vector Storage + Strict RBAC Pre-Filtering
```

---

## 2. How to Run

Ensure your virtual environment is activated, then execute:

```bash
source .venv/bin/activate
python3 scripts/verify_phase2.py
```

> **Note**: No API keys are required to run this script. The embedding model (`Qwen/Qwen3-Embedding-0.6B`) runs **100% locally in-process**, and Qdrant runs in local memory.

---

## 3. Detailed Phase-by-Phase Breakdown

### Phase 0: LLM Provider Abstraction
- **What is verified**:
  - `Message`, `MessageRole`, `ToolDefinition`, `ToolCall`, and `LLMResponse` classes.
  - `get_llm_provider()` factory pattern.
  - Ensures the agent reasoning layer is decoupled from underlying LLM vendors (supports both Google Gemini 2.0 Flash and local Ollama without code changes).

---

### Phase 1: Structure-Aware & Hierarchical Chunker
- **Input Document**: Multi-level hierarchical markdown document containing:
  - Top-level headings (`# Payment Service Architecture`)
  - Subsections (`## Checkout Workflow`)
  - Multi-step procedure headers (`### Step 1: Initialize Payment Intent`, `### Step 2: Customer Authorization`, `### Step 3: Capture and Settlement`)
  - Markdown Tables (`| Currency | Code | Min Amount | Max Daily |`)
- **What is verified**:
  1. **Breadcrumb Paths (`section_path`)**: Each chunk tracks its hierarchical trail (e.g. `Payment Service Architecture > Checkout Workflow > Step 1: Initialize Payment Intent`).
  2. **Procedure Extraction (`SequenceInfo`)**: Automatically detects multi-step workflows, populating `step=1`, `total_steps=3`, and `step_title`.
  3. **Content Classification (`ContentType`)**: Classifies chunks as `DOCUMENT_SECTION`, `PROCEDURE_STEP`, `TABLE_RECORD`, `CONVERSATION_THREAD`, or `CODE_SYMBOL`.
  4. **Bidirectional Sibling Linkage**: Stitches `prev_chunk_id` and `next_chunk_id` across all chunks for bi-directional context expansion at query time.
  5. **Empty Header Elimination**: Skips empty parent headers that have no standalone body text to prevent useless micro-chunks.

---

### Phase 2: Local Qwen Embeddings + Qdrant + RBAC Pre-Filtering
- **Model Used**: `Qwen/Qwen3-Embedding-0.6B`
  - **Dimension**: 1024 dimensions (normalized dense vectors).
  - **Local Cache**: `~/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B/`
  - **Context-Enriched Embedding**: Vectors encode Document Title, Section Breadcrumbs, Step Number, and Content.
- **RBAC Security Tests Verified**:
  1. **Test A (Authorized Access)**: A user with role `['devops']` searches for *"production database password"* $\rightarrow$ **Successfully retrieves** the confidential HashiCorp Vault credentials chunk.
  2. **Test B (Unauthorized Access / Pre-Filter)**: A user with role `['guest', 'intern']` searches for the exact same query $\rightarrow$ **Zero confidential results returned**. The restricted chunk is physically excluded at the database index level before similarity scoring.
  3. **Test C (Public Access)**: A guest user searches for company remote work policy $\rightarrow$ **Successfully retrieves** the public HR document.

---

## 4. Visual Output Sample

When executed, the script prints visual inspection boxes for every chunk:

```text
================================================================================
  PHASE 1: Structure-Aware & Hierarchical Chunker Verification
================================================================================
✅ Successfully chunked document into 5 rich SmartChunks.
   Here is the complete inspection of all generated chunks:

┌── 📦 CHUNK #0 ─────────────────────────────────────────────────────────────
│ ID:            https:__github_com_company_payments_docs_arch_md#sec_0
│ Content Type:  DOCUMENT_SECTION
│ Source:        GITHUB (architecture)
│ Breadcrumbs:   Payment Service Architecture
│ Sibling Links: [Prev: None] <───> [Next: ...#step_1]
│ RBAC Roles:    ['engineer', 'finance'] (is_public=False)
├── 📄 CHUNK TEXT CONTENT ───────────────────────────────────────────────────
│   # Payment Service Architecture
│   
│   Overview of the payment gateway integration and security parameters.
└────────────────────────────────────────────────────────────────────────────

┌── 📦 CHUNK #1 ─────────────────────────────────────────────────────────────
│ ID:            https:__github_com_company_payments_docs_arch_md#step_1
│ Content Type:  PROCEDURE_STEP
│ Source:        GITHUB (architecture)
│ Breadcrumbs:   Payment Service Architecture > Checkout Workflow > Step 1: Initialize Payment Intent
│ Procedure:     Step 1 of 3 (Step 1: Initialize Payment Intent)
│ Sibling Links: [Prev: ...#sec_0] <───> [Next: ...#step_2]
│ RBAC Roles:    ['engineer', 'finance'] (is_public=False)
├── 📄 CHUNK TEXT CONTENT ───────────────────────────────────────────────────
│   ### Step 1: Initialize Payment Intent
│   Call POST /v1/payments/initiate with amount, currency, and customer_id. The server returns a client_secret token.
└────────────────────────────────────────────────────────────────────────────

┌── 📦 CHUNK #4 ─────────────────────────────────────────────────────────────
│ ID:            https:__github_com_company_payments_docs_arch_md#tbl_5
│ Content Type:  TABLE_RECORD
│ Source:        GITHUB (architecture)
│ Breadcrumbs:   Payment Service Architecture > Supported Currencies Table
│ Sibling Links: [Prev: ...#step_3] <───> [Next: None]
│ RBAC Roles:    ['engineer', 'finance'] (is_public=False)
├── 📄 CHUNK TEXT CONTENT ───────────────────────────────────────────────────
│   ## Supported Currencies Table
│   
│   | Currency | Code | Min Amount | Max Daily |
│   |---|---|---|---|
│   | US Dollar | USD | $0.50 | $50,000 |
│   | Euro | EUR | €0.50 | €50,000 |
│   | British Pound | GBP | £0.50 | £40,000 |
└────────────────────────────────────────────────────────────────────────────

✅ All structural, sequence, and sibling assertions passed!

================================================================================
  PHASE 2: Local Qwen Embeddings + Qdrant + RBAC Verification
================================================================================
✅ Local Embedding Model:  Qwen/Qwen3-Embedding-0.6B
✅ Local Disk Cache Path:  /Users/ompatil/.cache/huggingface/hub/models--Qwen--Qwen3-Embedding-0.6B
✅ Output Vector Size:     1024 dimensions (dense normalized)
✅ Status:                 100% Local & In-Process (No external API calls)
✅ Ingested 2 test documents (2 vectors) into Qdrant.

[RBAC Test A] Querying: 'Where is the production database password?' as Role: ['devops']
  -> ✅ PASSED! Top Result (Score: 0.7042):
     Title:   Database Vault Credentials
     Content: The production PostgreSQL root password is stored in HashiCorp Vault at path secret/data/prod-db.
     Allowed: ['security-lead', 'devops']

[RBAC Test B] Querying: 'Where is the production database password?' as Role: ['guest', 'intern']
  -> ✅ PASSED! Confidential Vault document is strictly filtered out at index level.
     (Note: Only public documents like 'Remote Work Policy' were visible to guest)

[RBAC Test C] Querying: 'How many remote work days do we get?' as Role: ['guest']
  -> ✅ PASSED! Top Result (Score: 0.6773):
     Title:   Remote Work Policy
     Content: All employees are eligible for hybrid remote work up to 3 days per week.

================================================================================
  ALL PHASE 0, 1, & 2 CHECKS PASSED WITH FLYING COLORS! 🎉
================================================================================
```
