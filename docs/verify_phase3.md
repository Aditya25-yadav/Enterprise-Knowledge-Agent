# Phase 3 Verification Script Guide (`scripts/verify_phase3.py`)

This document details the architecture, execution instructions, test cases, and expected outputs of the BM25 Keyword Search verification script [`scripts/verify_phase3.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase3.py) and its test runner [`backend/storage/tests/verify_phase3.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/tests/verify_phase3.py).

---

## 1. Purpose of the Script

`scripts/verify_phase3.py` validates the exact keyword and identifier retrieval layer (Phase 3) of the **Enterprise Knowledge Agent**:

```text
[ Document / OKF Concept ]
            │
            ▼
   [ SmartOKFChunker ]
            │
      SmartChunks
            │
     ┌──────┴──────────────────────┐
     ▼                             ▼
[ Qdrant Dense Vector ]    [ BM25 Lexical Index ]
  - Natural language Q&A     - Exact Jira keys ('PAY-928')
  - Conceptual similarity    - PR numbers ('#1842')
  - Multilingual semantics   - Error codes ('HTTP 502', 'ECONNREFUSED')
                             - Code symbols ('PaymentGateway.charge()')
                             - File paths & config files
```

---

## 2. How to Run

Ensure your virtual environment is activated, then run either command:

### Option A: Via `scripts/`
```bash
source .venv/bin/activate
python3 scripts/verify_phase3.py
```

### Option B: Via `tests/`
```bash
source .venv/bin/activate
python3 backend/storage/tests/verify_phase3.py
```

> **Note**: Zero external API calls or network access are required. The tokenizer, BM25Plus model, and storage run **100% locally and in-process**.

---

## 3. What is Tested & Verified

### Section 1: Code & Identifier-Aware Tokenizer
- **Goal**: Ensure technical terms, ticket keys, symbol names, and error codes are not destroyed by standard English whitespace/punctuation splitting.
- **Test cases**:
  - `PAY-928: Fix 3DS timeout in AuthService.validate_token()` $\rightarrow$ extracts both full compound tokens (`pay-928`, `authservice.validate_token`) and atomic sub-tokens (`pay`, `928`, `authservice`, `validate`, `token`).
  - `PR #1842: Refactor refund_batch_processor to handle HTTP 401 & ECONNREFUSED` $\rightarrow$ preserves `#1842`, `1842`, `refund_batch_processor`, `refund`, `batch`, `401`, `econnrefused`.
  - Config paths `/etc/nginx/conf.d/payments.conf` $\rightarrow$ preserves `payments.conf`, `conf.d`, `nginx`, `payments`.

---

### Section 2: BM25Plus Exact Identifier & Symbol Search
- **Goal**: High-precision lexical matching for queries that vector embeddings frequently struggle with (exact numbers, function signatures, and error codes).
- **Queries tested**:
  - `PAY-928` $\rightarrow$ exact 100% match on Jira issue.
  - `PR #1842` $\rightarrow$ exact match on linked Pull Request.
  - `PaymentGateway.charge()` $\rightarrow$ exact code symbol match.
  - `ECONNREFUSED` & `HTTP 502 Bad Gateway` $\rightarrow$ exact runbook match.
  - `PTO Policy 2026` $\rightarrow$ exact HR document match.

---

### Section 3: Strict RBAC Pre-Filtering on Keyword Search
- **Goal**: Enforce database-level role boundaries so unauthorized roles cannot discover confidential keywords or tokens.
- **Tests**:
  1. **Security Admin Query**: Role `["security-admin"]` searches for `"CVE-2026-1123 SECRET-SSH-KEY-99"` $\rightarrow$ **Successfully retrieved**.
  2. **Guest Query (Unauthorized)**: Role `["guest"]` searches for the exact same query $\rightarrow$ **Zero results returned**. Restricted items are 100% blocked before returning candidates.
  3. **Guest Query (Public Doc)**: Role `["guest"]` searches for `"Engineering Onboarding"` $\rightarrow$ **Successfully retrieved**.

---

### Section 4: Disk Serialization & Instant Reload
- **Goal**: Validate `BM25Index.save_to_disk()` and `load_from_disk()`.
- **Validation**: Saves corpus to disk (`./data/verify_test_disk_index.json`), creates a fresh in-memory instance, loads the JSON payload, and verifies exact match queries continue to work with identical BM25 scores.

---

### Section 5: Dual-Indexing Ingestion Pipeline
- **Goal**: Verify that calling `IngestionPipeline.ingest_concept()` writes to **both** Qdrant (dense vectors) and BM25 (sparse lexical index) in a single unified step.
- **Validation**:
  - Ingests `Payment Refund Flow` OKF Concept.
  - Confirms Qdrant has 2 points and BM25 has 2 chunks.
  - Tests exact identifier search via BM25 (`TX-88219`).
  - Tests natural language semantic search via Qdrant (`"How do I execute a refund?"`).

---

## 4. Visual Output Sample

```text
================================================================================
  1. CODE & IDENTIFIER-AWARE TOKENIZER VERIFICATION
================================================================================

Input:  'PAY-928: Fix 3DS timeout in AuthService.validate_token()'
Tokens (11 unique extracted):
  -> ['3ds', '928', 'authservice', 'authservice.validate_token', 'fix', 'in', 'pay', 'pay-928', 'timeout', 'token', 'validate']

Input:  'PR #1842: Refactor refund_batch_processor to handle HTTP 401 & ECONNREFUSED'
Tokens (13 unique extracted):
  -> ['#1842', '1842', '401', 'batch', 'econnrefused', 'handle', 'http', 'pr', 'processor', 'refactor', 'refund', 'refund_batch_processor', 'to']

✅ Tokenizer correctly extracts and decomposes all symbols, tickets, and error codes!

================================================================================
  2. BM25PLUS EXACT IDENTIFIER & SYMBOL SEARCH
================================================================================
✅ Indexed 3 SmartChunks into BM25Plus index.

[Query] 'PAY-928' (Role: ['engineer'])
  -> Top Match: '[PAY-928] Fix 3DS timeout in PaymentGateway' (Score: 8.2470, Tokens: ['928', 'pay-928', 'pay'])

┌── 🔍 RESULT #1 ─────────────────────────────────────────────────────────────
│ Title:           [PAY-928] Fix 3DS timeout in PaymentGateway
│ BM25 Score:      8.2470
│ Matching Tokens: ['928', 'pay-928', 'pay']
│ Source:          JIRA (ticket)
│ Breadcrumbs:     Payments > Jira > PAY-928
│ Allowed Roles:   ['engineer', 'support'] (is_public=False)
├── 📄 MATCHED TEXT ─────────────────────────────────────────────────────────
│   Issue PAY-928: Investigating intermittent 3DS timeouts during POST /v1/payments/initiate. Resolved by PR #1842 in PaymentGateway.charge().
└────────────────────────────────────────────────────────────────────────────

✅ All exact identifier searches achieved 100% precision!

================================================================================
  3. STRICT RBAC ACCESS CONTROL PRE-FILTERING
================================================================================

[RBAC Test A] Security Admin searches for 'CVE-2026-1123 SECRET-SSH-KEY-99'
  -> ✅ PASSED! Security admin retrieved: '[CONFIDENTIAL] CVE-2026-1123 Security Patch' (Score: 19.0361)

[RBAC Test B] Guest searches for exact 'CVE-2026-1123 SECRET-SSH-KEY-99'
  -> ✅ PASSED! Zero matches returned. Confidential chunk is 100% blocked at the index level.

================================================================================
  ALL PHASE 3 BM25 KEYWORD SEARCH VERIFICATIONS PASSED! 🎉
================================================================================
```
