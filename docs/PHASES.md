# Enterprise Knowledge Agent — Phase Implementation & Architecture Guide

> **Document:** `docs/phases.md`  
> **Status:** Active Reference & System Audit  
> **Repository:** Enterprise Knowledge Agent  
> **Last Updated:** 2026-09-22  

---

## Executive Summary

The **Enterprise Knowledge Agent** is an autonomous, agentic RAG and knowledge intelligence platform designed to unify fragmented enterprise knowledge (GitHub, Jira, Notion, Confluence, Dropbox, Gmail) while strictly enforcing **database-level Role-Based Access Control (RBAC)**, preserving document structure via the **Open Knowledge Format (OKF v0.2)**, performing **multi-hop graph reasoning**, and ensuring answer accuracy through a **Self-RAG reflection loop** with **local cross-encoder reranking**.

---

## Master Architecture & Pipeline

```text
═══════════════════════════════════════════════════════════════════════════════════════════════
                              ENTERPRISE DATA SOURCES (MULTI-MODAL)
  GitHub (Repos, PRs, Commits, Issues)  │  Jira (Issues, ADF)  │  Notion (Pages, Databases)
  Dropbox (Word .docx, Excel .xlsx, PDF) │  Gmail (MIME Threads, Attachments)  │  Confluence
═══════════════════════════════════════════════════════════════════════════════════════════════
                                                │
                                                ▼
  ┌───────────────────────────────────────────────────────────────────────────────────────────┐
  │                           ENTERPRISE CONNECTOR & PARSER LAYER                             │
  │   • Specialized recursive parsers (ADF, MIME, Word/Excel/PDF binary blocks)               │
  │   • Normalized intermediate representation: Document & ContentBlock hierarchy             │
  └─────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                │
                                                ▼
  ┌───────────────────────────────────────────────────────────────────────────────────────────┐
  │                         OPEN KNOWLEDGE FORMAT (OKF v0.2) NORMALIZER                       │
  │   • Frontmatter: Provenance (sources, URLs), Trust Tiers, Lifecycle, Security ACLs        │
  │   • Dual export: .okf.md human-readable Markdown & .okf.json structured metadata          │
  └─────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                │
                                                ▼
  ┌───────────────────────────────────────────────────────────────────────────────────────────┐
  │                         STRUCTURE-PRESERVING CHUNKING (OKFChunker)                        │
  │   • Preserves parent titles, section breadcrumbs, sequence step chains, code blocks        │
  │   • Emits SmartChunk objects carrying complete ACL permissions & metadata payloads        │
  └─────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                │
                     ┌──────────────────────────┼──────────────────────────┐
                     ▼                          ▼                          ▼
     ┌───────────────────────────────┐ ┌───────────────────┐ ┌───────────────────────────────┐
     │      DENSE VECTOR STORE       │ │   SPARSE BM25     │ │     PROPERTY GRAPH STORE      │
     │  Local In-Process Embedder    │ │  Inverted Index   │ │  In-Memory / Neo4j Database   │
     │  (Qwen / BGE via st-transf)   │ │  (rank-bm25 JSON) │ │  (12 Parameterized Cypher Ops)│
     │  Qdrant Local Vector Engine   │ │  Exact ID Search  │ │  Developer & Hierarchy Graph  │
     └───────────────┬───────────────┘ └─────────┬─────────┘ └───────────────┬───────────────┘
                     │                           │                           │
═════════════════════╪═══════════════════════════╪═══════════════════════════╪═════════════════
                     │        AUTONOMOUS AGENT QUERY PROCESSING              │
                     │                                                       │
                     ▼                           ▼                           ▼
  ┌───────────────────────────────────────────────────────────────────────────────────────────┐
  │                    DATABASE-LEVEL RBAC PRE-FILTER TRANSLATION (PHASE 8)                   │
  │   • UserSecurityContext (user_id, roles, groups) expanded via Role/Group Hierarchy DAGs   │
  │   • QdrantFilterTranslator (Filter struct) │ BM25FilterTranslator │ CypherRBACClauseBuilder│
  │   • Zero top-k leakage & zero unauthorized data exposure before retrieval                 │
  └─────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                │
                                                ▼
  ┌───────────────────────────────────────────────────────────────────────────────────────────┐
  │                         LANGGRAPH STATE MACHINE WORKFLOW (PHASE 7)                        │
  │                                                                                           │
  │      ┌─────────────┐                                                                      │
  │      │    START    │                                                                      │
  │      └──────┬──────┘                                                                      │
  │             ▼                                                                             │
  │      ┌─────────────┐ ◄────────────────────────────────────────────────────┐               │
  │      │   reasoner  │                                                      │               │
  │      └──────┬──────┘                                                      │               │
  │             │ (tool_calls)                                                │               │
  │             ▼                                                             │               │
  │      ┌─────────────┐                                                      │               │
  │      │  tool_node  │ (Parallel multi-modal tool execution with RBAC)       │               │
  │      └──────┬──────┘                                                      │               │
  │             ▼                                                             │               │
  │      ┌─────────────┐                                                      │               │
  │      │   reranker  │ (Phase 9: Local Cross-Encoder score calibration & noise filter)     │
  │      └──────┬──────┘                                                      │               │
  │             ▼                                                             │               │
  │      ┌─────────────┐                                                      │               │
  │      │  evaluator  │ (Self-RAG Quality Control: relevance, sufficiency, gap detection)    │
  │      └──────┬──────┘                                                      │               │
  │             │                                                             │               │
  │      [ Evaluator Routing ]                                                │               │
  │       /                 \                                                 │               │
  │  (insufficient)      (sufficient / max attempts)                          │               │
  │      /                     \                                              │               │
  │     ▼                       ▼                                             │               │
  │ ┌──────────────┐      ┌─────────────┐                                     │               │
  │ │ reformulator │      │  generator  │ (Grounded answer synthesis with     │               │
  │ └──────┬───────┘      └──────┬──────┘  verifiable [1], [2] citations)     │               │
  │        │                     │                                            │               │
  │        └─────────────────────┼────────────────────────────────────────────┘               │
  │                              ▼                                                            │
  │                         ┌─────────┐                                                       │
  │                         │   END   │                                                       │
  │                         └─────────┘                                                       │
  └───────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Phase Breakdown

---

### Foundation Layer: Multi-Modal Connectors & OKF v0.2 Normalization

- **Status:** ✅ Complete & Verified
- **Goal:** Ingest multi-modal enterprise data from diverse APIs, normalize to strongly-typed intermediate documents, and export standardized OKF v0.2 Knowledge Bundles.

#### Key Implementations:
1. **Canonical Intermediate Document Models (`backend/models/document.py`, `backend/models/email.py`, `backend/models/dropbox.py`):**
   - Structured 3-layer architecture: `DocumentMetadata`, recursive `ContentBlock` tree, and atomic `to_source_attribution()`.
   - Semantic block normalization: `HEADING_1/2/3`, `PARAGRAPH`, `CODE`, `CALLOUT`, `BULLETED_LIST_ITEM`, `TO_DO`, `DATABASE` (with structured `columns`, `rows`, `properties`).
2. **Enterprise Connectors (`backend/connectors/`):**
   - **GitHub (`backend/connectors/github/`):** Pull requests, commit histories, issues, repository file trees, comments, author/reviewer metadata.
   - **Notion (`backend/connectors/notion/`):** Recursive block DFS, child databases, inline todo boards, rich property mapping (`number`, `select`, `date`, `formula`, `relation`).
   - **Gmail (`backend/connectors/email/gmail/`):** Recursive MIME parser (`text/plain`, `text/html`, base64url padding repair), message thread bundling, email headers.
   - **Dropbox (`backend/connectors/dropbox/`):** Official Dropbox SDK integration, OAuth2 auto-refresh tokens, folder traversal.
   - **Jira (`backend/connectors/jira/`):** REST v3 client, Atlassian Document Format (ADF) JSON recursive parser, issue keys, status, priority, components, assignees.
3. **Binary Document Extractors (`backend/parsers/document_extractors.py`):**
   - `pypdf`: Page-level headings and extracted text blocks.
   - `python-docx`: Heading styles, bullet lists, paragraph extraction, and Word table $\to$ `DATABASE` block conversion.
   - `openpyxl`: Multi-sheet Excel workbook extraction into structured tabular records.
4. **Open Knowledge Format (OKF v0.2) Specification (`backend/models/okf.py`):**
   - Frontmatter families: Core metadata, Provenance (`sources[]`, `usage_window`), Trust Tiers (`trust_tier`), Lifecycle (`stale_after`), and Security ACLs.
   - Bundle generator creating `.okf.md`, `.okf.json`, progressive disclosure `index.md`, and chronological `log.md`.

---

### Phase 0: LLM Provider Abstraction & Model Decoupling

- **Status:** ✅ Complete & Verified
- **Goal:** Decouple reasoning and synthesis logic from specific model vendors, enabling zero-code swapping between cloud APIs (Gemini 2.0 Flash, OpenAI) and private local runtimes (Ollama).

#### Key Implementations:
1. **Core Abstraction Layer (`backend/llm/base.py`):**
   - `LLMProvider(ABC)`: Standardized interface defining `generate(messages)` and `generate_with_tools(messages, tools)`.
   - `Message`, `MessageRole` (`SYSTEM`, `USER`, `ASSISTANT`, `TOOL_RESULT`), `ToolCall`, and `LLMResponse`.
2. **Provider Implementations (`backend/llm/`):**
   - `GeminiProvider` (`backend/llm/gemini_provider.py`): Google GenAI SDK integration with native function calling.
   - `OpenAIProvider` (`backend/llm/openai_provider.py`): OpenAI ChatCompletions API with JSON schema tool definitions.
   - `OllamaProvider` (`backend/llm/ollama_provider.py`): Local Ollama client for air-gapped enterprise deployments.
3. **Factory & Configuration (`backend/llm/factory.py`):**
   - Dynamic instantiation via `LLM_PROVIDER` environment variable (`gemini` | `ollama` | `openai`).

---

### Phase 1: Structure-Preserving Intelligent Chunking

- **Status:** ✅ Complete & Verified
- **Goal:** Transform normalized OKF documents into semantically rich, retrievable chunks while preserving hierarchical breadcrumbs, code blocks, tabular structures, and multi-step sequence chains.

#### Key Implementations:
1. **Chunk Data Model (`backend/ingestion/chunk.py`):**
   - `SmartChunk`: Encapsulates `chunk_id`, `text`, `source`, `resource_id`, `resource_type`, `title`, `section_heading`, `section_path` (breadcrumb hierarchy), `sequence` (`StepInfo`: step number, total steps, sequence ID), `code_language`, `permissions`, `url`, and timestamps.
2. **Connector-Aware Chunking Engine (`backend/ingestion/chunker.py`):**
   - **Heading-Section Splitting:** Automatically splits on Markdown headings (`#`, `##`, `###`) while tracking complete ancestral breadcrumb trails (`Section: Architecture > Backend > Database`).
   - **Atomic Table & Database Preservation:** Keeps tabular database records as indivisible chunks to prevent broken column alignments.
   - **Code Block Boundary Awareness:** Preserves complete code snippets with associated programming language tags.
   - **Sequence & Workflow Chains:** Tracks multi-step execution steps (e.g. `Step 2/5`) and links them via unique sequence identifiers.
   - **Sliding Window Fallback:** Token-bounded sliding window (512 tokens with 64-token overlap) for dense text blocks.

---

### Phase 2: Local In-Process Dense Embeddings & Vector Store

- **Status:** ✅ Complete & Verified
- **Goal:** In-process dense vector embedding generation with zero API dependencies, paired with local Qdrant vector database storage.

#### Key Implementations:
1. **Local Dense Embedder (`backend/ingestion/embedder.py`):**
   - Built on `sentence-transformers` running locally in-process (`HF_HUB_OFFLINE=1`).
   - Default models: `Qwen/Qwen3-Embedding-0.6B` or `BAAI/bge-base-en-v1.5` (768/1024 dimensions).
   - Device auto-detection (`mps` on Apple Silicon, `cuda` on GPU, `cpu`).
   - `format_chunk_for_embedding(chunk)`: Enriches text representations by prepending Document Title, Breadcrumb Hierarchy, and Step Information to maximize semantic density.
2. **Qdrant Storage Engine (`backend/storage/qdrant_client.py`):**
   - In-memory / on-disk local Qdrant vector engine (`qdrant-client`).
   - Stores dense vectors alongside rich JSON payloads containing text, titles, URLs, source identifiers, and security ACL permissions.
   - Supports cosine similarity search with database-level payload pre-filtering.

---

### Phase 3: Sparse BM25 Keyword Search & Inverted Index

- **Status:** ✅ Complete & Verified
- **Goal:** High-speed lexical search index for exact technical identifiers, error codes, ticket numbers, and code symbols where semantic embedding search fails.

#### Key Implementations:
1. **Inverted Index Engine (`backend/storage/bm25_index.py`):**
   - Implemented using `rank-bm25` (BM25Okapi).
   - Custom alphanumeric tokenizer preserving exact technical tokens (e.g. `PAY-928`, `PR#1842`, `HTTP_401`, `AuthService.charge`).
   - High-speed candidate filtering and ranking.
   - JSON serialization for instant disk persistence and cold-start loading without re-indexing.

---

### Phase 4: Core Autonomous Agent & Semantic Retrieval Tool

- **Status:** ✅ Complete & Verified
- **Goal:** Autonomous agent reasoning loop capable of selecting tools, querying the vector database, building context, and generating grounded responses with citations.

#### Key Implementations:
1. **Semantic Retriever (`backend/retrieval/semantic.py`):**
   - Vector query execution against Qdrant with optional security filtering.
2. **Context & Citation Engine (`backend/generation/context_builder.py`):**
   - Formats retrieved chunks into numbered context blocks (`[1]`, `[2]`).
   - Deduplicates overlapping sources and extracts verifiable citation cards (Title, URL, Source, Resource ID).
3. **Answer Generator (`backend/generation/answer_generator.py`):**
   - Grounded LLM answer synthesis strictly referencing provided context brackets.
4. **Tool Registry & LangChain Bridge (`backend/agent/tools.py`, `backend/agent/langchain_tools.py`):**
   - Provider-agnostic tool schemas and LangChain `@tool` adapters.

---

### Phase 5: Technical Identifier Keyword Retrieval Tool

- **Status:** ✅ Complete & Verified
- **Goal:** Dedicated keyword retrieval tool empowering the agent to resolve specific ticket IDs, PR numbers, commit hashes, and system error codes.

#### Key Implementations:
1. **Keyword Retriever (`backend/retrieval/keyword.py`):**
   - Wraps `BM25Index` into callable enterprise retrieval tool `keyword_search(query, top_k, filters)`.
   - Tool selection guidance in agent system instructions for technical identifiers vs natural language concepts.

---

### Phase 6: Property Graph Intelligence & Multi-Hop Traversal

- **Status:** ✅ Complete & Verified
- **Goal:** Graph RAG engine capable of answering multi-hop structural, relational, and developer-centric questions across knowledge assets and codebases.

#### Key Implementations:
1. **Dual Execution Engine (`backend/retrieval/entity_graph.py`):**
   - **In-Memory Graph:** Fast NetworkX / dictionary-based property graph for local development and testing.
   - **Production Neo4j Engine:** Native Cypher execution with connection pooling and parameterization.
2. **12 Native Parameterized Cypher Operations:**
   - `get_entity`: Direct lookup by ID, suffix, PR number, username, or filepath.
   - `get_neighbors`: Directional relationship matching with label and edge type filters.
   - `search_nodes`: Case-insensitive text and property matching.
   - `find_path`: Native `shortestPath()` graph algorithm.
   - `get_pr_details`: Aggregates author, reviewers, modified files, and closed issues.
   - `get_user_activity`: Developer 360 aggregation across PRs, commits, reviews, and issues.
   - `get_file_contributors`: Multi-hop code ownership and commit histories.
   - `get_commit_details`: Commit metadata, touched files, and parent PR links.
   - `get_issue_details`: Issue state, reporter, assignees, labels, and closing PRs.
   - `get_labeled_items`: Topic and label aggregation.
   - `get_team_overview`: Team membership and accessible repositories.
   - `get_repo_overview`: Maintainers, open issues, and file counts.
3. **Structural Graph Hierarchy (`backend/retrieval/graph.py`, `backend/retrieval/resource_lookup.py`):**
   - `get_children`, `get_neighbors`, `get_full_sequence`, `get_resource`, `get_related`.

---

### Phase 7: Evidence Evaluator & Self-RAG Reflection Node

- **Status:** ✅ Complete & Verified
- **Goal:** Transform the agent from a linear "retrieve $\to$ generate" pipeline into an autonomous quality-control **Self-RAG reflection state machine**.

#### Key Implementations:
1. **Evaluation Data Models (`backend/models/evaluation.py`):**
   - `ChunkRelevance`: Per-chunk score ($0.0 \dots 1.0$), relevance flag, and rationale.
   - `RecommendedAction`: `GENERATE` (sufficient), `RETRIEVE_MORE` (partial), `REFORMULATE` (off-track).
   - `EvaluationResult`: Aggregate score, sufficiency flag, `missing_information` gap list, and `recommended_tool`.
2. **Evidence Evaluator Engine (`backend/evaluation/evaluator.py`):**
   - Evaluates retrieved evidence against the active query and conversation history.
   - Detects knowledge gaps, unfulfilled constraints, and missing relationships.
   - Robust JSON code-fence parser with automatic heuristic fallback.
3. **Query Reformulator (`backend/agent/reformulator.py`):**
   - Synthesizes high-precision sub-queries targeting identified missing knowledge gaps.
   - Injects structured reflection messages (`[Self-RAG Reflection]`) into conversational state for subsequent reasoning turns.
4. **LangGraph 5-Node State Machine (`backend/agent/langgraph_planner.py`):**
   - `START` $\to$ `reasoner` $\to$ `tool_node` $\to$ `evaluator` $\to$ `generator` / `reformulator` $\to$ `reasoner`.
   - Guardrails against infinite loops via `max_retrieval_attempts` (default: 3) and `max_turns` (default: 5).

---

### Phase 8: Database-Level RBAC Resolver & Security Hierarchy

- **Status:** ✅ Complete & Verified
- **Goal:** Centralized enterprise authorization engine enforcing database-level pre-filtering across all retrieval modalities to eliminate top-$k$ leakage.

#### Key Implementations:
1. **Security Models (`backend/models/security.py`):**
   - `UserSecurityContext`: Caller identity (`user_id`), assigned roles, groups, tenant, attributes, superadmin flag, and resolved effective roles/groups.
   - `ResourcePermissions`: Access descriptors (`is_public`, `allowed_roles`, `allowed_users`, `allowed_groups`, `parent_id`).
   - `AccessDecision`: Evaluation outcome with `DecisionReason` (`PUBLIC`, `USER_WHITELIST`, `ROLE_MATCH`, `GROUP_MATCH`, `INHERITED_ALLOW`, `SUPERADMIN_BYPASS`, `DENIED`).
2. **Hierarchical Expansion DAGs (`backend/security/hierarchy.py`):**
   - `RoleHierarchy`: Transitive role DAG (e.g. `secops` $\to$ `security-admin` $\to$ `engineer` $\to$ `employee` $\to$ `guest`).
   - `GroupHierarchy`: Nested team/group DAG (e.g. `payments-core` $\to$ `payments-team` $\to$ `engineering` $\to$ `all-company`).
3. **Centralized RBAC Resolver (`backend/security/rbac_resolver.py`):**
   - Policy evaluation, context resolution, and candidate filtering with parent permission inheritance.
4. **Database-Native Pre-Filter Translators (`backend/security/translators.py`):**
   - `QdrantFilterTranslator`: Generates native Qdrant `rest.Filter` with `should` clauses.
   - `BM25FilterTranslator`: High-speed boolean candidate predicate.
   - `CypherRBACClauseBuilder`: Parameterized Cypher `WHERE` clause generator for Neo4j.
   - `GraphNodeFilter`: In-memory property graph node filter.

---

### Phase 9: Local Cross-Encoder Reranker

- **Status:** 🟡 In Implementation
- **Goal:** Local cross-encoder re-scoring of candidate chunks retrieved from Stage 1 (vector, BM25, graph) to calibrate relevance scores, reject noise, and re-order evidence before passing to the evaluator and LLM generator.

#### Planned Architecture:
1. **Cross-Encoder Scoring Engine (`backend/ranking/reranker.py`):**
   - Utilizes `sentence_transformers.CrossEncoder` with `cross-encoder/ms-marco-MiniLM-L-6-v2` or `BAAI/bge-reranker-base`.
   - Sigmoid score normalization producing calibrated $[0.0, 1.0]$ probabilities.
   - Configurable `score_threshold` for noise filtering.
   - Resilient in-process fallback cross-scorer for offline environments.
2. **LangGraph State Machine Integration (`backend/agent/langgraph_planner.py`):**
   - Dedicated `reranker` node: `tool_node` $\to$ `reranker` $\to$ `evaluator`.
   - Updates `retrieved_chunks` ordered descending by `rerank_score`.

---

### Phase 10: Hybrid Search Fusion Node (Reciprocal Rank Fusion)

- **Status:** ⚪ Planned
- **Goal:** Combine dense vector search, sparse keyword search, and graph traversal in parallel, applying Reciprocal Rank Fusion (RRF) inside LangGraph for unified multi-modal retrieval.

---

## Test & Verification Matrix

| Phase | Component | Test Suite | Verification Script | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 0** | LLM Provider Factory | Unit tests in `backend/llm/` | Provider verification | ✅ Passed |
| **Connectors** | Ingestion & OKF v0.2 | `backend/connectors/*/tests/` | `test_run_okf.py` | ✅ Passed |
| **Phase 1** | OKFChunker & SmartChunk | `backend/ingestion/tests/` | Chunking verification | ✅ Passed |
| **Phase 2** | LocalEmbedder & Qdrant | `backend/storage/tests/` | `scripts/verify_phase2.py` | ✅ Passed |
| **Phase 3** | BM25 Inverted Index | `backend/storage/tests/` | `scripts/verify_phase3.py` | ✅ Passed |
| **Phase 4** | Agent Planner & Generation | `backend/agent/tests/` | `scripts/verify_phase4.py` | ✅ Passed |
| **Phase 5** | Keyword Retrieval Tool | `backend/retrieval/tests/` | `scripts/verify_phase5.py` | ✅ Passed |
| **Phase 6** | Entity Graph & Cypher | `backend/retrieval/tests/` | `scripts/verify_phase6.py` | ✅ Passed |
| **Phase 7** | Evidence Evaluator & Self-RAG | `backend/evaluation/tests/` | `scripts/verify_phase7.py` | ✅ Passed |
| **Phase 8** | RBAC Resolver & Translators | `backend/security/tests/` (32/32) | `scripts/verify_phase8.py` | ✅ Passed |
| **Phase 9** | Local Cross-Encoder Reranker | `backend/ranking/tests/` | `scripts/verify_phase9.py` | 🟡 Pending |
| **Phase 10** | Hybrid Search Fusion (RRF) | `backend/retrieval/tests/` | `scripts/verify_phase10.py` | ⚪ Planned |

---

## Running Verification Scripts

To run any phase verification script from the repository root:

```bash
# Set offline embeddings flag
export HF_HUB_OFFLINE=1

# Phase 2: Embeddings & Vector Store
.venv/bin/python scripts/verify_phase2.py

# Phase 3: BM25 Sparse Index
.venv/bin/python scripts/verify_phase3.py

# Phase 4: Basic Agent & Grounded Generation
.venv/bin/python scripts/verify_phase4.py

# Phase 5: Keyword Identifier Retrieval
.venv/bin/python scripts/verify_phase5.py

# Phase 6: Property Graph Intelligence & Cypher
.venv/bin/python scripts/verify_phase6.py

# Phase 7: Evidence Evaluator & Self-RAG Reflection
.venv/bin/python scripts/verify_phase7.py

# Phase 8: Database-Level RBAC & Security Hierarchy
.venv/bin/python scripts/verify_phase8.py
```
