# Full-Stack Architecture & FastAPI Backend Implementation Plan

**Milestone:** Enterprise Production Deployment & API Service Layer  
**Target Systems:** React/Vite Frontend, FastAPI Async Backend, Multi-Modal Retrieval Engine, CDC Ingestion Worker  
**Status:** 📋 Architectural Blueprint & Implementation Specification

---

## 1. Executive Summary & Full-Stack Blueprint

This document defines the production architecture for the **Enterprise Knowledge Agent**, detailing:
1. **Frontend Architecture & UX Design** with dynamic **Connector Check/Uncheck filtering** and `@scope` query interactions.
2. **Incremental Ingestion & Change Data Capture (CDC)** strategy defining exactly when and how documents are re-indexed with zero redundant computation.
3. **FastAPI Backend Service Layer** including directory structure, Pydantic V2 schemas, dependency injection, and Server-Sent Events (SSE) streaming.
4. **End-to-End Connector Filter Pipeline** ensuring unchecked platforms are strictly excluded from Qdrant, BM25, Neo4j, Catalog Discovery, and LLM Context.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   FULL-STACK SYSTEM TOPOLOGY                                     │
│                                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                            REACT + VITE FRONTEND INTERFACE                                 │  │
│  │                                                                                            │  │
│  │  [ ☑ GitHub  ☑ Jira  ☐ Notion  ☑ Dropbox  ☑ Gmail  ☐ Confluence ] ◄── Connector Toggles    │  │
│  │                                                                                            │  │
│  │  ┌──────────────────────────────────────────────────────────────────────────────────────┐  │  │
│  │  │ User Input: "@github who approved the 3DS timeout PR?"                                │  │  │
│  │  └──────────────────────────────────────────────────────────────────────────────────────┘  │  │
│  │   • SSE Real-time Thought Stream       • Clickable Citation Slide-out Drawer               │  │
│  │   • Persistent Multi-Turn Sessions     • React Flow Interactive Developer Subgraph View    │  │
│  └──────────────────────────────────────────────┬─────────────────────────────────────────────┘  │
│                                                 │ HTTPS / SSE Streaming                          │
│                                                 ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │                              FASTAPI BACKEND SERVICE LAYER                                 │  │
│  │                                                                                            │  │
│  │  • POST /api/v1/chat/stream (SSE)              • GET /api/v1/threads (SQLite WAL)          │  │
│  │  • GET /api/v1/catalog (TOON Manifests)        • POST /api/v1/webhooks/{source} (CDC)      │  │
│  │                                                                                            │  │
│  │  ┌───────────────────────────────┐     ┌────────────────────────────────────────────────┐  │  │
│  │  │   Query Scope & Filter Guard  │ ──► │  LangGraph 6-Node State Machine Loop           │  │  │
│  │  │  • Validates @scope vs filter │     │  • Turn 1: Catalog Discovery (Filtered)        │  │  │
│  │  │  • Excludes unchecked sources │     │  • Turn 2: Hybrid Search + Sibling Windowing   │  │  │
│  │  └───────────────────────────────┘     │  • Reranker (Top-15 TOON Chunks)               │  │  │
│  │                                        │  • Self-RAG Evaluator & Answer Generator       │  │  │
│  │                                        └────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────┬─────────────────────────────────────────────┘  │
│                                                 │                                                │
│                 ┌───────────────────────────────┴───────────────────────────────┐                │
│                 ▼                                                               ▼                │
│  ┌───────────────────────────────────────────────┐  ┌─────────────────────────────────────────┐  │
│  │           STORAGE & RETRIEVAL TIER            │  │        ASYNC CDC INGESTION TIER         │  │
│  │  • Qdrant (Dense Vectors + Source Pre-filter) │  │  • Webhook Receivers (GitHub/Jira/...)  │  │
│  │  • BM25Plus (Lexical Inverted Index)          │  │  • 3-Tier Differential Hash Checker     │  │
│  │  • Neo4j / InMemoryGraph (Filtered Cypher)    │  │  • Delta Chunk Embedder & BM25 Upserter │  │
│  │  • SQLite WAL (Session Checkpointers)         │  │  • Atomic Global Catalog Sync           │  │
│  └───────────────────────────────────────────────┘  └─────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Dynamic Connector Check/Uncheck Filtering & UX Design

### 1. UI/UX Interaction Design

The frontend top navigation includes an interactive **Connector Filter Bar**:
```text
Source Connectors: [ ☑ GitHub ] [ ☑ Jira ] [ ☐ Notion ] [ ☑ Dropbox ] [ ☑ Gmail ] [ ☐ Confluence ]
```
* **Default State:** All 6 connectors are checked (`enabled_connectors = ["github", "jira", "notion", "dropbox", "gmail", "confluence"]`).
* **Dynamic Toggle:** When a user unchecks a connector (e.g. unchecks `notion` and `confluence`):
  1. The frontend updates local state: `enabled_connectors = ["github", "jira", "dropbox", "gmail"]`.
  2. Every subsequent API payload sends `enabled_connectors` in [`ChatRequest`](#4-pydantic-v2-schemas-backendapischemaschatpy).
  3. All retrieval engines enforce strict pre-filtering: `source IN enabled_connectors`.

---

### 2. Conflict Handling: `@scope` Modifiers vs. Unchecked Connectors

A key architectural requirement is handling scenarios where the user explicitly types a connector directive (e.g. `@github` or `@notion`) while that connector is **unchecked** in the UI:

```
                               User Query: "@github what is the timeout value?"
                               Active Filters: [ Jira, Notion, Dropbox ] (GitHub UNCHECKED)
                                                     │
                                                     ▼
                                      [ Query Scope & Filter Validator ]
                                                     │
                                    ┌────────────────┴────────────────┐
                                    ▼                                 ▼
                     Scenario A: Single Target (@github)      Scenario B: Multi Target / @enterprise
                     • Query strictly asked for GitHub        • Broad query or multiple connectors
                     • GitHub is DISABLED in filters          • GitHub is DISABLED in filters
                                    │                                 │
                                    ▼                                 ▼
                     [ Instant Safe Refusal / Notice ]        [ Filtered Exploration + Disclaimer ]
                     "Connector 'github' is disabled in your   "Note: 'github' is disabled in your active
                     active filters. Please enable GitHub in   filters. Searching only enabled connectors:
                     the connector bar or refine your query."  [jira, notion, dropbox]..."
```

#### Rule Matrix:

| Query Scope Directive | Connector Toggle State | Agent Execution Behavior | User-Facing Disclaimer / Output |
|---|---|---|---|
| `@github <query>` | `github` **CHECKED** | Normal targeted retrieval in GitHub. | Standard answer with GitHub citations `[1]`. |
| `@github <query>` | `github` **UNCHECKED** | **Instant Refusal / Early Return:** Bypasses tool execution. Does not touch disabled data. | *"⚠️ Connector `github` is currently disabled in your active filters. Please enable it in the toolbar to search GitHub repositories and PRs."* |
| `@enterprise <query>` | `github` **UNCHECKED**, Others **CHECKED** | Normal 6-node LangGraph loop, but all tools (`hybrid_search`, `catalog_discovery`, etc.) enforce `source IN [jira, notion, dropbox, gmail, confluence]`. | Searches all enabled platforms. Answer includes inline note: *"Searched across enabled sources: Jira, Notion, Dropbox, Gmail, Confluence (GitHub excluded)."* |
| `@general <query>` | Any State | Fast-path to LLM internal knowledge (0 tool calls). | Direct answer in ~0.5s with 0 citations. |

---

## 3. Incremental Document Updates & Change Data Capture (CDC)

### 1. When to Update Documents: The Golden Rules

> [!IMPORTANT]
> **RULE 1 (Query Isolation):** Ingestion pipelines **NEVER** run synchronously during a user query. Queries strictly execute read-only lookups against the pre-indexed Qdrant, BM25, and Neo4j stores.

> [!IMPORTANT]
> **RULE 2 (Event-Driven Ingestion):** Document updates are triggered **asynchronously** via Webhooks (real-time) or background incremental delta syncs (periodic cron).

> [!IMPORTANT]
> **RULE 3 (Differential Hashing):** An existing document is only re-chunked and re-embedded if its raw text or metadata has genuinely changed. Unchanged chunks retain their existing embeddings with **zero LLM/embedding API costs**.

---

### 2. Three-Tier Differential Hashing Pipeline

```
                              Document Change Event (Webhook / Poller)
                                                │
                                                ▼
                         [ Tier 1: Metadata Timestamp / ETag Check ]
                         • Compares `doc.last_edited_time > watermark`
                         • If unmodified ──► ABORT (0ms, 0 compute)
                                                │
                                                ▼
                         [ Tier 2: Document-Level SHA-256 Checksum ]
                         • Computes `doc_hash = SHA256(raw_content + tags)`
                         • Compares against `documents_meta` SQLite table
                         • If hash matches ──► ABORT (0 embeddings computed)
                                                │
                                                ▼
                         [ Tier 3: Chunk-Level Differential Hashing ]
                         • Split document into `SmartChunk` list via `StructureAwareChunker`
                         • For each chunk: `chunk_hash = SHA256(text + section_heading)`
                         • Compare with existing chunk hashes for `document_id`:
                                                │
                        ┌───────────────────────┼───────────────────────┐
                        ▼                       ▼                       ▼
               [ Unchanged Chunks ]     [ Modified / New Chunks ]   [ Deleted Chunks ]
               • Hash identical         • New / changed hash        • In old index, not in new
               • RETAIN vector in       • Compute dense vector via  • Delete vector from Qdrant
                 Qdrant & BM25            `EmbeddingGenerator`      • Remove from BM25 index
               • 0 embedding calls!     • Upsert to Qdrant & BM25   • Remove from Neo4j edges
                                                │
                                                ▼
                               [ Atomic Catalog & Graph Sync ]
                               • Update single `CatalogEntry` in `GlobalCatalogManager`
                               • Regenerate `global_index.toon` (In-memory, ~2ms)
                               • Upsert Neo4j nodes/edges via Cypher `MERGE`
```

---

## 4. FastAPI Backend Implementation Plan

---

### 1. File & Directory Layout

```text
backend/
├── api/
│   ├── __init__.py
│   ├── main.py                     # FastAPI application factory, lifespan, CORS
│   ├── dependencies.py             # Dependency injection (Container, SecurityContext)
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── chat.py                 # ChatRequest, ChatResponse, StreamEvent models
│   │   ├── threads.py              # ThreadSummary, ThreadDetail models
│   │   ├── catalog.py              # DomainTopology, CatalogResponse models
│   │   └── ingestion.py            # IngestJob, WebhookEvent models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── chat.py                 # POST /api/v1/chat, POST /api/v1/chat/stream (SSE)
│   │   ├── threads.py              # GET /api/v1/threads, GET/DELETE /api/v1/threads/{id}
│   │   ├── catalog.py              # GET /api/v1/catalog, GET /api/v1/catalog/domains
│   │   ├── ingest.py               # POST /api/v1/ingest/document, POST /api/v1/ingest/sync
│   │   ├── webhooks.py             # POST /api/v1/webhooks/{connector}
│   │   └── health.py               # GET /healthz, GET /readyz
│   └── middleware/
│       ├── __init__.py
│       └── auth_context.py         # Extracts user_id, roles, and enabled_connectors
```

---

### 2. Pydantic V2 Schemas ([`backend/api/schemas/chat.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/api/schemas/chat.py))

```python
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(..., description="User query or instruction (supports @scope modifiers)")
    thread_id: Optional[str] = Field(None, description="Conversation thread session ID")
    user_id: str = Field("engineer@company.com", description="Caller user ID for RBAC")
    roles: List[str] = Field(
        default_factory=lambda: ["engineer", "employee"],
        description="Caller RBAC roles (expanded via RoleHierarchy)",
    )
    enabled_connectors: List[str] = Field(
        default_factory=lambda: ["github", "jira", "notion", "dropbox", "gmail", "confluence"],
        description="List of active/checked connectors. Unchecked connectors are strictly excluded.",
    )
    context_format: Literal["toon", "standard"] = Field(
        "toon",
        description="Prompt serialization format ('toon' for 76% token reduction, 'standard' for markdown)",
    )
    provider: Literal["gemini", "ollama"] = Field("gemini", description="LLM provider")
    model: Optional[str] = Field(None, description="Optional model override (e.g. gemini-2.5-flash)")


class CitationItem(BaseModel):
    id: str
    index: int
    source: str
    title: str
    url: Optional[str] = None
    path: Optional[str] = None
    tool: Optional[str] = None
    chunk_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    thread_id: str
    citations: List[CitationItem] = []
    tools_called: List[Dict[str, Any]] = []
    execution_time_seconds: float
    turns_count: int
    context_format: str
    active_connectors: List[str]
    disclaimer: Optional[str] = None
```

---

### 3. Server-Sent Events (SSE) Protocol & Flow

When the frontend connects to `POST /api/v1/chat/stream`, the backend yields continuous typed JSON events as the LangGraph state machine progresses:

```text
event: filter_status
data: {"enabled_connectors": ["github", "jira", "dropbox", "gmail"], "excluded": ["notion", "confluence"]}

event: node_start
data: {"node": "reasoner", "turn": 1}

event: tool_call
data: {"tool": "catalog_discovery", "arguments": {"query": "3DS latency", "connectors": ["github", "jira", "dropbox", "gmail"]}}

event: tool_result
data: {"tool": "catalog_discovery", "results_count": 2, "top_match": "PAY-928"}

event: node_start
data: {"node": "reranker", "turn": 2}

event: rerank_complete
data: {"candidates_evaluated": 15, "top_score": 0.95}

event: node_start
data: {"node": "evaluator"}

event: evaluation
data: {"sufficient": true, "recommended_action": "GENERATE"}

event: node_start
data: {"node": "generator"}

event: token
data: {"delta": "The "}

event: token
data: {"delta": "3DS checkout latency "}

event: token
data: {"delta": "spiked to 32 seconds [1] due to an upstream gateway timeout."}

event: citations
data: [{"id": "1", "source": "GMAIL", "title": "[POST-MORTEM] 2026-09-20 Checkout 3DS Latency Spike", "tool": "catalog_discovery"}]

event: done
data: {"thread_id": "session_17904", "execution_time_seconds": 1.28, "turns": 2}
```

---

### 4. Dependency Container with Pre-Warming ([`backend/api/dependencies.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/api/dependencies.py))

```python
import os
from functools import lru_cache
from typing import List, Optional
from fastapi import Header

from backend.agent.langgraph_planner import LangGraphAgentPlanner
from backend.agent.tools import create_default_tool_registry
from backend.ingestion.catalog_aggregator import GlobalCatalogManager
from backend.llm.factory import LLMFactory
from backend.ranking.reranker import CrossEncoderReranker
from backend.retrieval.catalog import CatalogRetriever
from backend.retrieval.entity_graph import EntityGraphRetriever
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.hybrid import HybridRetriever
from backend.security.rbac_resolver import SecurityContext
from backend.storage.bm25_index import BM25Index
from backend.storage.checkpointers import get_checkpointer
from backend.storage.qdrant_client import QdrantVectorStore


class AppContainer:
    """Thread-safe singleton container pre-warming all storage, retrievers, and models."""

    def __init__(self):
        # 1. Storage & Checkpointers
        self.qdrant = QdrantVectorStore()
        self.bm25 = BM25Index()
        self.checkpointer = get_checkpointer(mode="sqlite", db_path="./data/chat_sessions.db")

        # 2. Catalogs & Graph Retrievers
        self.catalog_manager = GlobalCatalogManager()
        self.entity_graph = EntityGraphRetriever()
        self.graph_retriever = GraphRetriever()

        # 3. Hybrid Search & Cross-Encoder Reranker
        self.hybrid_retriever = HybridRetriever(vector_store=self.qdrant, bm25_index=self.bm25)
        self.catalog_retriever = CatalogRetriever(catalog_manager=self.catalog_manager)
        self.reranker = CrossEncoderReranker()

        # 4. LLM & LangGraph Planner
        self.llm_provider = LLMFactory.create_provider(
            provider_type=os.getenv("DEFAULT_LLM_PROVIDER", "gemini"),
            model_name=os.getenv("DEFAULT_LLM_MODEL", "gemini-2.5-flash"),
        )
        self.tool_registry = create_default_tool_registry(
            hybrid_retriever=self.hybrid_retriever,
            catalog_retriever=self.catalog_retriever,
            entity_retriever=self.entity_graph,
            graph_retriever=self.graph_retriever,
        )
        self.planner = LangGraphAgentPlanner(
            llm_provider=self.llm_provider,
            tool_registry=self.tool_registry,
            reranker=self.reranker,
            checkpointer=self.checkpointer,
            catalog_manager=self.catalog_manager,
            graph_retriever=self.graph_retriever,
        )


@lru_cache()
def get_container() -> AppContainer:
    return AppContainer()


def get_security_context(
    x_user_id: str = Header("engineer@company.com"),
    x_user_roles: str = Header("engineer,employee"),
    x_enabled_connectors: Optional[str] = Header(None),
) -> SecurityContext:
    roles = [r.strip() for r in x_user_roles.split(",") if r.strip()]
    connectors = (
        [c.strip().lower() for c in x_enabled_connectors.split(",") if c.strip()]
        if x_enabled_connectors
        else ["github", "jira", "notion", "dropbox", "gmail", "confluence"]
    )
    return SecurityContext(user_id=x_user_id, roles=roles, enabled_connectors=connectors)
```

---

### 5. Chat & Streaming Router ([`backend/api/routers/chat.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/api/routers/chat.py))

```python
import json
import time
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from backend.agent.query_scope import parse_query_scope
from backend.api.dependencies import AppContainer, get_container, get_security_context
from backend.api.schemas.chat import ChatRequest, ChatResponse, CitationItem
from backend.security.rbac_resolver import SecurityContext

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat_sync(
    request: ChatRequest,
    container: AppContainer = Depends(get_container),
    sec_ctx: SecurityContext = Depends(get_security_context),
):
    start_time = time.perf_counter()
    active_connectors = [c.lower() for c in request.enabled_connectors]

    # Validate query scope against active connectors
    scope_info = parse_query_scope(request.query)
    target_connector = scope_info.get("connector")

    # If user explicitly asked for a disabled connector (e.g. @github when github is unchecked)
    if target_connector and target_connector not in active_connectors:
        return ChatResponse(
            answer=(
                f"⚠️ Connector **{target_connector.upper()}** is currently disabled in your active filters.\n\n"
                f"Please enable {target_connector.capitalize()} in the connector filter bar to search this source."
            ),
            thread_id=request.thread_id or "default",
            citations=[],
            tools_called=[],
            execution_time_seconds=0.01,
            turns_count=1,
            context_format=request.context_format,
            active_connectors=active_connectors,
            disclaimer=f"Targeted connector '{target_connector}' was disabled.",
        )

    user_context = {
        "user_id": sec_ctx.user_id,
        "roles": sec_ctx.roles,
        "enabled_connectors": active_connectors,
    }

    result = container.planner.run(
        user_query=request.query,
        user_context=user_context,
        thread_id=request.thread_id,
        context_format=request.context_format,
    )

    elapsed = round(time.perf_counter() - start_time, 2)
    citations = [CitationItem(**c) for c in result.get("citations", [])]

    return ChatResponse(
        answer=result.get("answer", ""),
        thread_id=result.get("thread_id", request.thread_id or "default"),
        citations=citations,
        tools_called=result.get("tool_calls_executed", []),
        execution_time_seconds=elapsed,
        turns_count=result.get("turns_count", 1),
        context_format=request.context_format,
        active_connectors=active_connectors,
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    container: AppContainer = Depends(get_container),
    sec_ctx: SecurityContext = Depends(get_security_context),
):
    """Streams intermediate LangGraph events, tool executions, and answer tokens via SSE."""

    async def event_generator():
        start_time = time.perf_counter()
        active_connectors = [c.lower() for c in request.enabled_connectors]

        # Scope pre-check
        scope_info = parse_query_scope(request.query)
        target_connector = scope_info.get("connector")

        if target_connector and target_connector not in active_connectors:
            disclaimer = (
                f"⚠️ Connector **{target_connector.upper()}** is disabled in your active filters.\n"
                f"Please enable it in the toolbar to search its contents."
            )
            yield f"event: token\ndata: {json.dumps({'delta': disclaimer})}\n\n"
            yield f"event: done\ndata: {json.dumps({'execution_time_seconds': 0.01})}\n\n"
            return

        yield f"event: filter_status\ndata: {json.dumps({'enabled_connectors': active_connectors})}\n\n"

        user_context = {
            "user_id": sec_ctx.user_id,
            "roles": sec_ctx.roles,
            "enabled_connectors": active_connectors,
        }

        for event in container.planner.stream_events(
            user_query=request.query,
            user_context=user_context,
            thread_id=request.thread_id,
            context_format=request.context_format,
        ):
            event_type = event.get("event_type", "message")
            event_data = json.dumps(event.get("data", {}))
            yield f"event: {event_type}\ndata: {event_data}\n\n"

        elapsed = round(time.perf_counter() - start_time, 2)
        yield f"event: done\ndata: {json.dumps({'execution_time_seconds': elapsed})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

---

## 5. Phased Implementation Roadmap

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              FASTAPI BACKEND DELIVERY PHASES                          │
│                                                                                        │
│  Phase 1: FastAPI Core Foundation & Schemas                                            │
│  ├── Pydantic V2 Models: Chat, Threads, Catalog, Ingestion                             │
│  ├── AppContainer Dependency Injection & Lifespan Pre-warming                           │
│  └── CORS, Auth Middleware, and SecurityContext resolution                             │
│                                                                                        │
│  Phase 2: Connector Filtering & Query Scope Guard                                      │
│  ├── Wire `enabled_connectors` through Qdrant, BM25, and Neo4j filters                │
│  ├── Update `CatalogRetriever` to filter entries by enabled sources                    │
│  └── Add scope conflict validator (`@github` vs unchecked GitHub)                     │
│                                                                                        │
│  Phase 3: Synchronous & SSE Streaming Chat Endpoints                                   │
│  ├── Implement `POST /api/v1/chat` (Full response with citations & metrics)           │
│  └── Implement `POST /api/v1/chat/stream` (SSE event generator with LangGraph hooks)   │
│                                                                                        │
│  Phase 4: Multi-Turn Conversation & Catalog APIs                                       │
│  ├── `GET /api/v1/threads` & `GET/DELETE /api/v1/threads/{id}`                         │
│  └── `GET /api/v1/catalog` (Domain topology & TOON manifest exports)                   │
│                                                                                        │
│  Phase 5: CDC Webhook & Async Ingestion Worker                                         │
│  ├── Three-Tier Differential Hash Detector (`sha256(chunk)`)                           │
│  ├── Webhook Handlers (`POST /api/v1/webhooks/{source}`)                               │
│  └── Background delta embedder & atomic catalog re-indexer                             │
│                                                                                        │
│  Phase 6: Automated API Test Suite & Documentation                                     │
│  ├── Pytest AsyncClient API test suite (`backend/api/tests/`)                          │
│  └── Interactive Swagger UI verification (`http://localhost:8000/docs`)                │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Verification & Acceptance Criteria

1. **Connector Filter Isolation:**
   * When `github` is unchecked in `enabled_connectors`, searching for `"3DS checkout timeout"` retrieves only Gmail post-mortems and Jira issues with **0 GitHub chunks** entering the context builder or citations.
2. **Scope Refusal Check:**
   * Sending `@github who approved PR 142` with `github` unchecked immediately returns the disclaimer without calling any retrieval tools.
3. **SSE Streaming Stability:**
   * Connecting via `EventSource` receives events in proper sequence: `filter_status` $\to$ `node_start` $\to$ `tool_call` $\to$ `tool_result` $\to$ `rerank` $\to$ `token` $\to$ `citations` $\to$ `done`.
4. **Differential Ingestion:**
   * Modifying 1 paragraph of an existing 50-chunk document computes embeddings for **only 1 chunk**, while 49 chunks are reused without re-embedding.
5. **100% Test Pass Rate:**
   * All existing 138 unit tests continue to pass cleanly in `pytest`.
