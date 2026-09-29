# Enterprise Knowledge Agent

An enterprise AI assistant that retrieves, reasons over, and answers complex questions using an organization's multi-modal knowledge (code repositories, pull requests, Jira tickets, Notion pages, Dropbox runbooks, Gmail post-mortems, and Confluence RFCs) while strictly enforcing database-level Role-Based Access Control (RBAC) permissions.

It normalizes heterogeneous data into a unified schema called the **Open Knowledge Format (OKF v0.2)**, builds a **Map-First Global Master Index** across enterprise assets, indexes content into dense vectors (Qdrant), sparse BM25+ inverted indexes, and a Developer Property Graph (Neo4j / In-Memory), and uses a stateful **LangGraph Agent Planner** with **Self-RAG reflection**, **Cross-Encoder reranking**, and **SQLite multi-turn session persistence**.

---

## 🌟 Key Features & Capabilities

- **6 Enterprise Connectors:** Native ingestion and normalization for **GitHub**, **Jira**, **Notion**, **Dropbox**, **Gmail**, and **Confluence**.
- **Map-First Global Master Index:** Generates `data/global_index.md` and `data/global_log.md` catalogs enabling rapid, high-confidence cross-connector discovery before deep retrieval.
- **Unified Schema (OKF v0.2):** Standardized concept bundles preserving document hierarchy, metadata breadcrumbs, parent containers, and ACL permission boundaries.
- **7-Tool Retrieval Suite:**
  1. `catalog_discovery`: High-density cross-connector manifest search over the Global Master Index.
  2. `hybrid_search`: Reciprocal Rank Fusion (RRF) combining dense vector semantics, BM25+ keywords, and graph entities.
  3. `semantic_search`: 768-dim dense embedding vector search with database-level RBAC pre-filtering.
  4. `keyword_search`: BM25+ exact token matching for ticket IDs (e.g. `PAY-928`), error codes, and code symbols.
  5. `resource_lookup`: Full sequential document and runbook reconstruction by title, URI, or ID.
  6. `graph_traversal`: Parent-child hierarchies, sibling expansions, and ordered step sequences.
  7. `github_entity_search`: Deep developer graph traversal (PRs, commits, reviews, authors, modified files).
- **Local Neural Cross-Encoder Reranker:** Neural cross-attention reranking for sub-millimeter semantic precision.
- **Self-RAG Reflection Loop:** Automated evidence evaluation (`EvidenceEvaluator`) and iterative query reformulation (`QueryReformulator`) when evidence is incomplete.
- **Strict Database-Level RBAC:** Pre-filters candidate chunks before retrieval and inference, preventing unauthorized data leakage (e.g. CISO encryption keys from guest users).
- **Multi-Turn SQLite Session Persistence:** `SqliteCheckpointSaver` preserves conversation state and tool traces across persistent sessions (`thread_id`).
- **Deterministic Query Scope Directives:** Fast-path control via `@enterprise`, `@general`, `@docs`, `@llm`, `@web`, or `@<connector>` (`@jira`, `@github`, `@dropbox`, etc.).
- **Zero-Tool Fast Path:** Answers meta-conversational inquiries (*"what was the last question I asked?"*) and general programming concepts directly in 1 turn with 0 tool calls.
- **Dual LLM Provider Support:**
  - **100% Local & Air-Gapped:** Ollama (`qwen2.5:7b`, `llama3.1:8b`, `mistral-nemo:12b`).
  - **Cloud Frontier Models:** Google Gemini API (`gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-2.0-flash`) via official `google-genai` SDK.

---

## 🏗️ System Architecture

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        ENTERPRISE KNOWLEDGE AGENT ARCHITECTURE                         │
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
                       LangGraph Agent Planner (Self-RAG Loop)
                     (Ollama Local LLM or Google Gemini Cloud API)
                                            │
                                            ▼
                       Cited Answer [1], [2] + SQLite Checkpointing
```

---

## 🚀 Quick Start

### 1. Installation & Environment Setup

```bash
# Clone the repository
git clone https://github.com/omPatil3690/Enterprise-Knowledge-Agent.git
cd Enterprise-Knowledge-Agent

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)

Create or edit `.env` in the repository root:

```bash
# Choose LLM Provider: 'ollama' or 'gemini'
LLM_PROVIDER=gemini
GEMINI_API_KEY=AIzaSyYourSecretGeminiKeyHere
GEMINI_MODEL=gemini-2.5-flash

# If using Local Ollama:
# LLM_PROVIDER=ollama
# OLLAMA_MODEL=llama3.1:8b
# OLLAMA_BASE_URL=http://localhost:11434

# Optional: Knowledge Graph (Neo4j) — Falls back to in-memory graph if omitted
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=secret_password_123
```

---

## 🧪 Running the Live Pipeline & Automated Tests

The joint test runner ([`scripts/run_e2e_live.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/run_e2e_live.py)) runs 8 automated end-to-end verification cases covering incident traces, API specifications, disaster recovery runbooks, RBAC isolation, OpenSearch zero-downtime reindexing, Kubernetes TLS ingress, and real-time streaming data pipelines.

### Run with Google Gemini:
```bash
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash
```

### Run with Local Ollama:
```bash
# Ensure Ollama daemon is running: ollama serve
python scripts/run_e2e_live.py --provider ollama --model qwen2.5:7b
```

### Storage & Session Options:
```bash
# Run with ephemeral in-memory storage
python scripts/run_e2e_live.py --provider gemini --qdrant-mode memory --checkpoint-mode memory

# Force wipe and re-index persistent storage
python scripts/run_e2e_live.py --provider gemini --reset-storage

# Run with live Neo4j database
python scripts/run_e2e_live.py --provider gemini --neo4j-password secret_password_123
```

---

## 💬 Interactive Chat REPL Mode

Launch the interactive terminal session to chat with the agent in real-time across persistent SQLite conversation threads:

```bash
python scripts/run_e2e_live.py --provider gemini --model gemini-2.5-flash --interactive
```

### REPL Commands:
| Command | Action |
| :--- | :--- |
| `role <name>` | Switch active security persona (e.g. `role engineer`, `role ciso_admin`, `role guest`). |
| `thread <id>` | Switch active conversation thread/session. |
| `threads` | List all saved conversation threads in SQLite storage. |
| `new` | Start a fresh conversation thread. |
| `history` | Display conversation history for the current thread. |
| `exit` / `quit` | Exit REPL. |

### Query Scope Modifiers:
- `@enterprise <query>` or `@docs <query>`: Forces enterprise retrieval tools across connectors.
- `@general <query>` or `@llm <query>`: Direct LLM conceptual/meta answer (0 tools, 0s retrieval latency).
- `@<connector> <query>`: Target specific platform (e.g. `@jira PAY-928`, `@github cert-manager`, `@dropbox opensearch`).

---

## 📚 Documentation & Guides

- 📖 **[Google Gemini API E2E Testing Guide](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/e2e_gemini_api_testing.md)** — Comprehensive setup, verification checklist, and benchmarking with Gemini 2.5 Flash / Pro.
- 📖 **[Local LLM (Ollama) E2E Testing Guide](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/e2e_local_llm_testing.md)** — Air-gapped testing guide with Qwen 2.5 7B, LLaMA 3.1 8B, and Mistral-Nemo.
- 📖 **[Query Scope Modifiers & Directives Specification](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/query_scope_modifiers.md)** — Architectural specification for `@enterprise`, `@general`, `@web`, and `@<connector>` commands.
- 📖 **[Architecture & Personal Notes](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/NOTES.md)** — Design decisions, TOON compact formatting, and implementation roadmap.

---

## 🧩 Running the Unit Test Suite

The project includes a comprehensive 120-test automated test suite across all subsystems:

```bash
.venv/bin/pytest backend/ingestion/tests backend/storage/tests backend/retrieval/tests backend/ranking/tests backend/security/tests backend/evaluation/tests backend/agent/tests
```
