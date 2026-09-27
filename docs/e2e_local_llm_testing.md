# End-to-End Enterprise Knowledge Agent Testing Guide (Local LLM First)

> **Document:** `docs/e2e_local_llm_testing.md`  
> **Status:** Production Testing & Operator Guide  
> **Target Audience:** Developers, SREs, and Platform Engineers  
> **Last Updated:** 2026-09-27  

---

## 1. Overview & Architecture

This guide walks through testing the **complete, end-to-end Enterprise Knowledge Agent pipeline** locally without relying on external cloud APIs or third-party hosted services.

The entire stack runs **100% locally and air-gapped**:
- **Local LLM Engine:** [Ollama](https://ollama.com) running `qwen2.5:7b`, `llama3.1:8b`, or `mistral-nemo:12b` on `localhost:11434`.
- **Local Dense Embeddings:** In-process `sentence-transformers` (`BAAI/bge-base-en-v1.5` / `Qwen`) generating 768-dim embeddings.
- **Local Vector Store:** Qdrant (in-memory or persistent local disk).
- **Local Sparse Index:** BM25+ inverted keyword index.
- **Local Graph Store:** In-Memory Developer Property Graph with 12 parameterized Cypher operations (or local Neo4j).
- **Local Cross-Encoder Reranker:** In-process neural cross-attention reranker (`ms-marco-MiniLM-L-6-v2` / `bge-reranker`).
- **Map-First Global Master Index:** `global_index.md` manifest and `global_log.md` sync ledger.
- **Multi-Turn Session Persistence:** `SqliteCheckpointSaver` (or `MemorySaver`) persisting conversation state across threads.
- **LangGraph State Machine:** Multi-node workflow (`reasoner` $\to$ `tool_node` $\to$ `reranker` $\to$ `evaluator` $\to$ `generator`) with Self-RAG reflection and fast-path direct answering.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          LOCAL END-TO-END DATA & QUERY PIPELINE                        │
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
                     Local LLM via Ollama (qwen2.5:7b / llama3.1:8b)
                                            │
                                            ▼
                       Cited Answer [1], [2] + SQLite Checkpointing
```

---

## 2. Prerequisites & System Requirements

| Requirement | Recommended Specification | Minimum |
| :--- | :--- | :--- |
| **Operating System** | macOS (Apple Silicon M1/M2/M3/M4) or Linux | Windows (WSL2) |
| **Python Version** | Python 3.10, 3.11, 3.12, or 3.14 | Python 3.10 |
| **System Memory** | 16 GB RAM (for 7B/8B model inference) | 8 GB RAM |
| **Disk Space** | 10 GB free (for Ollama models & embedding weights)| 5 GB free |

---

## 3. Step 1: Install & Start Local LLM (Ollama)

### 1. Install Ollama

- **macOS:**
  ```bash
  brew install ollama
  ```
- **Linux:**
  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ```

### 2. Pull a Tool-Calling Capable Model

For multi-tool reasoning and structured function calling in LangGraph, we recommend:
```bash
# Recommended 1: Qwen 2.5 7B (Superior function calling & instruction following)
ollama pull qwen2.5:7b

# Recommended 2: Llama 3.1 8B (Strong general reasoning)
ollama pull llama3.1:8b

# Light option: Qwen 2.5 3B (Fast on lower-spec machines)
ollama pull qwen2.5:3b
```

### 3. Start the Ollama Background Server

In a dedicated terminal window:
```bash
ollama serve
```

*Verify Ollama is responding:*
```bash
curl http://localhost:11434/api/tags
# Should return JSON listing your pulled models
```

---

## 4. Step 2: Environment Setup

Activate your Python virtual environment and ensure all requirements are installed:

```bash
# Navigate to repository root
cd /path/to/Enterprise-Knowledge-Agent

# Activate virtualenv
source .venv/bin/activate

# Ensure ollama and sentence-transformers packages are available
pip install ollama sentence-transformers qdrant-client rank-bm25 langgraph langchain-core python-dotenv
```

### Optional: Configure Live Connector Credentials in `.env`

If you have live enterprise data sources, populate their API tokens in `.env`:

```bash
# GitHub
GITHUB_TOKEN=ghp_your_github_token
GITHUB_REPOS=company/payments,company/auth-service

# Jira
JIRA_URL=https://company.atlassian.net
JIRA_EMAIL=engineer@company.com
JIRA_API_TOKEN=your_jira_token

# Notion
NOTION_API_KEY=secret_notion_key
NOTION_ROOT_PAGE_ID=your_page_id

# Dropbox
DROPBOX_ACCESS_TOKEN=sl.your_dropbox_token

# Gmail / Google Workspace
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
GMAIL_USER_EMAIL=user@company.com

# Confluence
CONFLUENCE_URL=https://company.atlassian.net/wiki
CONFLUENCE_USERNAME=engineer@company.com
CONFLUENCE_API_TOKEN=your_confluence_token

# Knowledge Graph (Neo4j) — Optional (Falls back to in-memory graph if omitted)
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

> **Note:** The joint test runner script ([`scripts/run_e2e_live.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/run_e2e_live.py)) includes an 18-document multi-modal enterprise corpus representing all 6 connectors. If live credentials for any connector or Neo4j are omitted, the script automatically falls back to normalized OKF test bundles and in-memory graph storage, allowing immediate full-pipeline testing.

---

## 5. Step 3: Run the Live End-to-End Test Suite

Run the joint execution script with your local Ollama model:

```bash
# Run automated verification across all connectors (Default: Local Persistent Disk Storage + SQLite Sessions)
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b

# Run with TOON (Token-Oriented Object Notation) high-density context formatting
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --context-format toon

# Run with ephemeral in-memory storage (resets on exit)
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --qdrant-mode memory --checkpoint-mode memory

# Force wipe and re-index persistent storage
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --reset-storage

# Custom persistent storage directory
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --qdrant-path ./data/custom_qdrant

# Run with live Neo4j database (instead of default in-memory graph)
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --neo4j-password secret_password_123
```

### Storage, Graph & Session Persistence Options:
| CLI Flag | Default | Description |
| :--- | :--- | :--- |
| `--provider` | `ollama` | LLM Provider (`ollama` or `gemini`). |
| `--model` | `llama3.1:8b` | Model name (e.g. `qwen2.5:7b`, `llama3.1:8b`, `mistral-nemo:12b`). |
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

> 💡 **Persistent Indexing Benefit:** When using `mode="local"` (default), after the initial ingestion run, subsequent test runs detect existing vectors on disk and skip the re-embedding phase automatically, making repeated runs instantaneous!
> 
> 🕸️ **Dual Graph Mode:** If you do not pass `--neo4j-password` or set `NEO4J_PASSWORD` in `.env`, the pipeline runs with 0 dependencies using the built-in `InMemoryEntityGraph`. If a password is provided, it automatically connects, creates constraints, and synchronizes entities to live Neo4j.

### What Happens During Execution:
1. **[1/6] Storage Setup:** Initializes Qdrant vector store (`mode="local"`) and loads or builds the BM25+ inverted index.
2. **[2/6] Document Ingestion:** Normalizes 18 documents from GitHub, Jira, Notion, Dropbox, Gmail, and Confluence into OKF v0.2 concept bundles and runs structure-preserving chunking (or reuses existing vectors on disk).
3. **[3/6] Property Graph Ingestion:** Populates developer relationships, PRs, review chains, and modified files in the property graph.
4. **[4/6] LLM Connection:** Connects to local Ollama daemon at `http://localhost:11434`.
5. **[5/6] Global Master Index Generation:** Builds Map-First `global_index.md` and `global_log.md` sync ledger.
6. **[6/6] Multi-Modal Retrievers & LangGraph Planner:** Initializes the full 7-tool suite, Cross-Encoder reranker, SQLite checkpointer, and compiles the agent state machine.
7. **Execution of 8 Live Test Scenarios:**
   - **Case 1 (Incident Investigation):** Multi-hop trace across Jira (`PAY-928`), GitHub (PR fix `#142` by `alice`), and Gmail (post-mortem email).
   - **Case 2 (Architecture & APIs):** Verification of `/v1/payments/initiate` and `Idempotency-Key` headers from GitHub specs.
   - **Case 3 (Disaster Recovery SOP):** Step-by-step PostgreSQL failover procedure (`patronictl`, `PgBouncer`) from Dropbox runbooks.
   - **Case 4 (Security / RBAC Isolation):** Verifies unauthorized `guest` user is strictly blocked from reading CISO master encryption keys.
   - **Case 5 (Authorized CISO Access):** Verifies `ciso_admin` successfully accesses master KMS vault parameters (`vault-prod-master`).
   - **Case 6 (SRE OpenSearch Reindexing):** Zero-downtime index creation (`product_catalog_v2`), `_reindex`, and atomic `_aliases` switchover from Dropbox SOP.
   - **Case 7 (DevOps Kubernetes Ingress & TLS):** Cert-Manager `letsencrypt-production` `ClusterIssuer` and 500 req/s rate limits from GitHub manifests.
   - **Case 8 (Real-Time Streaming Data Pipeline):** Flink + Iceberg real-time clickstream latency reduction from 8 hours to under 45 seconds from Jira `DATA-782`.

---

## 6. Step 4: Interactive Live Chat REPL Mode

To interact with the Enterprise Knowledge Agent in real-time as a chatbot:

```bash
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --interactive
```

### Interactive REPL Commands:

#### 1. Session & Persona Management
- `role <role_name>`: Switch active security persona (e.g. `role engineer`, `role ciso_admin`, `role guest`).
- `thread <thread_id>`: Switch active conversation thread/session.
- `threads`: List all saved conversation threads in SQLite storage.
- `new`: Start a fresh conversation thread.
- `history`: Display message history for current thread.
- `exit` or `quit`: Exit REPL.

#### 2. Query Scope Modifiers
- `@enterprise <query>` / `@docs <query>`: Force internal enterprise retrieval tools across connectors.
- `@general <query>` / `@llm <query>`: Direct LLM conceptual/meta answer (0 tools, 0s retrieval latency).
- `@<connector> <query>`: Target specific source (e.g. `@jira PAY-928`, `@github cert-manager`, `@dropbox opensearch`).

#### 3. Conversational Meta-Queries
Ask questions about past conversation turns (*"what was the last question I asked?"*, *"summarize our discussion"*) to get direct answers with 0 tool calls.

---

## 7. High-Density Context Serialization (TOON) for Local LLMs

Local LLM inference faces two primary constraints: **limited VRAM / unified memory** and **prompt processing latency (Time-to-First-Token / TTFT)**. The pipeline features native **TOON (Token-Oriented Object Notation)** support to drastically reduce prompt overhead for local models.

### 1. Why TOON is Crucial for Local Models
1. **Accelerated Prompt Ingestion (TTFT):** On local hardware (Apple Silicon M-series or consumer GPUs), evaluating a 4,000-token prompt can take 2–5 seconds. Reducing prompt metadata by 60–75% cuts TTFT down to under 1 second.
2. **Context Window Protection:** Standard 8B models (e.g. `llama3.1:8b`, `qwen2.5:7b`) typically operate with 8k–32k context windows. TOON enables packing **3x–4x more retrieved evidence chunks** into the same budget without triggering context truncation.
3. **Structured Provenance without JSON Parsing Errors:** Small local models can struggle with complex nested JSON metadata in context. TOON's bracketed header format is clean, unambiguous, and trivial for 7B/8B models to cite accurately.

### 2. Header Syntax Specification
```text
[#<citation_idx>|C:<chunk_id>|S:<source>|D:<domain>|R:<roles>|T:<type>|K:<rerank_score>|P:<path>|U:<url>|Seq:<step>/<total>]
<clean_markdown_payload>
```

| Key | Description | Example |
| :--- | :--- | :--- |
| `#<idx>` | Citation reference number | `#1` |
| `C:` | Chunk ID | `C:PAY-928_chunk_0` |
| `S:` | Source connector | `S:jira` |
| `D:` | Domain / service | `D:payments` |
| `R:` | RBAC roles allowed | `R:engineer,ciso_admin` |
| `T:` | Resource type | `T:issue` |
| `K:` | Cross-encoder rerank score | `K:0.892` |
| `P:` | Document path | `P:PAY-928.json` |
| `U:` | Source URL | `U:https://jira.company.com/browse/PAY-928` |
| `Seq:` | Chunk position / total chunks | `Seq:1/3` |

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

### 4. Running Local E2E Pipeline with TOON
```bash
# Automated verification with local model + TOON
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --context-format toon

# Interactive REPL with local model + TOON
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b --context-format toon --interactive
```

---

## 8. Step 5: Switching from Local LLM to Cloud LLM (Gemini)

If you ever want to benchmark local Ollama against Google Gemini without changing any code:

```bash
export LLM_PROVIDER=gemini
export GEMINI_API_KEY="your-gemini-api-key"
export GEMINI_MODEL="gemini-2.5-flash"

python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --context-format toon
```

---

## 9. Troubleshooting & Tips for Local LLMs

### 1. `ConnectionRefusedError: [Errno 61] Connection refused`
- **Cause:** Ollama background daemon is not running.
- **Fix:** Open a terminal and run `ollama serve`. Keep this window open.

### 2. Slow Response Times on Older Hardware
- **Fix 1:** Use TOON format (`--context-format toon`) to cut prompt ingestion time by up to 75%.
- **Fix 2:** Use smaller quantized models, e.g., `ollama pull qwen2.5:3b`.
- **Fix 3:** Ensure GPU / Metal acceleration is enabled in Ollama settings.

### 3. Model Not Calling Tools / Hallucinating JSON
- Smaller models (<3B) may struggle with strict JSON schema tool calls.
- **Recommended Fix:** Use `qwen2.5:7b` or `mistral-nemo:12b`, which are specifically fine-tuned for high-accuracy function and tool calling.

### 4. Offline Embedding Warning
- If Hugging Face attempts to reach the internet, export the offline flags:
  ```bash
  export HF_HUB_OFFLINE=1
  export TRANSFORMERS_OFFLINE=1
  ```

---

## 10. Verification Checklist

- [x] Ollama daemon running at `http://localhost:11434`
- [x] Tool-capable local model pulled (`qwen2.5:7b` or `llama3.1:8b`)
- [x] Map-First Global Master Index (`global_index.md`) and sync ledger (`global_log.md`) initialized
- [x] High-density TOON master index (`data/global_index.toon`) exported
- [x] Qdrant local vector store initialized with RBAC payload filter across 18 multi-modal documents
- [x] BM25+ inverted index built for exact tokens and Jira/GitHub IDs
- [x] Developer Property Graph indexed with PRs, commits, reviews, and contributors
- [x] 7-tool retrieval suite active: `catalog_discovery`, `hybrid_search`, `semantic_search`, `keyword_search`, `resource_lookup`, `graph_traversal`, `github_entity_search`
- [x] Cross-Encoder Reranker active for semantic precision
- [x] Self-RAG reflection loop verifying evidence sufficiency
- [x] TOON (Token-Oriented Object Notation) high-density context serialization supported (`--context-format toon`)
- [x] Grounded answers generated with numbered citations `[1]`, `[2]` and tool provenance
- [x] Database-level RBAC pre-filtering preventing confidential data leaks
- [x] SQLite conversation checkpointing maintaining multi-turn sessions across threads
- [x] Fast-path direct answering for meta-conversational and general conceptual queries
- [x] Explicit query scope modifiers (`@enterprise`, `@general`, `@<connector>`) supported
