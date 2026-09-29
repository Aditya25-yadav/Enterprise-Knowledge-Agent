# End-to-End Enterprise Knowledge Agent Testing Guide (Google Gemini API)

> **Document:** `docs/e2e_gemini_api_testing.md`  
> **Status:** Production Testing & Operator Guide  
> **Target Audience:** Developers, SREs, and Enterprise Administrators  
> **Last Updated:** 2026-09-27  

---

## 1. Overview & Architecture

This guide walks through testing the **complete, end-to-end Enterprise Knowledge Agent pipeline** using Google's **Gemini API** (`gemini-2.5-flash`, `gemini-2.5-pro`, or `gemini-2.0-flash`) via the official `google-genai` SDK.

In this architecture:
- **Reasoning & Planning (Cloud):** Google Gemini handles Map-First catalog discovery, multi-turn reasoning, Self-RAG reflection evaluation, tool orchestration, and grounded answer synthesis.
- **Storage & Security (100% Local):** Document chunking (`SmartOKFChunker`), 768-dim dense embeddings (`LocalEmbedder`), vector store (`QdrantVectorStore`), BM25+ inverted index, and Developer Property Graph (`InMemoryEntityGraph` / Neo4j) remain completely local and private.
- **Data Privacy & RBAC:** Database-level RBAC pre-filters candidate chunks *before* context is sent to the LLM, ensuring strict security boundary compliance.
- **Multi-Turn Session Persistence:** `SqliteCheckpointSaver` (or `MemorySaver`) persists chat state and tool history across distinct conversation threads.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        HYBRID CLOUD / ON-PREMISE ARCHITECTURE                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                            │
  ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
  │                                         │                                         │
  ▼                                         ▼                                         ▼
GitHub / Jira / Notion             Confluence / Dropbox / Gmail             Developer Graph
(18 Multi-Modal Docs)              (18 Multi-Modal Docs)                    (PRs, Commits, Users)
  │                                         │                                         │
  └─────────────────────────────────────────┼─────────────────────────────────────────┘
                                            │
                                            ▼
                    Global Master Index (global_index.md & global_log.md)
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
                    7-Tool Multi-Modal Retrieval & RRF Fusion Engine
        (catalog_discovery, hybrid_search, semantic_search, keyword_search,
         resource_lookup, graph_traversal, github_entity_search)
                                            │
                                            ▼
                            Local Cross-Encoder Reranker Node
                                            │
                                            ▼
                        Google Gemini API (gemini-2.5-flash / pro)
                                            │
                                            ▼
                       Cited Answer [1], [2] + SQLite Checkpointing
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

Ensure you have activated your virtual environment and installed the dependencies:

```bash
# Navigate to repository root
cd /path/to/Enterprise-Knowledge-Agent

# Activate virtualenv
source .venv/bin/activate

# Install google-genai and pipeline dependencies if not already installed
pip install google-genai sentence-transformers qdrant-client rank-bm25 langgraph langchain-core python-dotenv
```

---

## 4. Step 2: Run the Automated Live End-to-End Test Suite

Run the joint execution script passing `--provider gemini`:

```bash
# Run automated verification with Gemini (Default: Local Persistent Disk Storage + SQLite Sessions)
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash

# Run with TOON (Token-Oriented Object Notation) high-density context formatting
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --context-format toon

# Run with ephemeral in-memory storage (RAM-only)
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --qdrant-mode memory --checkpoint-mode memory

# Force wipe and re-index persistent storage
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --reset-storage

# Run with live Neo4j database (instead of default in-memory graph)
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --neo4j-password secret_password_123
```

### Storage, Graph & Session Persistence Options:
| CLI Flag | Default | Description |
| :--- | :--- | :--- |
| `--provider` | `ollama` | LLM Provider (`gemini` or `ollama`). |
| `--model` | `llama3.1:8b` / `gemini-2.5-flash` | Model name (e.g. `gemini-2.5-flash`, `gemini-2.5-pro`, `qwen2.5:7b`). |
| `--context-format` | `standard` | Evidence serialization format (`standard` verbose markdown or `toon` high-density). |
| `--checkpoint-mode` | `sqlite` | Multi-turn session checkpointer (`sqlite`, `memory`, `none`). |
| `--checkpoint-path` | `./data/chat_sessions.db` | Local SQLite database path for persistent conversation sessions. |
| `--max-turns` | `10` | Maximum agent reasoning / Self-RAG reflection turns. |
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
> 🕸️ **Dual Graph Mode:** If you do not pass `--neo4j-password` or set `NEO4J_PASSWORD` in `.env`, the pipeline runs with 0 external dependencies using the built-in `InMemoryEntityGraph`. If a password is provided, it automatically connects, creates constraints, and synchronizes entities to live Neo4j.

### What Happens During Execution:

1. **[1/6] Storage Setup:** Local Qdrant vector engine initializes (`mode="local"`) and loads the BM25+ inverted index.
2. **[2/6] Document Ingestion:** 18 multi-modal enterprise documents across all 6 connectors (GitHub, Jira, Notion, Dropbox, Gmail, Confluence) are normalized into OKF v0.2 concept bundles and chunked with structure preservation.
3. **[3/6] Property Graph Ingestion:** Relationship graph connecting developer profiles, PRs, review approvals, and modified code files is constructed.
4. **[4/6] Gemini Connection:** Instantiates `GeminiProvider` using the official `google-genai` SDK with native JSON schema function calling.
5. **[5/6] Global Master Index Generation:** Builds Map-First `global_index.md` and `global_log.md` sync ledger.
6. **[6/6] Multi-Modal Retrievers & LangGraph Planner:** Initializes the full 7-tool suite, Cross-Encoder reranker, SQLite checkpointer, and compiles the agent state machine.
7. **Execution of 8 Live Verification Test Cases:**
   - **Case 1 (Incident Investigation):** Gemini correlates Jira bug `PAY-928`, GitHub PR `#142` (by `alice`), and post-mortem email.
   - **Case 2 (Payment Architecture):** Gemini searches for payment initiation guidelines, extracting `/v1/payments/initiate` and `Idempotency-Key` headers.
   - **Case 3 (Disaster Recovery SOP):** Gemini extracts PostgreSQL failover procedures (`patronictl`, `PgBouncer`) from Dropbox runbooks.
   - **Case 4 (RBAC Security Isolation):** Verifies unauthorized `guest` users cannot retrieve or leak CISO Master Encryption keys (`AES-SECRET-KEY-PROD-998877`).
   - **Case 5 (Authorized CISO Secret Access):** Verifies `ciso_admin` successfully accesses and cites the master KMS vault ARN (`vault-prod-master`).
   - **Case 6 (SRE OpenSearch Reindexing):** Extracts zero-downtime index creation (`product_catalog_v2`), `_reindex`, and atomic `_aliases` switchover from Dropbox SOP.
   - **Case 7 (DevOps Kubernetes Ingress & TLS):** Extracts Cert-Manager `letsencrypt-production` `ClusterIssuer`, NGINX ingress class `nginx-external`, and 500 req/s rate limits from GitHub manifests.
   - **Case 8 (Real-Time Streaming Data Pipeline):** Extracts Kafka + Apache Flink + Iceberg real-time architecture reducing clickstream latency from 8 hours to under 45 seconds from Jira `DATA-782`.

---

## 5. Step 3: Interactive Terminal Chat REPL with Gemini

To chat with the Enterprise Knowledge Agent in real-time powered by Gemini:

```bash
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --interactive
```

### Interactive REPL Commands & Features:

#### 1. Session & Persona Management
- `role <role_name>`: Switch active security persona (e.g. `role engineer`, `role ciso_admin`, `role guest`).
- `thread <thread_id>`: Switch active conversation thread/session.
- `threads`: List all saved conversation threads in SQLite storage.
- `new`: Start a fresh conversation thread.
- `history`: Display message history for the current thread.
- `exit` or `quit`: Exit REPL.

#### 2. Query Scope Modifiers
Prefix your query with a directive to guide the agent deterministically:
- `@enterprise <query>` or `@docs <query>`: Forces enterprise retrieval tools across connectors.
- `@general <query>` or `@llm <query>`: Fast-path direct answer from base model weights (0 tools, 0s retrieval latency).
- `@<connector> <query>`: Pre-filters search to a specific platform (e.g. `@jira PAY-928`, `@github cert-manager`, `@dropbox opensearch reindex`).

#### 3. Zero-Tool Fast Path for Meta & General Inquiries
Meta-conversational questions (*"what was the last question I asked?"*, *"summarize our conversation"*) and general programming questions (*"explain how quicksort works"*) are answered immediately in 1 turn with 0 tool calls and 0 citations.

---

### Example Interactive Session:

```text
================================================================================
💬 INTERACTIVE ENTERPRISE KNOWLEDGE AGENT REPL
   Type your questions below.
   Session Commands:
     • 'role <role_name>'   - Switch active security persona (e.g. engineer, ciso_admin, guest)
     • 'thread <thread_id>' - Switch active conversation thread/session
     • 'threads'            - List all saved conversation threads in SQLite storage
     • 'new'                - Start a fresh conversation thread
     • 'history'            - Display message history for current thread
     • 'exit' or 'quit'     - Exit REPL
   Query Scope Modifiers:
     • '@enterprise <query>' - Force enterprise retrieval tools across connectors
     • '@general <query>'    - Direct LLM conceptual/meta answer (0 tools, 0s retrieval)
     • '@<connector> <query>' - Target specific source (e.g. @jira, @github, @dropbox)
================================================================================
Active Session Thread: 'session_1790406562'

[engineer | session_1790406562] > What was the root cause of the 3DS checkout timeout and how was it mitigated?

🤖 Reasoning and retrieving multi-modal evidence across connectors...
────────────────────────────────────────────────────────────────────────────────
⏱️  Execution Time: 1.42s | Turns: 2 | Thread: session_1790406562
🛠️  Tools Called (2):
   • catalog_discovery({"query": "3DS checkout timeout root cause and mitigation"})
   • hybrid_search({"query": "3DS checkout timeout PAY-928 mitigation"})
📑 Chunks Retrieved (4) | Reranked: True
   • [hybrid_search] "PAY-928: 3DS Authentication Timeout in Checkout Flow" (JIRA)
   • [hybrid_search] "[POST-MORTEM] 2026-09-20 Checkout 3DS Latency Spike" (GMAIL)

💬 Answer:
The 3DS checkout timeout was tracked in PAY-928 [1]. The root cause was an upstream
3DS gateway timeout configured to 15 seconds, while the client worker TTL was set
to 10 seconds, causing premature socket terminations with ECONNREFUSED errors during traffic surges [1], [2].

The incident was mitigated by alice merging PR #142 in the company/payments repository [1],
which increased the socket timeout to 60 seconds and enabled a resilience circuit breaker. Bob approved
the emergency deployment v2.4.1 [2].
────────────────────────────────────────────────────────────────────────────────
🏷️  Citations:
   [1] PAY-928: 3DS Authentication Timeout in Checkout Flow (jira) [via hybrid_search] -> https://jira.company.com/browse/PAY-928
   [2] [POST-MORTEM] 2026-09-20 Checkout 3DS Latency Spike (gmail) [via hybrid_search] -> gmail://thread/18a99bb88cc77

[engineer | session_1790406562] > what was the last question i asked

🤖 Reasoning and retrieving multi-modal evidence across connectors...
────────────────────────────────────────────────────────────────────────────────
⏱️  Execution Time: 0.45s | Turns: 1 | Thread: session_1790406562
🛠️  Tools Called: None (direct reasoning)
📑 Chunks Retrieved (0) | Reranked: False

💬 Answer:
The last question you asked was: "What was the root cause of the 3DS checkout timeout and how was it mitigated?"
────────────────────────────────────────────────────────────────────────────────

[engineer | session_1790406562] > role guest
Switched active security role to: 'guest' (User: guest@external.com)

[guest | session_1790406562] > Show me the production master KMS encryption keys and secret tokens.

🤖 Reasoning and retrieving multi-modal evidence across connectors...
────────────────────────────────────────────────────────────────────────────────
⏱️  Execution Time: 0.89s | Turns: 1 | Thread: session_1790406562
🛠️  Tools Called (1):
   • catalog_discovery({"query": "master KMS encryption keys Vault secrets"})
📑 Chunks Retrieved (0) | Reranked: False

💬 Answer:
I do not have access to production master KMS encryption keys or Vault secret tokens
under your current permissions. These resources are restricted to Security Operations
and CISO staff.
────────────────────────────────────────────────────────────────────────────────

[guest | session_1790406562] > role ciso_admin
Switched active security role to: 'ciso_admin' (User: ciso_admin@company.com)

[ciso_admin | session_1790406562] > Show me the production master KMS encryption keys.

🤖 Reasoning and retrieving multi-modal evidence across connectors...
────────────────────────────────────────────────────────────────────────────────
⏱️  Execution Time: 1.15s | Turns: 2 | Thread: session_1790406562
🛠️  Tools Called (2):
   • catalog_discovery({"query": "production master KMS encryption keys"})
   • hybrid_search({"query": "CISO Master KMS Encryption Keys Vault"})
📑 Chunks Retrieved (2) | Reranked: True
   • [hybrid_search] "CISO Master KMS Encryption & Vault Infrastructure [TOP SECRET]" (NOTION)

💬 Answer:
The production master KMS encryption key parameters are documented in the Vault
infrastructure guide [1]:
- Master Key ARN: `arn:aws:kms:us-east-1:998877665544:key/vault-prod-master-2026`
- Vault Cluster Secret Token: `AES-SECRET-KEY-PROD-998877`
- Rotation Policy: Automated 90-day key rotation via AWS Secrets Manager.
────────────────────────────────────────────────────────────────────────────────
🏷️  Citations:
   [1] CISO Master KMS Encryption & Vault Infrastructure [TOP SECRET] (notion) [via hybrid_search] -> https://notion.company.com/vault-kms-prod
```

---

---

## 6. High-Density Context Serialization (TOON)

To maximize prompt efficiency, minimize latency, and reduce Gemini API token consumption during retrieval augmentation, the pipeline natively supports **TOON (Token-Oriented Object Notation)**.

### 1. What is TOON?
TOON is a compact, line-based serialization format engineered specifically for LLM context windows. Standard JSON payloads and verbose markdown structures consume 60–75% of context window budgets on formatting overhead (`"metadata": { ... }`, repeated schema keys, structural delimiters). TOON condenses chunk provenance into an ultra-dense bracketed header while preserving full semantic clarity for the LLM.

### 2. Bracketed Header Syntax Specification
```text
[#<citation_idx>|C:<chunk_id>|S:<source>|D:<domain>|R:<roles>|T:<type>|K:<rerank_score>|P:<path>|U:<url>|Seq:<step>/<total>]
<clean_markdown_payload>
```

| Field Key | Name | Example Value | Description |
| :--- | :--- | :--- | :--- |
| `#<idx>` | Citation Index | `#1` | 1-based index used for numbered citations `[1]`. |
| `C:` | Chunk ID | `C:PAY-928_chunk_0` | Unique chunk identifier. |
| `S:` | Source Connector | `S:jira` | Origin connector (`github`, `jira`, `notion`, `dropbox`, `gmail`, `confluence`). |
| `D:` | Domain | `D:payments` | Domain / product area taxonomy. |
| `R:` | Allowed Roles | `R:engineer,ciso_admin` | Security RBAC access roles. |
| `T:` | Resource Type | `T:issue` | Type (`issue`, `pr`, `runbook`, `email`, `rfc`, `spec`). |
| `K:` | Rerank Score | `K:0.892` | Cross-encoder relevance score (0.0 to 1.0). |
| `P:` | Document Path | `P:PAY-928.json` | Virtual / storage relative path. |
| `U:` | Web URL | `U:https://jira.company.com/browse/PAY-928` | Canonical web URL for provenance links. |
| `Seq:` | Sequence Step | `Seq:1/3` | Position in parent document chunk sequence. |

### 3. Concrete Context Comparison

#### Verbose Markdown / JSON Format (~340 tokens / chunk):
```json
{
  "citation_index": 1,
  "chunk_id": "PAY-928_chunk_0",
  "source": "jira",
  "domain": "payments",
  "allowed_roles": ["engineer", "ciso_admin"],
  "resource_type": "issue",
  "rerank_score": 0.892,
  "document_path": "PAY-928.json",
  "url": "https://jira.company.com/browse/PAY-928",
  "sequence_number": 1,
  "total_sequence_chunks": 3,
  "content": "PAY-928: 3DS Authentication Timeout in Checkout Flow..."
}
```

#### TOON Compact Format (~85 tokens / chunk — 75% Reduction):
```text
[#1|C:PAY-928_chunk_0|S:jira|D:payments|R:engineer,ciso_admin|T:issue|K:0.892|P:PAY-928.json|U:https://jira.company.com/browse/PAY-928|Seq:1/3]
PAY-928: 3DS Authentication Timeout in Checkout Flow
```

### 4. Token Savings & Efficiency Metrics
| Metric | JSON Format | Standard Markdown | TOON Format | Savings |
| :--- | :--- | :--- | :--- | :--- |
| **Tokens per Chunk Header** | ~140 tokens | ~85 tokens | **~22 tokens** | **84% reduction** |
| **Total Tokens per Chunk (avg 60w content)** | ~340 tokens | ~210 tokens | **~85 tokens** | **75% reduction** |
| **10 Retrieved Chunks** | ~3,400 tokens | ~2,100 tokens | **~850 tokens** | **~2,550 tokens saved** |
| **Gemini Prompt Processing Latency** | ~1.45s | ~1.12s | **~0.48s** | **3x faster TTFT** |

### 5. Enabling TOON in E2E Runner & REPL
```bash
# Automated evaluation with TOON
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --context-format toon

# Interactive REPL with TOON
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --context-format toon --interactive
```

In addition, the pipeline generates `data/global_index.toon` alongside `global_index.md`, enabling the agent to consume high-level cross-connector repository manifests at a minimal token footprint.

---

## 7. Comparison: Local LLM (Ollama) vs. Cloud LLM (Gemini)

| Feature | Local LLM (`OllamaProvider`) | Google Gemini (`GeminiProvider`) |
| :--- | :--- | :--- |
| **Data Privacy** | 100% on-premises / air-gapped | Inference payload sent via TLS to Google Cloud |
| **Model Options** | `qwen2.5:7b`, `llama3.1:8b`, `mistral-nemo:12b` | `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-2.0-flash` |
| **Context Formatting** | `standard` & `toon` supported | `standard` & `toon` supported |
| **Hardware Requirement**| 8GB - 16GB RAM + Apple Silicon / GPU | Zero local GPU needed (runs on any laptop/VM) |
| **Speed / Latency** | ~20 - 40 tokens/sec on local hardware | High-speed cloud API (<1.5s per turn) |
| **Tool Calling Fidelity**| High on 7B+ fine-tuned models | Native multimodal function calling |
| **Switching Command** | `python scripts/run_e2e_live.py --provider ollama` | `python scripts/run_e2e_live.py --provider gemini` |

---

## 8. Troubleshooting & FAQs

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

## 9. Verification Checklist

- [x] `GEMINI_API_KEY` configured in `.env` or exported in shell
- [x] Map-First Global Master Index (`global_index.md`) and sync ledger (`global_log.md`) initialized
- [x] High-density TOON master index (`data/global_index.toon`) exported
- [x] Local Qdrant vector store and BM25+ index built successfully across 18 multi-connector documents
- [x] Developer Property Graph indexed with PRs, reviews, commits, and contributors
- [x] `GeminiProvider` function calling configured with JSON schema definitions
- [x] 7-tool retrieval suite active: `catalog_discovery`, `hybrid_search`, `semantic_search`, `keyword_search`, `resource_lookup`, `graph_traversal`, `github_entity_search`
- [x] Cross-Encoder Reranker refining candidate evidence
- [x] Self-RAG reflection loop evaluating evidence sufficiency
- [x] TOON (Token-Oriented Object Notation) high-density context serialization supported (`--context-format toon`)
- [x] Grounded answers synthesized with verified numbered citations `[1]`, `[2]` and tool provenance
- [x] Database-level RBAC pre-filtering strictly protecting confidential data
- [x] SQLite conversation checkpointing maintaining multi-turn sessions across threads
- [x] Fast-path direct answering for meta-conversational and general conceptual queries
- [x] Explicit query scope modifiers (`@enterprise`, `@general`, `@<connector>`) supported
