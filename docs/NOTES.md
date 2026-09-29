# This markdown file is for maintaining personal notes.

1. Handling Images, Audios, Videos and PDF files is remaining for Notion connector.
2. Handling Images, Audios, Videos and PDF files (OCR / Text extraction / Multimodal parsing) is remaining for Email/Gmail connector (currently capturing metadata only).
3. PDF, Word (.docx), and Excel (.xlsx) parsing is COMPLETED and operational! Remaining for Dropbox / Drive: media files (audio/video) and image OCR.
4. For dropbox, in word docx, the tables are collected and printed at the end together, check that later.
5. In pdfs, docx, images arent handed as of now.
6. LangGraph Multi-Tool Execution: Currently, logical multi-tool planning runs sequentially inside `_tool_node()` via a `for` loop because local Qdrant/BM25 lookups take < 5ms. In the future when integrating remote HTTP APIs or high-latency network connectors, upgrade `_tool_node()` to use `ThreadPoolExecutor` or `asyncio.gather()` for true concurrent network I/O.
7. **TOON Formatting (Token-Oriented Object Notation)**: COMPLETED and operational! (`backend/serialization/toon.py`). Features high-density bracketed header serialization `[#<idx>|C:<id>|S:<source>|D:<domain>|R:<roles>|T:<type>|K:<score>|P:<path>|U:<url>]` saving 60-75% token budget across evidence chunks, OKF concepts, and master catalog manifests (`global_index.toon`). Integrated into `ContextBuilder`, `OKFConcept`, `GlobalCatalogManager`, `AnswerGenerator`, `EvidenceEvaluator`, and `scripts/run_e2e_live.py (--context-format toon)`.
8. **Explicit Query Scope Modifiers & Directives (`@enterprise`, `@docs`, `@general`, `@llm`, `@web`, `@<connector>`)**: COMPLETED and operational! (`backend/agent/query_scope.py`). Features deterministic regex parsing and LangGraph planner fast-path routing:
   - `@enterprise` / `@docs`: Forces enterprise retrieval pipeline (`catalog_discovery`, `hybrid_search`, `resource_lookup`, `github_entity_search`).
   - `@general` / `@llm`: Bypasses all enterprise retrieval tools and answers directly from LLM base knowledge / conversation history (0 tools, 0s retrieval latency).
   - `@web`: Targets external internet / web documentation search.
   - `@github`, `@jira`, `@notion`, `@dropbox`, `@gmail`, `@confluence`: Scopes search strictly to a specific connector platform.
   - *Detailed design and architecture guide in [`docs/query_scope_modifiers.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/query_scope_modifiers.md).*
Phase 1 — Basic multi-turn
LangGraph Checkpointer
+
thread_id
+
recent-message window
Phase 2 — Proper context budgeting
Token-based trimming
+
tool-result trimming
Phase 3 — Better continuity
Conversation summary
+
query rewriting
Phase 4 — Better retrieval
Hybrid retrieval
+
reranking
+
contextual retrieval

Your existing architecture already has the foundations for this hybrid approach.

Phase 5 — Agentic context
Just-in-time retrieval
+
progressive disclosure
+
dynamic tool selection
Phase 6 — Long-running conversations
Structured notes
+
long-term memory
+
relevant historical-message retrieval
Phase 7 — Advanced architecture
Sub-agents
+
context isolation
+
artifact-based handoffs
+
context resets
