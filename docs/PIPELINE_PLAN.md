# Agentic Enterprise Knowledge System — Implementation Plan (v2)

## What We're Building

An **agentic, permission-aware enterprise knowledge system** where a Gemini-powered
agent dynamically decides which retrieval tool to call — with a clean split between
local infrastructure and swappable LLM providers.

```text
                    Enterprise Data
                         │
                         ▼
                    OKF Pipeline
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     Embeddings        Qdrant         Neo4j
      LOCAL             LOCAL          LOCAL
     (sentence-      (vector DB)   (graph DB)
     transformers)
          │              │              │
          └──────────────┼──────────────┘
                         │
                         ▼
                    User Query
                         │
                         ▼
                ┌─────────────────┐
                │  LLMProvider    │  ← swappable: Gemini API / Ollama / OpenAI
                │  Agent/Planner  │
                └────────┬────────┘
                         │  tool calls
              ┌──────────┼──────────┐
              ▼          ▼          ▼
           Vector      Keyword     Graph
            Tool        Tool        Tool
           (Qdrant)    (BM25)    (Neo4j)
              └──────────┼──────────┘
                         ▼
                  Local Reranker
                (cross-encoder)
                         ▼
                  Context Builder
                         ▼
                ┌─────────────────┐
                │  LLMProvider    │  ← same abstraction: Gemini API / Ollama
                │  Answer + Cite  │
                └─────────────────┘
```

---

## Two Layers — Stay Separate Forever

| Layer             | What it does                                                                                      | How it's built                                                |
| :---------------- | :------------------------------------------------------------------------------------------------ | :------------------------------------------------------------ |
| **Deterministic** | Connectors, OKF, chunking, embeddings, vector DB, keyword index, graph DB, reranking, permissions | Pure Python, zero LLM decisions, fully local                  |
| **Agentic**       | Query understanding, tool selection, iterative retrieval, answer synthesis                        | `LLMProvider` abstraction — swap API ↔ local with one env var |

The agent **never** controls how chunks are created, how Qdrant is queried internally,
or how Neo4j Cypher is built.

---

## Tech Stack Decisions (Updated)

| Component           | Choice                                              | Mode                | Why                                                                           |
| :------------------ | :-------------------------------------------------- | :------------------ | :---------------------------------------------------------------------------- |
| Embeddings          | `BAAI/bge-base-en-v1.5` via `sentence-transformers` | **Local**           | No API cost, stable, works offline. 768-dim. Good for mixed code+text content |
| Reranker            | `cross-encoder/ms-marco-MiniLM-L-6-v2`              | **Local**           | Fast, inexpensive, no API calls on every query                                |
| Vector DB           | Qdrant (local mode)                                 | **Local**           | Metadata filtering for RBAC; no Docker needed initially                       |
| Keyword index       | `rank-bm25`                                         | **Local**           | Zero infra, pure Python                                                       |
| Graph DB            | Neo4j                                               | **Local**           | Already built                                                                 |
| Agent / Planner LLM | `LLMProvider` → Gemini 2.0 Flash initially          | **API → swappable** | Reliable function calling; switch to Ollama for private deployment            |
| Answer LLM          | Same `LLMProvider` instance                         | **API → swappable** | Better quality for demos; same swap path as agent                             |

### Why local embeddings?

You may embed thousands of chunks across all connectors. Making every chunk
dependent on a third-party API creates cost, rate-limit, and outage risk. A
local `sentence-transformers` model runs in-process, is free, and is stable.

### Why a provider abstraction for LLM?

An enterprise product eventually cannot send retrieved internal data to a cloud API.
Designing the `LLMProvider` interface from the start means switching from Gemini
to Ollama is one env var change — no architectural rewrite.

---

## LLMProvider Abstraction — Central Design

```python
# backend/llm/base.py
class LLMProvider:
    def generate(self, messages: list[dict]) -> str: ...
    def generate_with_tools(self, messages: list[dict], tools: list[dict]) -> ToolCall | str: ...

# backend/llm/gemini_provider.py
class GeminiProvider(LLMProvider): ...   # uses google-genai SDK

# backend/llm/ollama_provider.py
class OllamaProvider(LLMProvider): ...   # uses ollama Python client

# backend/llm/openai_provider.py
class OpenAIProvider(LLMProvider): ...   # uses openai SDK
```

**Usage everywhere in the system**:

```python
agent = Agent(llm=llm_provider, tools=tools)
answer_gen = AnswerGenerator(llm=llm_provider)
```

**Configuration** (single env var switches provider):

```env
# .env
LLM_PROVIDER=gemini          # development
LLM_PROVIDER=ollama          # private/enterprise deployment
LLM_PROVIDER=openai          # alternative

EMBEDDING_PROVIDER=local     # always local
RERANKER_PROVIDER=local      # always local
```

---

## Where We Are Now

| Component                                     | Status       |
| :-------------------------------------------- | :----------- |
| Connectors (GitHub, Notion, Dropbox, Gmail)   | ✅ Done      |
| OKF normalization (`Document` → `OKFConcept`) | ✅ Done      |
| Graph models + extraction + Neo4j client      | ✅ Done      |
| `LLMProvider` abstraction                     | ❌ Not built |
| Chunker                                       | ❌ Not built |
| Local embeddings                              | ❌ Not built |
| Qdrant vector store                           | ❌ Not built |
| BM25 keyword index                            | ❌ Not built |
| Agent + tool loop                             | ❌ Not built |
| Retrieval tools                               | ❌ Not built |
| Local reranker                                | ❌ Not built |
| Permission pre-filter                         | ❌ Not built |
| Answer generator                              | ❌ Not built |

---

## Build Phases

### Phase 0 — LLMProvider Abstraction

**Goal**: Define the LLM interface before anything else touches it.
Built first so every subsequent phase that needs an LLM just depends on the abstract interface.

```python
provider = GeminiProvider(api_key=...)   # development
provider = OllamaProvider(model="llama3") # private deployment
# same agent code, zero changes
```

**Files to create**:

- `backend/llm/__init__.py`
- `backend/llm/base.py` — `LLMProvider` abstract base class + `ToolCall` dataclass
- `backend/llm/gemini_provider.py` — Gemini 2.0 Flash with function calling
- `backend/llm/ollama_provider.py` — Ollama with function calling (structured output)
- `backend/llm/factory.py` — `get_llm_provider()` reads `LLM_PROVIDER` env var

---

### Phase 1 — Chunker + Chunk Model

**Goal**: Split OKF content into retrievable chunks with full metadata attached.

```text
OKFConcept
    │
    ▼
OKFChunker       ← connector-aware splitting strategy
    │
    ▼
List[Chunk]      ← text + resource_id + source + permissions + url + timestamps
```

**Key rule**: Every chunk carries the full metadata payload needed for RBAC and citation.
The vector is just the embedding — the metadata travels alongside it.

**Connector-aware strategies**:

- GitHub: PR description + comments as separate chunks; file content by 512-token windows
- Notion/Confluence: Heading-section boundaries (h2 = chunk boundary)
- Slack: Thread-level chunks (not individual messages)
- Gmail: Thread summary + body (not individual email per chunk)
- Default: Sliding window (512 tokens, 64 token overlap)

**Files to create**:

- `backend/ingestion/__init__.py`
- `backend/ingestion/chunk.py` — `Chunk` dataclass
- `backend/ingestion/chunker.py` — `OKFChunker` class

---

### Phase 2 — Local Embeddings + Qdrant

**Goal**: Embed chunks locally and store in Qdrant with full metadata payload.

```text
List[Chunk]
    │
    ▼
LocalEmbedder       ← sentence-transformers BAAI/bge-base-en-v1.5
    │                  runs in-process, no API key
    ▼
QdrantClient        ← upsert vectors + metadata payload
```

**Qdrant payload per vector** (stored alongside the embedding):

```json
{
  "chunk_id": "chunk_abc123",
  "resource_id": "github:repo:owner/name:pr:17",
  "source": "github",
  "resource_type": "pull_request",
  "text": "This PR fixes the authentication timeout...",
  "url": "https://github.com/owner/name/pull/17",
  "title": "Fix auth timeout",
  "permissions": {
    "is_public": true,
    "allowed_roles": ["employee"],
    "allowed_users": [],
    "allowed_groups": []
  },
  "created_at": "2026-09-01T00:00:00Z",
  "updated_at": "2026-09-04T00:00:00Z"
}
```

**Files to create**:

- `backend/ingestion/embedder.py` — `LocalEmbedder` wrapping `sentence-transformers`
- `backend/storage/__init__.py`
- `backend/storage/qdrant_client.py` — upsert, search, delete, collection management
- `backend/ingestion/pipeline.py` — ties OKF → Chunk → Embed → Qdrant together

**New packages**:

```bash
pip install sentence-transformers qdrant-client
```

---

### Phase 3 — BM25 Keyword Index

**Goal**: In-memory BM25 index over the same chunks for exact/ID lookup.

```text
List[Chunk]
    │
    ▼
BM25Index       ← rank-bm25 from chunk texts, persisted to disk as JSON
    │
    ▼
keyword_search(query) → ranked List[Chunk]
```

**When keyword beats semantic**:

- PR/ticket IDs: `"PR #1842"`, `"PAY-928"`
- Function/class names: `"authenticate_user()"`, `"PaymentService"`
- Error codes: `"HTTP 401"`, `"ECONNREFUSED"`
- Exact person/repo names

**Files to create**:

- `backend/storage/bm25_index.py` — `BM25Index` (build, search, save to disk, load from disk)

**New packages**:

```bash
pip install rank-bm25
```

---

### Phase 4 — Basic Agent with Semantic Tool ← **MILESTONE 1**

**Goal**: Full working loop — user question → agent → tool call → cited answer.

```text
User: "How does authentication work?"
    │
    ▼
Agent (LLMProvider.generate_with_tools)
    │  tool_call:
    ▼
semantic_search(query="authentication", top_k=5)
    │
    ▼
[Chunk 1, Chunk 2, Chunk 3...]  (all local Qdrant)
    │
    ▼
Context Builder → Answer Generator (LLMProvider.generate)
    │
    ▼
"Authentication is handled by..." + [Source: GitHub PR #17]
```

**Files to create**:

- `backend/retrieval/__init__.py`
- `backend/retrieval/semantic.py` — `semantic_search()` implementation
- `backend/agent/__init__.py`
- `backend/agent/tools.py` — tool schemas for the LLMProvider (provider-agnostic format)
- `backend/agent/planner.py` — agent loop: query → tool call → execute → answer
- `backend/generation/__init__.py`
- `backend/generation/context_builder.py` — formats chunks into structured LLM context
- `backend/generation/answer_generator.py` — final LLM call + citations

**Requires**: `GEMINI_API_KEY` in `.env` (or `LLM_PROVIDER=ollama`)

---

### Phase 5 — Keyword Tool

**Goal**: Agent picks `keyword_search` for ID-based queries automatically.

```text
User: "What happened in PR #1842?"
    │
    ▼
Agent → keyword_search("PR 1842", top_k=5)   ← agent recognized ID pattern
    │
    ▼
Exact match result → Answer
```

**Files to create**:

- `backend/retrieval/keyword.py` — `keyword_search()` backed by `BM25Index`

---

### Phase 6 — Graph Tools

**Goal**: Agent can answer relationship questions via Neo4j traversal.

```text
User: "Who authored commits touching auth.py?"
    │
    ▼
Agent → graph_search(entity="auth.py", relationship="MODIFIES", direction="incoming")
    │
    ▼
Internally builds safe Cypher → queries Neo4j → returns structured results
    │                 (agent never sees Cypher)
    ▼
"Alice and Bob authored commits that modified auth.py"
```

**Files to create**:

- `backend/retrieval/graph.py` — `graph_search()` (Cypher hidden from agent)
- `backend/retrieval/resource_lookup.py` — `get_resource()`, `get_related()`

---

### Phase 7 — Evidence Evaluator (Iterative Retrieval)

**Goal**: Agent detects missing information and makes a second tool call.

```text
User: "Who approved the authentication fix?"
    │
    ▼
Agent → semantic_search("authentication fix")
    │
    ▼
Result: PR #1842, author John ← reviewer/approver MISSING
    │
    ▼
Evaluator: insufficient evidence
    │
    ▼
Agent → graph_search("PR #1842", "REVIEWED")
    │
    ▼
Alice reviewed PR #1842 → full answer
```

**This is where the system becomes genuinely agentic.**

**Changes to**: `backend/agent/planner.py` — add evaluator node + multi-turn loop
with max-iterations guard (default: 5 tool calls before forced answer)

---

### Phase 8 — RBAC Permission Pre-Filter

**Goal**: Pre-filtered results — users only retrieve what they're authorized for.

**Two-level RBAC**:

1. **At ingestion** (already designed): permissions stored in Qdrant payload + Neo4j properties
2. **At retrieval**: filter built from user identity, applied before vector search

```text
User Query + User Identity (user_id, groups, roles)
    │
    ▼
Permission Resolver → Qdrant filter {"groups": ["engineering"]}
    │
    ▼
semantic_search(query, filters=permission_filter)
    │
    ▼
Results (all pre-authorized — never post-filtered)
```

**Critical**: The agent is **never** trusted to make authorization decisions.
The permission engine is infrastructure, completely outside the agent loop.

**Files to create**:

- `backend/permissions/__init__.py`
- `backend/permissions/resolver.py` — user identity → `PermissionContext`
- `backend/permissions/filters.py` — `PermissionContext` → Qdrant/Neo4j filter objects

---

### Phase 9 — Local Reranker

**Goal**: Top-k candidates are reranked locally for precision before going to the LLM.

```text
Top 20 raw results (from vector/keyword/graph)
    │
    ▼
cross-encoder/ms-marco-MiniLM-L-6-v2   ← local, no API call
(scores relevance of query ↔ chunk pairs)
    │
    ▼
Top 5–10 most relevant
    │
    ▼
Context Builder → Answer LLM
```

**Why local reranker?**
Every query triggers reranking over ~20 results. API calls at this scale add cost
and latency. A local cross-encoder runs in milliseconds on CPU.

**Files to create**:

- `backend/retrieval/reranker.py` — `LocalReranker` using `sentence-transformers` cross-encoder
- Update `backend/generation/context_builder.py` — structured source formatting

**New packages**:

```bash
pip install sentence-transformers   # already installed from Phase 2; cross-encoders included
```

---

### Phase 10 — Hybrid Search + Full Tool Set

**Goal**: Agent has the complete tool set; `hybrid_search` for broad questions.

```python
tools = [
    semantic_search,   # "How does X work?"
    keyword_search,    # "What is PR #1842?"
    graph_search,      # "Who owns Service X?"
    hybrid_search,     # "Why did payment fail?" → vector + BM25 + graph + RRF
    get_resource,      # Fetch full document by resource_id
    get_related,       # 1-hop graph neighbors
]
```

`hybrid_search` runs vector + BM25 + graph in parallel, applies
**Reciprocal Rank Fusion (RRF)**, and returns a single unified ranking.
The agent calls it when the question is broad or ambiguous.

**Files to create**:

- `backend/retrieval/hybrid.py` — parallel search + RRF fusion

---

## Complete Target File Structure

```text
backend/
├── connectors/          ✅ Done
├── models/              ✅ Done
├── graph/               ✅ Done
│
├── llm/                 ← Phase 0  (NEW)
│   ├── __init__.py
│   ├── base.py          ← LLMProvider + ToolCall abstractions
│   ├── gemini_provider.py
│   ├── ollama_provider.py
│   └── factory.py       ← get_llm_provider() from env var
│
├── ingestion/           ← Phase 1-2  (NEW)
│   ├── __init__.py
│   ├── chunk.py
│   ├── chunker.py
│   ├── embedder.py      ← LOCAL: sentence-transformers
│   └── pipeline.py
│
├── storage/             ← Phase 2-3  (NEW)
│   ├── __init__.py
│   ├── qdrant_client.py
│   └── bm25_index.py
│
├── retrieval/           ← Phase 4-10  (NEW)
│   ├── __init__.py
│   ├── semantic.py
│   ├── keyword.py
│   ├── graph.py
│   ├── resource_lookup.py
│   ├── hybrid.py
│   └── reranker.py      ← LOCAL: cross-encoder
│
├── permissions/         ← Phase 8  (NEW)
│   ├── __init__.py
│   ├── resolver.py
│   └── filters.py
│
├── agent/               ← Phase 4-9  (NEW)
│   ├── __init__.py
│   ├── tools.py
│   └── planner.py
│
└── generation/          ← Phase 4-9  (NEW)
    ├── __init__.py
    ├── context_builder.py
    └── answer_generator.py
```

---

## Configuration Design

```env
# .env additions needed

# LLM Provider (swappable — one line to switch)
LLM_PROVIDER=gemini           # development default
# LLM_PROVIDER=ollama         # private/enterprise deployment
# LLM_PROVIDER=openai         # alternative

# Specific model per provider
GEMINI_MODEL=gemini-2.0-flash
OLLAMA_MODEL=llama3.1
OLLAMA_BASE_URL=http://localhost:11434

# Gemini API key (only needed when LLM_PROVIDER=gemini)
GEMINI_API_KEY=your_key_here

# Embedding + Reranker (always local)
EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=BAAI/bge-base-en-v1.5
RERANKER_PROVIDER=local
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

# Vector DB
QDRANT_MODE=local             # "local" (in-process) or "server" (docker)
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=enterprise_knowledge
```

---

## Packages to Install (all phases)

```bash
# Phase 0  (LLM abstraction)
pip install google-genai              # Gemini provider

# Phase 2  (embeddings + vector DB)
pip install sentence-transformers     # local embeddings + reranker
pip install qdrant-client             # vector store

# Phase 3  (keyword index)
pip install rank-bm25

# Phase 6  (Ollama, optional)
pip install ollama                    # local LLM provider
```

---

## Milestone Definitions

| Milestone | Phases | Achieved when...                                                                       |
| :-------- | :----- | :------------------------------------------------------------------------------------- |
| **M1**    | 0–4    | GitHub → OKF → chunk → local embed → Qdrant → agent → `semantic_search` → cited answer |
| **M2**    | 5      | Agent picks `keyword_search` for `"PR #1842"` style queries                            |
| **M3**    | 6      | Agent calls `graph_search` and answers relationship questions                          |
| **M4**    | 7      | Agent makes a second tool call when first result is insufficient                       |
| **M5**    | 8      | User A gets result X; User B (no access) does not                                      |
| **M6**    | 9–10   | Full hybrid multi-tool agentic retrieval with local reranking + citations              |

---

## The Demo Story

> _"The knowledge infrastructure is entirely self-hosted: embeddings run locally,
> Qdrant and Neo4j run locally, and the reranker runs locally. The reasoning
> layer is provider-agnostic — we use Gemini during development, and switching
> to a locally hosted model for enterprise data privacy is a single environment
> variable change."_

That is considerably stronger than "we built a RAG system."

---

> [!IMPORTANT]
> **Before Phase 4 (agent)**: Add `GEMINI_API_KEY` to `.env`.
> Phases 0–3 (LLMProvider interface + chunker + embeddings + BM25) work without it.

> [!NOTE]
> **Qdrant local mode**: No Docker needed for any phase. Switch to server mode
> (`QDRANT_MODE=server`) when you want the visual collection inspector or
> persistence across restarts.

> [!NOTE]
> **First model download**: `sentence-transformers` downloads `BAAI/bge-base-en-v1.5`
> (~440MB) on first run. Cached in `~/.cache/huggingface/hub/` after that.
