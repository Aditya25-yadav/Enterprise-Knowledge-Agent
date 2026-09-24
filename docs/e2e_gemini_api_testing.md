# End-to-End Enterprise Knowledge Agent Testing Guide (Google Gemini API)

> **Document:** `docs/e2e_gemini_api_testing.md`  
> **Status:** Production Testing & Operator Guide  
> **Target Audience:** Developers, SREs, and Enterprise Administrators  
> **Last Updated:** 2026-09-24  

---

## 1. Overview & Architecture

This guide walks through testing the **complete, end-to-end Enterprise Knowledge Agent pipeline** using Google's **Gemini API** (`gemini-2.5-flash`, `gemini-2.5-pro`, or `gemini-2.0-flash`) via the official `google-genai` SDK.

In this architecture:
- **Reasoning & Planning (Cloud):** Google Gemini handles tool selection, complex multi-turn reasoning, Self-RAG reflection evaluation, and cited answer synthesis.
- **Storage & Security (100% Local):** Document chunking (`SmartOKFChunker`), 768-dim dense embeddings (`LocalEmbedder`), vector store (`QdrantVectorStore`), BM25+ inverted index, and Developer Property Graph (`InMemoryEntityGraph` / Neo4j) remain completely local.
- **Data Privacy & RBAC:** Database-level RBAC pre-filters candidate chunks *before* context is sent to the LLM, ensuring strict security boundary compliance.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        HYBRID CLOUD / ON-PREMISE ARCHITECTURE                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                            │
  ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
  │                                         │                                         │
  ▼                                         ▼                                         ▼
GitHub / Jira / Notion             Confluence / Dropbox / Gmail             Developer Graph
(Enterprise Connectors)            (Enterprise Connectors)                  (PRs, Commits, Users)
  │                                         │                                         │
  └─────────────────────────────────────────┼─────────────────────────────────────────┘
                                            │
                                            ▼
                    SmartOKFChunker (Preserves headings & hierarchy)
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
          Local Dense Embeddings                           Sparse BM25+ Index
          (BAAI/bge-base-en-v1.5)                        (Exact IDs & Tokens)
                    │                                               │
                    └───────────────────────┬───────────────────────┘
                                            │
                                            ▼
                         HybridRetriever & Reciprocal Rank Fusion
                                            │
                                            ▼
                           Local Cross-Encoder Reranker Node
                                            │
                                            ▼
                       Google Gemini API (gemini-2.5-flash / pro)
                                            │
                                            ▼
                       Cited Answer [1], [2] + RBAC Protection
```

---

## 2. Prerequisites & API Key Setup

### 1. Obtain a Gemini API Key

1. Navigate to [Google AI Studio](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **"Get API key"** $\to$ **"Create API key"**.
4. Copy your generated API key (starts with `AIzaSy...`).

### 2. Set Up Environment Variables

You can export the API key in your terminal session or add it to your project's `.env` file:

#### Option A: Direct Terminal Export
```bash
export LLM_PROVIDER=gemini
export GEMINI_API_KEY="AIzaSyYourSecretGeminiKeyHere"
export GEMINI_MODEL="gemini-2.5-flash"   # Recommended: gemini-2.5-flash or gemini-2.5-pro
```

#### Option B: In `.env` Configuration
Create or edit `.env` in the repository root:
```bash
# LLM Configuration
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIzaSyYourSecretGeminiKeyHere
GEMINI_MODEL=gemini-2.5-flash

# Optional: Connector API Keys (if testing live connectors)
GITHUB_TOKEN=ghp_your_token
JIRA_URL=https://company.atlassian.net
JIRA_API_TOKEN=your_jira_token
NOTION_API_KEY=secret_notion_key
DROPBOX_ACCESS_TOKEN=sl.your_dropbox_token

# Optional: Knowledge Graph (Neo4j) — Falls back to in-memory graph if omitted
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=secret_password_123
NEO4J_DATABASE=neo4j
```

---

### Step-by-Step Neo4j Setup Options:

The Knowledge Graph supports two execution modes:

#### Option 1: In-Memory Graph (Default — 0 Credentials Needed)
- If `NEO4J_PASSWORD` is omitted in `.env` (or not passed via CLI), the runner automatically uses [`InMemoryEntityGraph`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/retrieval/entity_graph.py#L50-L100).
- **No Docker, no database installation, and no passwords required.**

#### Option 2: Local Neo4j via Docker
Run a lightweight Neo4j container locally:
```bash
docker run -d \
  --name neo4j-knowledge \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/secret_password_123 \
  neo4j:5-community
```
Then add to `.env`:
```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=secret_password_123
```

#### Option 3: Free Cloud Instance (Neo4j AuraDB)
1. Create a free account at [console.neo4j.io](https://console.neo4j.io/).
2. Click **"New Instance"** $\to$ choose **AuraDB Free**.
3. Download the credentials file and copy the **Connection URI** and **Password** into `.env`:
   ```bash
   NEO4J_URI=neo4j+s://your-instance-id.databases.neo4j.io
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=your_saved_aura_password
   ```

---

## 3. Step 1: Install Dependencies

Ensure you have activated the virtual environment and installed the `google-genai` SDK:

```bash
# Navigate to repository root
cd /path/to/Enterprise-Knowledge-Agent

# Activate virtualenv
source .venv/bin/activate

# Install google-genai and pipeline dependencies if not already installed
pip install google-genai sentence-transformers qdrant-client rank-bm25 langgraph langchain-core
```

---

## 4. Step 2: Run the Automated Live End-to-End Test Suite

Run the joint execution script passing `--provider gemini`:

```bash
# Run automated verification with Gemini (Default: Local Persistent Disk Storage)
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash

# Run with ephemeral in-memory storage (RAM-only)
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --qdrant-mode memory

# Force wipe and re-index persistent storage
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --reset-storage

# Run with live Neo4j database (instead of default in-memory graph)
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --neo4j-password secret_password_123
```

### Storage & Graph Persistence Options:
| CLI Flag | Default | Description |
| :--- | :--- | :--- |
| `--qdrant-mode` | `local` | `local` (disk persistence), `memory` (RAM-only), `server` (Docker/remote). |
| `--qdrant-path` | `./data/qdrant_storage` | Local directory where Qdrant vectors and SQLite metadata are stored. |
| `--bm25-path` | `./data/live_bm25_index.json` | Path where the BM25+ inverted index is persisted. |
| `--reset-storage` | `False` | Wipes existing vectors and BM25 index to re-ingest corpus freshly. |
| `--neo4j-uri` | `bolt://localhost:7687` | Neo4j Bolt connection URI (or AuraDB `neo4j+s://...`). |
| `--neo4j-password` | `None` | Neo4j password. If omitted, seamlessly uses `InMemoryEntityGraph`. |
| `--neo4j-user` | `neo4j` | Neo4j username. |
| `--neo4j-database` | `neo4j` | Neo4j target database. |

> 💡 **Persistent Indexing Benefit:** When using `mode="local"` (default), all vectors and payloads persist on disk. After the first run, future executions skip the re-embedding phase and start querying immediately!
> 
> 🕸️ **Dual Graph Mode:** If you do not pass `--neo4j-password` or set `NEO4J_PASSWORD` in `.env`, the pipeline runs with 0 dependencies using the built-in `InMemoryEntityGraph`. If a password is provided, it automatically connects, creates constraints, and synchronizes entities to live Neo4j.

### What Happens During Execution:

1. **[1/4] Storage Setup:** Local Qdrant vector engine initializes (`mode="local"`) and loads the BM25+ inverted index.
2. **[2/4] Document Ingestion:** Multi-modal enterprise documents across all 6 connectors are normalized into OKF v0.2 concept bundles and chunked with structure preservation (or reuses existing vectors on disk).
3. **[3/4] Property Graph Ingestion:** Relationship graph connecting developer profiles, PRs, review approvals, and touched code files is constructed.
4. **[4/4] Gemini Connection:** Instantiates `GeminiProvider` using the official `google-genai` SDK with native JSON schema function calling.
5. **Execution of 5 Live Verification Test Cases:**
   - **Case 1 (Incident Trace):** Gemini invokes `hybrid_search` to correlate Jira bug `PAY-928`, GitHub PR `#142` (by `alice`), and post-mortem communications.
   - **Case 2 (Payment Architecture):** Gemini searches for payment initiation guidelines, extracting `/v1/payments/initiate` and `Idempotency-Key` headers.
   - **Case 3 (Disaster Recovery SOP):** Gemini extracts step-by-step PostgreSQL failover instructions from Dropbox runbooks.
   - **Case 4 (RBAC Security Verification):** Verifies unauthorized `guest` users cannot retrieve or leak CISO Master Encryption keys.
   - **Case 5 (Authorized CISO Access):** Verifies `ciso_admin` successfully accesses and cites the master KMS vault ARN.

---

## 5. Step 3: Interactive Terminal Chat REPL with Gemini

To chat with the Enterprise Knowledge Agent in real-time powered by Gemini:

```bash
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --interactive
```

### Example Interactive Session:

```text
================================================================================
💬 INTERACTIVE ENTERPRISE KNOWLEDGE AGENT REPL (Gemini 2.5 Flash)
   Type your questions below. Type 'exit', 'quit', or 'role <role_name>' to switch persona.
================================================================================

[engineer] > What was the root cause of the 3DS checkout timeout and how was it mitigated?

🤖 Reasoning and retrieving multi-modal evidence across connectors...
────────────────────────────────────────────────────────────────────────────────
Answer (1.42s | 1 tool calls):
The 3DS checkout timeout was tracked in PAY-928 [1]. The root cause was an upstream
3DS gateway timeout configured to 15 seconds, while the client worker TTL was set
to 10 seconds, causing premature socket terminations with ECONNREFUSED errors.

This was resolved in PR #142 by alice in the company/payments repository [1],
which increased the socket timeout to 60 seconds and enabled a resilience circuit breaker.
────────────────────────────────────────────────────────────────────────────────
Citations:
  [1] PAY-928: 3DS Authentication Timeout in Checkout Flow (jira) -> https://jira.company.com/browse/PAY-928

[engineer] > role guest
Switched active security role to: 'guest'

[guest] > Show me the production master KMS encryption keys and secret tokens.

🤖 Reasoning and retrieving multi-modal evidence across connectors...
────────────────────────────────────────────────────────────────────────────────
Answer (0.89s | 1 tool calls):
I do not have access to production master KMS encryption keys or Vault secret tokens
under your current permissions. These resources are restricted to Security Operations
and CISO staff.
────────────────────────────────────────────────────────────────────────────────

[guest] > role ciso_admin
Switched active security role to: 'ciso_admin'

[ciso_admin] > Show me the production master KMS encryption keys.

🤖 Reasoning and retrieving multi-modal evidence across connectors...
────────────────────────────────────────────────────────────────────────────────
Answer (1.15s | 1 tool calls):
The production master KMS encryption key parameters are documented in the Vault
infrastructure guide [1]:
- Master Key ARN: `arn:aws:kms:us-east-1:998877665544:key/vault-prod-master-2026`
- Vault Cluster Secret Token: `AES-SECRET-KEY-PROD-998877`
- Rotation Policy: Automated 90-day key rotation via AWS Secrets Manager.
────────────────────────────────────────────────────────────────────────────────
Citations:
  [1] CISO Master KMS Encryption & Vault Infrastructure [TOP SECRET] (notion) -> https://notion.company.com/vault-kms-prod
```

---

## 6. Comparison: Local LLM (Ollama) vs. Cloud LLM (Gemini)

| Feature | Local LLM (`OllamaProvider`) | Google Gemini (`GeminiProvider`) |
| :--- | :--- | :--- |
| **Data Privacy** | 100% on-premises / air-gapped | Inference payload sent via TLS to Google Cloud |
| **Model Options** | `qwen2.5:7b`, `llama3.1:8b`, `mistral-nemo` | `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-2.0-flash` |
| **Hardware Requirement**| 8GB - 16GB RAM + Apple Silicon / GPU | Zero local GPU needed (runs on any laptop/VM) |
| **Speed / Latency** | ~20 - 40 tokens/sec on local hardware | High-speed cloud API (<1.5s per turn) |
| **Tool Calling Fidelity**| High on 7B+ fine-tuned models | Native multimodal function calling |
| **Switching Command** | `python scripts/run_e2e_live.py --provider ollama` | `python scripts/run_e2e_live.py --provider gemini` |

---

## 7. Troubleshooting & FAQs

### 1. `ValueError: GeminiProvider requires GEMINI_API_KEY env var or api_key argument.`
- **Cause:** `GEMINI_API_KEY` is not exported or is empty.
- **Fix:** Run `export GEMINI_API_KEY="your_actual_key"` or add `GEMINI_API_KEY=your_key` to `.env`.

### 2. `google.genai.errors.APIError: 429 Resource has been exhausted`
- **Cause:** Reached free tier rate limit (RPM/TPM).
- **Fix:** Wait a few seconds between queries, or switch to `gemini-2.5-flash` which has high rate allowances on Google AI Studio.

### 3. Missing `google-genai` package
- **Fix:**
  ```bash
  pip install google-genai
  ```

---

## 8. Verification Checklist

- [x] `GEMINI_API_KEY` configured in `.env` or exported in shell
- [x] Local Qdrant vector store and BM25+ index built successfully
- [x] Developer Property Graph indexed with PRs, reviews, and commits
- [x] `GeminiProvider` function calling configured with JSON schema definitions
- [x] Hybrid Search (RRF) combining vector, BM25, and graph modalities
- [x] Cross-Encoder Reranker refining candidate evidence
- [x] Self-RAG reflection loop evaluating evidence sufficiency
- [x] Grounded answers synthesized with verified numbered citations `[1]`, `[2]`
- [x] Database-level RBAC pre-filtering strictly protecting confidential data
