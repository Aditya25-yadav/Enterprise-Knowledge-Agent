# Enterprise Knowledge Agent - Change Log & Architecture History

This document maintains a chronological record of all architectural decisions, code changes, and iterations performed on the Enterprise Knowledge Agent codebase. Future AI models and developers should refer to this document to understand design decisions, trace system evolution, or revert/reproduce specific steps.

---

## Metadata
- **Author / Engineer:** Om Patil
- **Project:** Enterprise Knowledge Agent
- **Initial Date:** 2026-08-20
- **Timezone:** IST (+05:30)

---

## Step 1: Intermediate Document Model Design
- **Date:** 2026-08-20
- **Time:** 18:54:33 IST
- **Purpose:** Establish the intermediate structured document schema bridging raw source platform JSON (Notion, Confluence, Jira, etc.) and downstream stages (OKF normalization, Chunking, Embeddings, Knowledge Graph).

### Key Decisions & Rationale:
1. **Three-Layer Architecture**:
   - **Layer 1 (Page Metadata)**: `DocumentMetadata` retains stable identifier (`id`), title, source URL, timestamps for change tracking/sync, and hierarchy container (`parent_id`). Discards bulky raw API payloads.
   - **Layer 2 (Semantic Blocks)**: `ContentBlock` normalizes platform-specific block types into standard types (`heading_1`, `heading_2`, `paragraph`, `bulleted_list_item`, `to_do`, `code`, `callout`, `quote`). Preserves structural meaning rather than flat text concatenation.
   - **Layer 3 (Source Attribution)**: `to_source_attribution()` links any atomic text chunk back to its source page URL, page ID, and block ID for exact citations and updates.

### Files Created:
- [`backend/models/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/__init__.py)
- [`backend/models/document.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/document.py)

---

## Step 2: Notion Rich Text & Block Normalization Parser
- **Date:** 2026-08-20
- **Time:** 20:24:12 IST
- **Purpose:** Convert raw Notion JSON responses into the standardized intermediate representation.

### Key Decisions & Rationale:
1. **Rich Text Extraction**: `extract_rich_text(rich_text)` extracts all `plain_text` elements, discarding complex formatting tags while preserving readable text.
2. **Block Normalization**: `extract_block(block)` maps Notion block types to internal semantic types and extracts block IDs for citations.
3. **Dual Page Metadata Support**: `extract_page_metadata(page)` seamlessly handles both `/v1/pages/{id}` (title in properties) and `/v1/blocks/{id}` (title in `child_page`).

### Files Created:
- [`backend/connectors/notion/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/parser.py)

---

## Step 3: Separation of Retrieval vs. Normalization (Pagination & Nested Blocks)
- **Date:** 2026-08-20
- **Time:** 20:34:17 IST
- **Purpose:** Decouple network I/O from data parsing and address Notion API pagination and block hierarchy.

### Key Decisions & Rationale:
1. **Single Responsibility**: Network traversal belongs in `NotionClient`; data parsing belongs in `parser.py`.
2. **Cursor Pagination**: `fetch_all_blocks()` uses a `while has_more:` loop with `next_cursor` to ensure multi-page block streams are never truncated.
3. **Recursive DFS for `has_children == True`**: Automatically fetches child blocks for toggles, callouts, and sub-bullets and attaches them under `children: [...]` so normalization receives the complete document tree.

### Files Created / Modified:
- [`backend/connectors/notion/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/client.py) (Created)
- [`backend/connectors/notion/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/parser.py) (Updated to recursively normalize `children`)

---

## Step 4: Unified Test Data Extractor Script
- **Date:** 2026-08-20
- **Time:** 20:51:06 IST
- **Purpose:** Consolidate isolated testing scripts into an interactive, multi-option extractor for local development and inspection.

### Key Decisions & Rationale:
1. **Unified CLI Options**:
   - `block`: Fetches overall block/page metadata to `test_data/test_notion_block.json`.
   - `children`: Fetches all underlying children blocks to `test_data/test_notion_children.json`.
   - `both`: Fetches both objects simultaneously.
2. **Interactive & Non-Interactive**: Supports CLI flags (`--option`, `--page-id`) as well as an interactive terminal prompt.

### Files Created / Modified:
- [`backend/connectors/notion/tests/test_notion_fetch.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/tests/test_notion_fetch.py) (Created)
- [`backend/connectors/notion/tests/test_notion_extraction.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/tests/test_notion_extraction.py) (Updated entrypoint)

---

## Step 5: Child Database & Todo List Row Extraction
- **Date:** 2026-08-20
- **Time:** 21:02:49 IST
- **Purpose:** Extract items/tasks from Notion databases (e.g., inline "Todo List" databases) where records are stored as database rows rather than standard block children.

### Key Decisions & Rationale:
1. **Database Querying**: `fetch_database_rows(database_id)` calls `POST /v1/databases/{id}/query` with cursor pagination to retrieve all rows/pages in that database.
2. **Task & Status Normalization**: `parser.py` parses each row's properties (Title and Checkbox/Status), converting them into normalized `to_do` child items under the database block.

### Files Modified:
- [`backend/connectors/notion/tests/test_notion_fetch.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/tests/test_notion_fetch.py)
- [`backend/connectors/notion/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/parser.py)

---

## Step 6: Filtering Media, Binary Files, and Web Bookmarks
- **Date:** 2026-08-20
- **Time:** 21:22:16 IST
- **Purpose:** Prevent non-textual attachments and links from polluting the text/semantic knowledge pipeline.

### Key Decisions & Rationale:
1. **Explicit Ignored Block Registry**: Defined `IGNORED_BLOCK_TYPES` in `parser.py`:
   - `pdf`, `video`, `audio`, `file`, `bookmark`, `embed`, `image`, `link_preview`, `divider`, `unsupported`.
2. **Early Elimination**: Any block matching an ignored type returns `None` immediately, ensuring downstream chunkers and graph extractors receive clean, high-signal knowledge.

### Files Modified:
- [`backend/connectors/notion/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/parser.py)

---

## Step 7: Abstract Base Connector Contract (`BaseConnector`)
- **Date:** 2026-08-21
- **Time:** 19:06:41 IST
- **Purpose:** Establish a standardized lifecycle contract for all enterprise source connectors (Notion, Confluence, Jira, Slack, Drive).

### Key Decisions & Rationale:
1. **Unified Interface**: Defined `BaseConnector(ABC)` with mandatory abstract methods:
   - `test_connection() -> bool`: Verifies API keys and reachability.
   - `load_documents() -> List[Document]`: Returns full collection of normalized `Document` objects.
   - `load_document_by_id(doc_id) -> Optional[Document]`: Targeted single-document retrieval.
2. **Built-in Incremental Sync Fallback**: `sync_incremental(last_sync_time)` provides timestamp-based filtering over `doc.metadata.last_edited_time` for delta synchronization.

### Files Created:
- [`backend/connectors/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/__init__.py)
- [`backend/connectors/base.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/base.py)

---

## Step 8: Notion Connector Orchestrator & Typed Document Bridge
- **Date:** 2026-08-21
- **Time:** 19:07:39 IST
- **Purpose:** Implement `NotionConnector` to orchestrate `NotionClient`, `parser.py`, and `backend/models/document.py` into a unified interface conforming to `BaseConnector`.

### Key Decisions & Rationale:
1. **End-to-End Orchestrator**: `NotionConnector` provides `test_connection()`, `load_document_by_id()`, `load_documents()`, and `sync_incremental()`.
2. **Workspace Auto-Discovery**: Implemented `POST /v1/search` in `NotionClient.search_pages()` allowing automatic multi-page discovery across the entire workspace without hardcoding single page IDs.
3. **Type-Safe Document Bridge**: `dict_to_document()` maps raw normalized dictionaries into strongly-typed `Document`, `DocumentMetadata`, and recursive `ContentBlock` trees.

### Files Created / Modified:
- [`backend/connectors/notion/connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/connector.py) (Created)
- [`backend/connectors/notion/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/client.py) (Updated with search & test_connection)
- [`backend/models/document.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/document.py) (Updated with recursive markdown rendering and BlockType helper)
- [`backend/connectors/notion/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/__init__.py) (Exported NotionConnector)
- [`backend/connectors/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/__init__.py) (Exported NotionConnector)

---

## Step 9: End-to-End Live Runner & Inspector (`test_run_connector.py`)
- **Date:** 2026-08-21
- **Time:** 19:30:39 IST
- **Purpose:** Provide a dedicated CLI runner to execute the live connector against a Notion page or workspace and save both formatted Markdown and structured JSON outputs.

### Key Decisions & Rationale:
1. **Dual Output Generation**: Saves both `test_data/output_document.json` (for programmatic RAG validation) and `test_data/output_document.md` (for human inspection of rendered document).
2. **Metadata & Block Breakdown Summary**: Displays key statistics in terminal including parent containers, last edited timestamps, and counts per block type.

### Files Created:
- [`backend/connectors/notion/tests/test_run_connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/tests/test_run_connector.py)

---

## Step 10: Complete Database, Chart & Table Extraction (Schemas & Records)
- **Date:** 2026-08-21
- **Time:** 19:39:42 IST
- **Purpose:** Full structural extraction of Notion databases, charts, metric boards, and tables into structured JSON records and formatted Markdown tables.

### Key Decisions & Rationale:
1. **Rich Property Value Extraction**: Implemented `extract_property_value()` in `parser.py` handling all Notion column types: `number`, `select`, `multi_select`, `date`, `checkbox`, `status`, `formula`, `url`, `email`, `phone_number`, `people`, `relation`.
2. **Tabular Record Representation**: Each database/chart block in `ContentBlock` now contains:
   - `columns`: List of column header names.
   - `rows`: List of records `{"id": row_id, "data": {col_name: typed_value}}`.
   - `properties`: Summary statistics (`total_rows`).
3. **Markdown Table Rendering**: `Document.to_markdown()` converts database and table blocks into clean GitHub-Flavored Markdown tables (`| Col1 | Col2 |` / `| --- | --- |`).

### Files Modified:
- [`backend/connectors/notion/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/parser.py)
- [`backend/models/document.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/document.py)
- [`backend/connectors/notion/connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/connector.py)

---

## Step 11: Open Knowledge Format (OKF v0.2) Specification Implementation
- **Date:** 2026-08-21
- **Time:** 20:28:51 IST
- **Purpose:** Fully align the knowledge representation layer with the official OKF v0.2 Specification (incorporating Provenance, Trust Tiers, Lifecycle, Actor conventions, Footnote Attributions, and Knowledge Bundle index/log generators).

### Key Decisions & Rationale:
1. **Full OKF v0.2 Frontmatter Families**:
   - **Core**: `type` (required), `title`, `description`, `resource`, `tags`.
   - **Trust**: `generated: { by, at }`, `verified: [{ by, at }]` with auto-derived `trust_tier` (`unverified`, `machine-confirmed`, `human-reviewed`).
   - **Lifecycle**: `status` (`draft | stable | deprecated`), `stale_after` timestamp with `is_stale` check.
   - **Provenance**: `sources` list with join keys `id`, `resource`, credibility signals (`author`, `usage_count`, `last_modified`), and `usage_window`.
2. **Footnote-Based Attribution**: Formats citations in Markdown as `[^source-id]` matching `sources[].id`.
3. **Knowledge Bundle Management (`OKFBundle`)**: Supports multi-concept bundles, auto-generating `index.md` (progressive disclosure) and `log.md` (chronological update history).

### Files Created / Modified:
- [`backend/models/okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/okf.py) (Updated to strict OKF v0.2)
- [`backend/models/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/__init__.py) (Exported `OKFConcept`, `OKFBundle`, `OKFSource`, `OKFActor`)

---

## Step 12: End-to-End OKF v0.2 Knowledge Bundle Runner & Output Inspector
- **Date:** 2026-08-21
- **Time:** 22:13:37 IST
- **Purpose:** Build a dedicated CLI runner to ingest Notion pages and export complete OKF v0.2 Knowledge Bundles with `.okf.md`, `.okf.json`, `index.md`, and `log.md` files.

### Key Decisions & Rationale:
1. **Full Bundle Generation**: Saves all concepts into `backend/connectors/notion/test_data/okf_bundle/`.
2. **Progressive Disclosure & History**: Automatically generates `index.md` and `log.md` matching §8 and §9 of the OKF v0.2 Specification.

### Files Created:
- [`backend/connectors/notion/tests/test_run_okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/tests/test_run_okf.py)

---

## Step 13: Test Suite Documentation Guide (`tests/README.md`)
- **Date:** 2026-08-21
- **Time:** 22:18:46 IST
- **Purpose:** Provide a dedicated guide inside the `tests/` directory explaining script purposes, execution commands, and output data locations.

### Key Decisions & Rationale:
1. **Comprehensive Directory Guide**: Documents `test_run_okf.py`, `test_run_connector.py`, and `test_notion_fetch.py`.
2. **Storage Layout**: Clarifies locations for raw JSON fixtures, intermediate document outputs, and OKF v0.2 knowledge bundle artifacts.

### Files Created:
- [`backend/connectors/notion/tests/README.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/tests/README.md)

---

## Step 14: YAML Frontmatter String Escaping & Quote Sanitization
- **Date:** 2026-08-21
- **Time:** 22:29:16 IST
- **Purpose:** Ensure all YAML frontmatter strings (titles, descriptions, source titles) with embedded quotes or special characters are properly escaped with standard YAML/JSON serialization to prevent invalid YAML formatting.

### Key Decisions & Rationale:
1. **JSON-Safe Escaping**: Applied `json.dumps(..., ensure_ascii=False)` to `title`, `description`, and `source.title` fields in `to_okf_markdown()` so embedded double quotes are escaped as `\"` instead of breaking YAML parsers.
2. **Leading/Trailing Quote Trimming**: Automatically cleans leading/trailing quotes when synthesizing automatic descriptions in `from_intermediate_document()`.

### Files Modified:
- [`backend/models/okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/okf.py)

---

## Step 15: Explicit Timestamp & Delta Change Tracking (`created_at`, `updated_at`, `content_hash`)
- **Date:** 2026-08-21
- **Time:** 22:32:47 IST
- **Purpose:** Explicitly record creation and modification timestamps in OKF frontmatter and JSON metadata to support downstream delta change detection, recency scoring, and incremental synchronization.

### Key Decisions & Rationale:
1. **Explicit Frontmatter Timestamps**: Added `created_at` and `updated_at` (derived from Notion's `created_time` and `last_edited_time`) to `OKFConcept`.
2. **Multi-Layer Delta Synchronization**:
   - `content_hash`: SHA-256 hash for byte-level change comparison.
   - `updated_at` / `last_modified`: ISO timestamp for incremental synchronization and recency queries.
   - `verified[].at`: ISO timestamp recording when the sync job confirmed the document.

### Files Modified:
- [`backend/models/okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/okf.py)

---

## Step 16: Verification & Alignment with Official Notion Search OpenAPI Specification
- **Date:** 2026-08-21
- **Time:** 23:16:01 IST
- **Purpose:** Verify and upgrade `search_pages()` against the official Notion `POST /v1/search` OpenAPI 3.1.0 specification.

### Key Decisions & Rationale:
1. **OpenAPI Spec Compliance**:
   - Upgraded `search_pages()` in `NotionClient` to include the standard `sort: {"direction": "descending", "timestamp": "last_edited_time"}` so pages are discovered in order of recency.
   - Configurable `filter_object`: supports filtering by `"page"`, `"data_source"` / `"database"`, or retrieving all shared objects.
   - Strict adherence to pagination (`page_size: 100`, `start_cursor`, `next_cursor`, `has_more`).

### Files Modified:
- [`backend/connectors/notion/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/client.py)

---

## Step 17: Resilient Error Handling & Standard UUID Formatting for Notion Databases
- **Date:** 2026-08-21
- **Time:** 23:56:22 IST
- **Purpose:** Prevent `400 Bad Request` or permissions errors on individual database queries from halting the ingestion of parent pages.

### Key Decisions & Rationale:
1. **Standard Hyphenated UUIDs (`format_uuid`)**: Formats all Notion IDs to standard `8-4-4-4-12` format (`2fb3317c-c712-8165-8fcf-d305c67818ae`) required by Notion endpoints.
2. **Resilient Database Recovery**: In `fetch_database_rows()`, non-200 responses (e.g. linked database views without external permissions or empty data source blocks) log a clean notice and return `[]` instead of raising an uncaught exception, allowing the parent page and all its other content to finish loading seamlessly.

### Files Modified:
- [`backend/connectors/notion/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/client.py)

---

## Step 18: Unfiltered Workspace Discovery & Item Enumeration Logging
- **Date:** 2026-08-22
- **Time:** 00:02:46 IST
- **Purpose:** Ensure all objects (pages, wikis, root databases) are discovered without restrictive filters, and print explicit discovery summaries during test runs.

### Key Decisions & Rationale:
1. **Default `filter_object=None`**: Changed `search_pages()` default to `None` so Notion returns all shared assets across the workspace without omitting non-page types.
2. **Terminal Enumeration**: `test_run_okf.py` now prints every discovered item name, type, and ID to clearly show which pages are visible to the connection token.

### Files Modified:
- [`backend/connectors/notion/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/client.py)
- [`backend/connectors/notion/tests/test_run_okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/notion/tests/test_run_okf.py)

---

## Step 19: Untrack Test Data Directories & Comprehensive .gitignore Configuration
- **Date:** 2026-08-23
- **Time:** 22:54:19 IST
- **Purpose:** Ensure local test data, test JSON payloads, and generated OKF bundle artifacts are untracked by Git while preserved locally on disk.

### Key Decisions & Rationale:
1. **Git Index Cache Removal**: Executed `git rm -r --cached` on `backend/connectors/notion/test_data/` to remove tracked artifacts from Git staging while preserving local files on disk.
2. **Comprehensive .gitignore**: Added `test_data/`, `**/test_data/`, `*.okf.md`, `*.okf.json`, Python cache, and virtual environment patterns to `.gitignore`.

### Files Modified:
- [`.gitignore`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/.gitignore)

---

## Step 20: Environment Configuration Template (`.env.example`)
- **Date:** 2026-08-23
- **Time:** 22:59:48 IST
- **Purpose:** Provide a clean, documented template for environment variables across connectors (Notion, Confluence, Jira, GitHub, Slack), LLM providers (Gemini, OpenAI), Vector stores (Qdrant, Chroma), and Neo4j Knowledge Graph.

### Key Decisions & Rationale:
1. **Clear Modular Sections**: Structured with clear headings and links to developer setup portals for rapid onboarding.

### Files Created:
- [`.env.example`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/.env.example)

---

## Step 21: OAuth Secrets & Credential Exclusion in .gitignore
- **Date:** 2026-08-25
- **Time:** 13:33:33 IST
- **Purpose:** Secure sensitive OAuth tokens (`token.json`, `credentials.json`, `client_secret*.json`, `service_account*.json`) used by Gmail/Google OAuth across all subdirectories from being tracked in Git.

### Key Decisions & Rationale:
1. **Glob Patterns for Nested Secrets**: Added `credentials.json`, `**/credentials.json`, `token.json`, `**/token.json`, `*.token.json`, `client_secret*.json`, and `service_account*.json` to `.gitignore`.

### Files Modified:
- [`.gitignore`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/.gitignore)

---

## Step 22: Gmail Connector Setup & OAuth 2.0 Guide Documentation
- **Date:** 2026-08-25
- **Time:** 14:20:33 IST
- **Purpose:** Document step-by-step setup, configuration of Google Cloud OAuth consent, test users, `gmail.readonly` scope, and local execution flow.

### Key Decisions & Rationale:
1. **Least-Privilege Security**: Enforced `https://www.googleapis.com/auth/gmail.readonly` over full mail access.
2. **Complete Reference Guide**: Provided an in-depth reference inside `backend/connectors/email/gmail/README.md` covering Google Cloud Project creation, OAuth client setup, downloading `credentials.json`, installing libraries, and running `test_gmail.py`.

### Files Created:
- [`backend/connectors/email/gmail/README.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/README.md)

---

## Step 23: Centralized Documentation Directory (`documents.md`)
- **Date:** 2026-08-25
- **Time:** 14:27:49 IST
- **Purpose:** Provide a centralized directory of all official external documentation, API references, developer consoles, and credential creation portals across all supported connectors, AI providers, and vector/graph databases.

### Key Decisions & Rationale:
1. **Comprehensive Directory Organization**: Organized into 6 structured sections: Email/Gmail, Notion, Atlassian (Confluence/Jira), Code/Collaboration (GitHub/Slack/Drive), LLM Providers (Gemini/OpenAI/Anthropic), and Databases (Qdrant/Chroma/Neo4j).

### Files Created:
- [`documents.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/documents.md)

---

## Step 24: Silent JSON Payload Export in `test_gmail.py`
- **Date:** 2026-08-25
- **Time:** 14:31:21 IST
- **Purpose:** Redirect all Gmail API responses and raw message payloads directly into `backend/connectors/email/gmail/test_data/` instead of dumping sensitive emails into the terminal output.

### Key Decisions & Rationale:
1. **File-Based Output Redirection**:
   - `inbox_messages_list.json`: Saved raw result from `messages.list(maxResults=5)`.
   - `message_{id}.json`: Saved individual full message payloads.
   - `all_sample_messages.json`: Saved complete batch of message data.
2. **Clean Terminal Reporting**: Terminal only displays authentication status and generated output file paths.

### Files Modified:
- [`backend/connectors/email/gmail/tests/test_gmail.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/tests/test_gmail.py)

---

## Step 25: Canonical Email Document Models (`EmailDocument`, `EmailAttachment`)
- **Date:** 2026-08-25
- **Time:** 14:54:44 IST
- **Purpose:** Establish provider-independent email data models bridging raw email APIs (Gmail, Outlook, IMAP) to the system's intermediate Document format.

### Key Decisions & Rationale:
1. **Provider-Agnostic Schema**: Defined `EmailDocument` and `EmailAttachment`.
2. **Seamless Intermediate Bridge (`to_intermediate_document()`)**:
   - Maps email headers into a clean `CALLOUT` block (`✉️`).
   - Maps subject into a `HEADING_2` block.
   - Parses email body text into semantic `PARAGRAPH` blocks.
   - Attaches files into structured bullet list blocks with `📎` citations.
   - Sets `parent_type="thread"` and `parent_id=thread_id` to preserve conversation relationships in downstream Graph RAG.

### Files Created:
- [`backend/models/email.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/email.py)

---

## Step 26: Centralized Global Models Layout (`backend/models/email.py`)
- **Date:** 2026-08-25
- **Time:** 18:35:51 IST
- **Purpose:** Centralize all domain data models inside `backend/models/` for unified global model management across the entire codebase.

### Key Decisions & Rationale:
1. **Global Models Packaging**: Placed `EmailDocument` and `EmailAttachment` in `backend/models/email.py` and exported them directly from `backend/models/__init__.py`.
2. **Clean Connector Interface**: `backend/connectors/email/__init__.py` re-exports from `backend.models.email`.

### Files Created / Modified:
- [`backend/models/email.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/email.py) (Created)
- [`backend/models/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/__init__.py) (Updated exports)
- [`backend/connectors/email/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/__init__.py) (Updated imports)

---

## Step 27: Recursive Gmail MIME & Payload Parser (`parser.py`)
- **Date:** 2026-08-25
- **Time:** 18:38:10 IST
- **Purpose:** Implement recursive traversal of complex nested MIME email payloads (multipart/alternative, multipart/mixed), base64 decoding, header extraction, HTML cleanup, and conversion to canonical `EmailDocument`.

### Key Decisions & Rationale:
1. **Recursive DFS for MIME Trees**: Traverses all nested child parts in `_extract_mime_parts_recursive()` to cleanly separate `text/plain`, `text/html`, and attachments.
2. **URL-Safe Base64 Padding Repair**: `decode_base64url()` automatically normalizes `-` / `_` and adds `=` padding before decoding.
3. **HTML Sanitization Fallback**: `clean_html_to_text()` converts HTML tags to readable text when plaintext bodies are omitted.
4. **Validation Test Suite**: Created `backend/connectors/email/gmail/tests/test_parser.py` and verified 100% successful parsing across real Gmail payloads.

### Files Created:
- [`backend/connectors/email/gmail/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/parser.py)
- [`backend/connectors/email/gmail/tests/test_parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/tests/test_parser.py)

---

## Step 28: Update Pending Notes for Email Attachments (`NOTES.md`)
- **Date:** 2026-08-25
- **Time:** 18:43:07 IST
- **Purpose:** Record pending task in `NOTES.md` for multimodal media, OCR, and binary attachment parsing for the Email/Gmail connector.

### Key Decisions & Rationale:
1. **Tracking Future Media Parsers**: Documented that OCR and multimodal attachment extraction (PDFs, images, audio, video) will be implemented as specialized extractors.

### Files Modified:
- [`NOTES.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/NOTES.md)

---

## Step 29: Gmail Client & BaseConnector Implementation (`client.py` & `connector.py`)
- **Date:** 2026-08-25
- **Time:** 18:58:35 IST
- **Purpose:** Implement `GmailClient` and `GmailConnector` conforming to `BaseConnector` for connection verification, full message ingestion, single email lookup, and incremental synchronization.

### Key Decisions & Rationale:
1. **BaseConnector Conformance**: Implemented `test_connection()`, `load_documents()`, `load_document_by_id()`, and `sync_incremental()`.
2. **Batch Retrieval & Paging**: `fetch_messages_batch()` in `GmailClient` automatically paginates through `users.messages.list` and retrieves full MIME payloads via `users.messages.get`.
3. **Live Verification**: Created `backend/connectors/email/gmail/tests/test_run_connector.py` and successfully tested live inbox ingestion with intermediate Document generation and Markdown rendering.

### Files Created / Modified:
- [`backend/connectors/email/gmail/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/client.py) (Created)
- [`backend/connectors/email/gmail/connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/connector.py) (Created)
- [`backend/connectors/email/gmail/tests/test_run_connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/tests/test_run_connector.py) (Created)
- [`backend/connectors/email/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/__init__.py) (Updated exports)
- [`backend/connectors/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/__init__.py) (Updated exports)

---

## Step 30: End-to-End OKF v0.2 Knowledge Bundle Runner for Gmail (`test_run_okf.py`)
- **Date:** 2026-08-25
- **Time:** 20:12:41 IST
- **Purpose:** Implement end-to-end OKF v0.2 Knowledge Bundle generation for Gmail, saving individual `.okf.md`, `.okf.json`, progressive disclosure `index.md`, and chronological `log.md`.

### Key Decisions & Rationale:
1. **Dynamic Platform Provenance**: Upgraded `from_intermediate_document()` in `backend/models/okf.py` to dynamically attribute provenance sources (`gmail://messages/{id}`), verification processes (`process:gmail-sync`), and titles.
2. **Complete Knowledge Bundle Export**: Generates full OKF v0.2 bundles inside `backend/connectors/email/gmail/test_data/okf_bundle/`.
3. **Test Suite Documentation**: Created `backend/connectors/email/gmail/tests/README.md` documenting all test runners and data directories.

### Files Created / Modified:
- [`backend/connectors/email/gmail/tests/test_run_okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/tests/test_run_okf.py) (Created)
- [`backend/connectors/email/gmail/tests/README.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/email/gmail/tests/README.md) (Created)
- [`backend/models/okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/okf.py) (Updated platform provenance)

---

## Step 31: Comprehensive Test Suite Documentation (`backend/connectors/email/gmail/tests/README.md`)
- **Date:** 2026-08-25
- **Time:** 20:18:32 IST
- **Purpose:** Provide an in-depth, file-by-file testing reference and command cheat sheet for all Gmail connector test runners, MIME validators, payload extractors, and output data directories.

### Key Decisions & Rationale:
1. **Complete File Matrix**: Documented all 4 test scripts (`test_run_okf.py`, `test_run_connector.py`, `test_parser.py`, `test_gmail.py`) with input arguments, purpose, and generated output files.
2. **Directory Layout Guide**: Outlined the complete storage layout under `backend/connectors/email/gmail/test_data/`.

---

## Step 32: System-Wide Documentation Synchronization & Formatting (`DOCUMENTS.md`)
- **Date:** 2026-09-03
- **Time:** 18:57:30 IST
- **Purpose:** Restructure and format `DOCUMENTS.md` with complete, properly aligned Markdown tables, GitHub alert blocks, a table of contents, and all official developer portal/API reference links.

### Key Decisions & Rationale:
1. **Clear Navigation & Structure**: Added an indexed Table of Contents covering 9 distinct categories:
   - Email (Gmail & Workspace), Cloud Storage (Dropbox & Drive), Notion, Atlassian (Confluence & Jira), Code (GitHub), Collaboration (Slack), AI/LLMs (Gemini, OpenAI, Claude), Databases (Qdrant, ChromaDB, Neo4j), and Parsing Engines (PyPDF, python-docx, openpyxl).
2. **Standardized Formatting**: Enforced clean GitHub-Flavored Markdown tables with explicit columns (`Resource`, `Description`, `Official URL`) and clickable external links.
3. **Security & Implementation Notes**: Integrated GitHub callout blocks (`> [!NOTE]`, `> [!TIP]`) highlighting least-privilege scopes and token lifecycle rules.

### Files Modified:
- [`DOCUMENTS.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/DOCUMENTS.md)

---

## Step 33: Comprehensive Markdown Representation & Formatting (`RESEARCH.md`)
- **Date:** 2026-09-03
- **Time:** 23:04:45 IST
- **Purpose:** Format `RESEARCH.md` into clean GitHub-Flavored Markdown without altering section ordering, sequence, or substantive content.

### Key Decisions & Rationale:
1. **Diagram & Schema Encapsulation**: Enclosed all unformatted ASCII flowcharts, tree structures, and architecture diagrams in explicit ````text ... ```` code fences with complete root and footer boundaries.
2. **Payload Syntax Highlighting**: Wrapped raw JSON response payloads and data dictionaries in ````json ... ```` fences, and Python client API calls in ````python ... ```` fences.
3. **Tabular Data Conversion**: Formatted pseudo-tables (e.g. metadata sync tables, question-type vs retrieval mechanism matrix) into aligned GitHub-Flavored Markdown tables.
4. **Header Separation**: Fixed inline accidental concatenations (e.g. `me → authenticated Gmail account 7. What are query parameters?`) into clean standalone markdown section headings (`## 7. What are query parameters?`).
5. **Content Integrity**: Verified 100% balanced code fences (370 total) and verified that all 105 headings and core content blocks maintain the exact chronological research sequence.

### Files Modified:
- [`RESEARCH.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/RESEARCH.md)

---

## Step 32: Official Dropbox Python SDK Integration & Connectivity Verification
- **Date:** 2026-09-01
- **Time:** 13:30:25 IST
- **Purpose:** Install and integrate the official `dropbox` Python SDK (v12.2.1) and implement connectivity test via `test_dropbox_auth.py`.

### Key Decisions & Rationale:
1. **Official SDK Integration**: Installed `dropbox==12.2.1` into virtual environment (`.venv`).
2. **Authentication Verification**: Created `backend/connectors/dropbox/tests/test_dropbox_auth.py` verifying `users_get_current_account()` and folder listing.
3. **Scope Diagnosis**: Verified live connection for account `Om Patil` (`ompatilseetara@gmail.com`) and identified missing `files.metadata.read` scope on the generated access token.

### Files Created:
- [`backend/connectors/dropbox/tests/test_dropbox_auth.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/tests/test_dropbox_auth.py)

---

## Step 33: Dropbox SDK Refactor & OKF v0.2 Knowledge Bundle Generation
- **Date:** 2026-09-01
- **Time:** 23:44:59 IST
- **Purpose:** Refactor `DropboxClient` using the official `dropbox.Dropbox` Python SDK with auto-refresh token support, verify `BaseConnector` lifecycle, and generate live OKF v0.2 Knowledge Bundles.

### Key Decisions & Rationale:
1. **SDK-Powered Client**: Upgraded `DropboxClient` to utilize `dropbox.Dropbox` with `app_key`, `app_secret`, and `oauth2_refresh_token` for automatic background token lifecycle management.
2. **Interactive Refresh Token Helper**: Created `backend/connectors/dropbox/tests/get_refresh_token.py` using `DropboxOAuth2FlowNoRedirect` with `token_access_type='offline'`.
3. **Live Ingestion Verification**: Ran `test_run_connector.py` and `test_run_okf.py` live against Dropbox account, ingesting 9 documents and generating `.okf.md`, `index.md`, and `log.md` files in `backend/connectors/dropbox/test_data/okf_bundle/`.

### Files Created / Modified:
- [`backend/connectors/dropbox/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/client.py) (Upgraded to official SDK)
- [`backend/connectors/dropbox/tests/get_refresh_token.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/tests/get_refresh_token.py) (Created)
- [`backend/connectors/dropbox/tests/test_run_connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/tests/test_run_connector.py) (Updated)
- [`backend/connectors/dropbox/tests/test_run_okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/tests/test_run_okf.py) (Updated)

---

## Step 34: Dropbox Test Suite Documentation Update (`backend/connectors/dropbox/tests/README.md`)
- **Date:** 2026-09-01
- **Time:** 23:47:01 IST
- **Purpose:** Update the Dropbox test suite documentation to reflect the official `dropbox` SDK usage, OAuth 2.0 refresh token helper, and test execution commands.

### Key Decisions & Rationale:
1. **Documented SDK Test Scripts**: Added `test_dropbox_auth.py` and `get_refresh_token.py` to the testing matrix.
2. **Updated Execution Reference**: Standardized CLI commands and output locations for `.okf.md` bundles.

### Files Modified:
- [`backend/connectors/dropbox/tests/README.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/tests/README.md)

---

## Step 35: System-Wide Documentation Synchronization (`DOCUMENTS.md`, `NOTES.md`, `developer_setup.md`)
- **Date:** 2026-09-02
- **Time:** 00:07:30 IST
- **Purpose:** Synchronize all documentation markdown files to ensure Dropbox connector developer URLs, pending OCR/binary notes, architecture diagrams, and environment variable references are 100% updated and consistent across the repository.

### Key Decisions & Rationale:
1. **`DOCUMENTS.md`**: Added Section 3 for Dropbox (App Console, OAuth 2.0 guide, Python SDK docs, API HTTP reference, and API Explorer).
2. **`NOTES.md`**: Added Note #3 for handling future binary attachments (`.docx`, `.xlsx`, `.pdf`) and proprietary `.paper` documents for Dropbox.
3. **`developer_setup.md`**: Updated data source diagrams, added dedicated Section 10 for Dropbox connector setup, and updated `.env` templates with `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN`, and `DROPBOX_ACCESS_TOKEN`.

### Files Modified:
- [`DOCUMENTS.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/DOCUMENTS.md)
- [`NOTES.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/NOTES.md)
- [`developer_setup.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/developer_setup.md)

---

## Step 36: Binary Document Extractors Setup & Test Suite Cleanup
- **Date:** 2026-09-02
- **Time:** 09:32:44 IST
- **Purpose:** Remove obsolete raw HTTP fetch test scripts from Dropbox test suite and install enterprise binary document parsing dependencies (`pypdf`, `python-docx`, `openpyxl`).

### Key Decisions & Rationale:
1. **Test Suite Hygiene**: Deleted `test_dropbox_fetch.py` and `test_dropbox_extraction.py` as `test_dropbox_auth.py` and `test_run_connector.py` fully supersede raw HTTP calls.
2. **Binary Parsing Dependencies**: Installed `pypdf==6.16.2` (PDF text extraction), `python-docx==1.2.0` (Word document parsing), and `openpyxl==3.1.5` (Excel workbook/table extraction).
3. **Requirements Synchronization**: Updated `requirements.txt`.

### Files Modified / Deleted:
- `backend/connectors/dropbox/tests/test_dropbox_fetch.py` (Deleted)
- `backend/connectors/dropbox/tests/test_dropbox_extraction.py` (Deleted)
- [`requirements.txt`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/requirements.txt) (Updated)

---

## Step 37: Reusable Binary Document Extractors & Live Dropbox Ingestion
- **Date:** 2026-09-02
- **Time:** 09:51:37 IST
- **Purpose:** Implement universal binary document parsers for PDF, Word (.docx), and Excel (.xlsx), integrate them into the Dropbox connector pipeline, and verify live on user's real documents.

### Key Decisions & Rationale:
1. **Universal Extractors Package (`backend/parsers/`)**:
   - `extract_pdf_blocks`: Extracts text per page into page headings and paragraphs using `pypdf`.
   - `extract_docx_blocks`: Preserves document styles (Heading 1/2/3, Bullet Lists, Paragraphs) and converts Word tables into structured `ContentBlock(type=BlockType.DATABASE)` records with column headers using `python-docx`.
   - `extract_xlsx_blocks`: Iterates across workbook worksheets and converts non-empty rows into structured `ContentBlock(type=BlockType.DATABASE)` records using `openpyxl`.
2. **Connector & Model Integration**:
   - Upgraded `DropboxClient.download_file()` to return `raw_bytes` along with metadata.
   - Upgraded `DropboxFile` and `normalize_file_document()` to accept `raw_bytes` and seamlessly invoke `extract_document_blocks()`.
3. **Live Verification & Full OKF Bundle Generation**:
   - Ingested 19 items live from Dropbox account, including `complex_dummy_test_document.docx` (all 7 KPI/Trend/Risk tables & sections extracted) and `leetcode 75 questions (neetcode on yt).xlsx` (all 75 problem rows & notes extracted).
   - Generated 19 complete `.okf.md` and `.okf.json` concepts in `backend/connectors/dropbox/test_data/okf_bundle/`.

### Files Created / Modified:
- [`backend/parsers/document_extractors.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/parsers/document_extractors.py) (Created)
- [`backend/parsers/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/parsers/__init__.py) (Created)
- [`backend/models/dropbox.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/dropbox.py) (Updated)
- [`backend/connectors/dropbox/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/client.py) (Updated)
- [`backend/connectors/dropbox/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/parser.py) (Updated)
- [`backend/connectors/dropbox/connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/connector.py) (Updated)
- [`backend/connectors/dropbox/tests/test_run_connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/dropbox/tests/test_run_connector.py) (Updated)
- [`NOTES.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/NOTES.md) (Updated)

---

## Step 38: Confluence Connector (Client, Storage-Format Parser, OKF Bundle)
- **Date:** 2026-09-05
- **Time:** 10:59:01 IST
- **Purpose:** Add Atlassian Confluence as a full enterprise knowledge source with a REST v1 client, Storage-Format (XHTML) → semantic block normalization, a `BaseConnector`-conformant orchestrator, and end-to-end OKF v0.2 Knowledge Bundle generation.

### Key Decisions & Rationale:
1. **Confluence REST v1 + Basic Auth**: `ConfluenceClient` uses `CONFLUENCE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_TOKEN`; automatically appends `/wiki` when the base URL lacks it (Cloud default), verifies via `GET /rest/api/user/current`, and paginates with `_links.next` URLs (no cursors).
2. **Storage-Format DOM → Semantic Blocks**: `parser.py` builds a lightweight `html.parser` element tree that preserves **document order** (text segments interleaved with child nodes). Converts headings, paragraphs, bullet/numbered lists with nested children, code blocks, blockquotes, and dividers; tables become structured `DATABASE` blocks with columns + rows; inline links render as `Text (https://...)`; images/media/macros are ignored via an explicit ignore-list; `ac:structured-macro` panels flatten to inner text.
3. **Dual Discovery Paths**: `load_documents()` supports `space_keys` (specific spaces) or auto-discovery of all spaces; `--include-spaces` optionally emits space-level overview documents.
4. **Robust URL Joining**: `_join_page_url()` prevents the double-`/wiki` bug when both `_links.base` and `_links.webui` carry the `/wiki` prefix.
5. **No New Dependencies**: Everything uses `requests` + stdlib `html.parser`; `requirements.txt` unchanged.

### Files Created / Modified:
- [`backend/connectors/confluence/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/client.py) (Created)
- [`backend/connectors/confluence/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/parser.py) (Created)
- [`backend/connectors/confluence/connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/connector.py) (Created)
- [`backend/connectors/confluence/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/__init__.py) (Created)
- [`backend/connectors/confluence/tests/test_run_connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/tests/test_run_connector.py) (Created)
- [`backend/connectors/confluence/tests/test_run_okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/tests/test_run_okf.py) (Created)
- [`backend/connectors/confluence/tests/README.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/tests/README.md) (Created)
- [`backend/connectors/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/__init__.py) (Updated exports)
- [`DOCUMENTS.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/DOCUMENTS.md) (Updated Atlassian API references)
- [`RESEARCH.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/RESEARCH.md) (Updated Confluence research section)

---

## Step 39: Jira Connector (Client, ADF Parser, OKF Bundle)
- **Date:** 2026-09-05
- **Time:** 10:59:01 IST
- **Purpose:** Add Atlassian Jira as a full enterprise knowledge source with a REST v3 client, Atlassian Document Format (ADF) → semantic block normalization, a `BaseConnector`-conformant orchestrator, and end-to-end OKF v0.2 Knowledge Bundle generation.

### Key Decisions & Rationale:
1. **Jira REST v3 + Basic Auth**: `JiraClient` uses `JIRA_URL`, `JIRA_USERNAME`, `JIRA_API_TOKEN`; verifies via `GET /rest/api/3/myself`, discovers projects via `GET /rest/api/3/project`, and paginates issue search with JQL `startAt`/`maxResults` windows (resumable & idempotent).
2. **ADF JSON → Semantic Blocks**: `parser.py` walks the recursive ADF node tree — text nodes fold with `strong`/`em`/`code` marks, mentions render as `@handle`, nested list items nest as `children`, code blocks capture `language`, tables become structured `DATABASE` blocks, and `rule` nodes become dividers. Unknown node types are skipped gracefully so one-off ADF nodes never crash ingestion.
3. **Dual Discovery Paths**: `load_documents()` supports `project_keys` (specific projects), auto-discovery of all projects, an optional JQL filter, and `--include-projects` for project-level overview documents.
4. **Rich Issue Metadata**: extracts issue type, status, priority, assignee, reporter, labels, components, plus created/updated timestamps into `extra` for recency scoring and dashboards.
5. **No New Dependencies**: Everything uses `requests` + stdlib; `requirements.txt` unchanged.

### Files Created / Modified:
- [`backend/connectors/jira/client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/jira/client.py) (Created)
- [`backend/connectors/jira/parser.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/jira/parser.py) (Created)
- [`backend/connectors/jira/connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/jira/connector.py) (Created)
- [`backend/connectors/jira/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/jira/__init__.py) (Created)
- [`backend/connectors/jira/tests/test_run_connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/jira/tests/test_run_connector.py) (Created)
- [`backend/connectors/jira/tests/test_run_okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/jira/tests/test_run_okf.py) (Created)
- [`backend/connectors/jira/tests/README.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/jira/tests/README.md) (Created)
- [`backend/connectors/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/__init__.py) (Updated exports)
- [`DOCUMENTS.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/DOCUMENTS.md) (Updated Atlassian API references)
- [`RESEARCH.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/RESEARCH.md) (Updated Jira research section)

---

## Step 40: Jira Live Connectivity Verification & `JIRA_URL` Pitfall Documentation
- **Date:** 2026-09-05
- **Time:** 13:13:24 IST
- **Purpose:** Verify the Jira connector end-to-end against a live site and document the base-URL pitfall that silently breaks every API payload.

### Key Decisions & Rationale:
1. **Diagnosis**: A first live run returned HTTP 401 (`Client must be authenticated`) because the API token had not been refreshed. After token refresh the connection test passed, but `get_current_user()` / `list_projects()` failed with `Expecting value: line 1 column 1` — the endpoint returned the Atlassian Home HTML portal (HTTP 200, `content-type: text/html`) instead of REST JSON.
2. **Root Cause**: `JIRA_URL` had been set to `https://home.atlassian.com/` (the generic account portal) instead of the actual Jira instance URL. Because the portal answers with an HTML page and status 200, `test_connection()` (which only checks the status code) can pass while every `response.json()` downstream throws.
3. **Hardening**: Documented the constraint in `.env.example`, the Jira test-suite `README.md` guidance, and this changelog: `JIRA_URL` must point to the exact site the API token was issued for (e.g. `https://your-org.atlassian.net`), not `home.atlassian.com` or any other Atlassian product-less host.

### Files Modified:
- [`.env.example`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/.env.example) (Jira URL comments)
- [`DOCUMENTS.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/DOCUMENTS.md) (Jira connection-check references)
- [`RESEARCH.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/RESEARCH.md) (Jira credential/URL checklist)

---

## Step 37: LLMProvider Abstraction & Ingestion Chunker (`backend/llm/` & `backend/ingestion/`)
- **Date:** 2026-09-09
- **Time:** 11:24 IST
- **Purpose:** Implemented Phase 0 (Provider-agnostic LLM interface with Gemini & Ollama implementations) and Phase 1 (Structure-preserving and connector-aware OKF Chunker).

### Key Decisions & Rationale:
1. **`LLMProvider` Base Abstraction (`backend/llm/base.py`)**:
   - Provider-agnostic message and tool schema definitions (`Message`, `ToolDefinition`, `ToolCall`, `LLMResponse`).
   - Clean decoupling: Agent and generation layers depend solely on `LLMProvider`, allowing seamless zero-code switching between Gemini API (cloud) and Ollama (local private deployment).
2. **`GeminiProvider` (`backend/llm/gemini_provider.py`)**:
   - Uses the official `google-genai` SDK with Gemini 2.0 Flash.
   - Converts standard JSON Schema directly to `types.FunctionDeclaration` without custom schema conversions.
   - Manual turn handling with `automatic_function_calling=False` so agent planner maintains full determinism.
3. **`OllamaProvider` (`backend/llm/ollama_provider.py`)**:
   - Local LLM provider supporting tool calling on models like `llama3.1` and `qwen2.5`.
4. **`LLMProvider` Factory (`backend/llm/factory.py`)**:
   - `get_llm_provider()` dynamically instantiates provider based on `LLM_PROVIDER` environment variable.
5. **`Chunk` Data Model (`backend/ingestion/chunk.py`)**:
   - Holds the chunk text, source, resource ID, section heading, indices, and full RBAC permissions dictionary.
   - Includes `.to_qdrant_payload()` for vector indexing with pre-filter keys (`is_public`, `allowed_roles`, `allowed_users`, `allowed_groups`).
6. **`OKFChunker` (`backend/ingestion/chunker.py`)**:
   - Markdown header-based semantic boundary splitting.
   - Preserves markdown tables and code fences without mid-block truncations.
   - Size-bounded windowing with configurable overlap.

### Files Created:
- [`backend/llm/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/llm/__init__.py)
- [`backend/llm/base.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/llm/base.py)
- [`backend/llm/gemini_provider.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/llm/gemini_provider.py)
- [`backend/llm/ollama_provider.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/llm/ollama_provider.py)
- [`backend/llm/factory.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/llm/factory.py)
- [`backend/ingestion/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/__init__.py)
- [`backend/ingestion/chunk.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/chunk.py)
- [`backend/ingestion/chunker.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/chunker.py)

### Next Step:
- Phase 2: Local Embeddings (`sentence-transformers`) + Qdrant Vector Storage Client (`backend/storage/qdrant_client.py`) + Ingestion Pipeline.

## Step 38: Updated `.env.example` with Local/Cloud Hybrid Configuration
- **Date:** 2026-09-09
- **Time:** 12:58 IST
- **Purpose:** Comprehensive update to `.env.example` documenting all configuration keys for the swappable LLM provider layer, local sentence-transformers embeddings, local cross-encoder reranker, and Qdrant local/server modes.

### Key Sections Updated:
1. **LLM Provider**: `LLM_PROVIDER` (`gemini` / `ollama`), `GEMINI_API_KEY`, `GEMINI_MODEL`, `OLLAMA_MODEL`, `OLLAMA_BASE_URL`.
2. **Local Embeddings & Reranker**: `EMBEDDING_PROVIDER=local`, `EMBEDDING_MODEL=BAAI/bge-base-en-v1.5`, `RERANKER_PROVIDER=local`, `RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2`.
3. **Vector Database**: `QDRANT_MODE` (`local` / `server`), `QDRANT_PATH`, `QDRANT_URL`, `QDRANT_COLLECTION`.
4. **Knowledge Graph**: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`.
5. **Connectors**: GitHub, Notion, Dropbox, Confluence, Jira, Slack, Drive.

### Files Modified:
- [`.env.example`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/.env.example)

## Step 39: Added `extra_metadata` to OKFConcept & Ingestion Chunker
- **Date:** 2026-09-09
- **Time:** 14:03 IST
- **Purpose:** Propagated connector-specific properties (e.g., GitHub PR numbers/branches, Gmail sender/recipients/thread IDs, Jira keys/sprints, Dropbox file sizes/revs) from `DocumentMetadata.extra` through `OKFConcept.extra_metadata` into `Chunk.extra_metadata` and the Qdrant payload dictionary.

### Key Changes:
1. **`OKFConcept` (`backend/models/okf.py`)**:
   - Added `extra_metadata: Dict[str, Any] = field(default_factory=dict)`.
   - Updated `to_okf_markdown()` to render `extra_metadata` cleanly in YAML frontmatter.
   - Updated `to_dict()` to include `extra_metadata` for JSON serialization.
   - Updated `from_intermediate_document()` to automatically inherit `doc.metadata.extra`.
2. **`OKFChunker` (`backend/ingestion/chunker.py`)**:
   - Merges `concept.extra_metadata` into each output `Chunk.extra_metadata`.
   - Ensures payload metadata is ready for Qdrant filtering queries.

### Files Modified:
- [`backend/models/okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/models/okf.py)
- [`backend/ingestion/chunker.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/chunker.py)

### Next Step:
- Phase 2: Local Embeddings (`sentence-transformers`) + Qdrant Vector Storage Client (`backend/storage/qdrant_client.py`) + Ingestion Pipeline (`backend/ingestion/pipeline.py`).

## Step 40: Documented Structure-Aware & Hierarchical Chunking in `RESEARCH.md`
- **Date:** 2026-09-09
- **Time:** 15:33 IST
- **Purpose:** Appended the complete Structure-Aware, Semantic & Hierarchical Chunking Architecture section to `RESEARCH.md`, detailing parent-child chunking, sequence/procedure preservation, neighboring context expansion, connector-specific strategies, and the `SmartChunk` data contract.

### Files Modified:
- [`RESEARCH.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/RESEARCH.md)

## Step 41: Upgraded Structure-Aware & Hierarchical Chunker (`backend/ingestion/`)
- **Date:** 2026-09-10
- **Time:** 22:32 IST
- **Purpose:** Upgraded `Chunk` to `SmartChunk` with complete hierarchical parent-child relationships, bidirectional sibling pointers (`prev_chunk_id`, `next_chunk_id`), structural breadcrumb paths (`section_path`), content-type taxonomy (`ContentType`), and multi-step procedure sequence tracking (`SequenceInfo`).

### Key Enhancements:
1. **`SmartChunk` & Supporting Types (`backend/ingestion/chunk.py`)**:
   - `ContentType`: `DOCUMENT_SECTION`, `PROCEDURE_STEP`, `CONVERSATION_THREAD`, `CODE_SYMBOL`, `TABLE_RECORD`, `GENERAL`.
   - `SequenceInfo`: Tracks `sequence_id`, `step`, `total_steps`, and `step_title` for runbooks and workflows.
   - `SmartChunk`: Carries `parent_id`, `parent_text`, `prev_chunk_id`, `next_chunk_id`, `section_path`, `sequence`, and RBAC permissions.
   - Backward compatibility: `Chunk = SmartChunk`.
2. **`SmartOKFChunker` Strategy Dispatchers (`backend/ingestion/chunker.py`)**:
   - **Hierarchical Document Strategy**: Tracks heading stack for breadcrumbs, skips empty headers, and embeds section paths.
   - **Procedure Sequence Strategy**: Automatically detects ordered workflows and step headings, populating `SequenceInfo`.
   - **Conversation Thread Strategy**: Chunks email and chat turns retaining conversation context.
   - **Code Symbol Strategy**: Splits code along function and class definitions.
   - **Tabular Strategy**: Detects Markdown tables and preserves structured table records.
   - **Second-Pass Sibling Linkage**: Stitches `prev_chunk_id` and `next_chunk_id` across all resulting chunks.
3. **Comprehensive Test Suite (`backend/ingestion/tests/test_smart_chunker.py`)**:
   - Validates breadcrumb path hierarchy, procedure step extraction, sibling links, code symbol parsing, and table classification.

### Files Modified/Created:
- [`backend/ingestion/chunk.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/chunk.py)
- [`backend/ingestion/chunker.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/chunker.py)
- [`backend/ingestion/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/__init__.py)
- [`backend/ingestion/tests/test_smart_chunker.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/tests/test_smart_chunker.py)

### Next Step:
- Phase 2: Local Embeddings (`sentence-transformers`) + Qdrant Vector Storage Client (`backend/storage/qdrant_client.py`) + Ingestion Pipeline (`backend/ingestion/pipeline.py`).

## Step 42: Local Embeddings (`sentence-transformers`) + Qdrant Storage + Ingestion Pipeline (Phase 2)
- **Date:** 2026-09-10
- **Time:** 22:37 IST
- **Purpose:** Implemented Phase 2 of the Agentic RAG pipeline: local in-process embedding generation using `BAAI/bge-base-en-v1.5` (768 dimensions, zero API cost), Qdrant vector database client with strict RBAC pre-filtering, and the master `IngestionPipeline` tying chunks to vector storage.

### Key Components Built:
1. **`LocalEmbedder` (`backend/ingestion/embedder.py`)**:
   - In-process local embedding model (`BAAI/bge-base-en-v1.5`) via `sentence-transformers==6.0.1`.
   - Batch encoding support (`embed_texts`, `embed_chunks`) returning normalized 768-dimensional float vectors.
   - Works 100% offline with zero external API calls.
2. **`QdrantVectorStore` (`backend/storage/qdrant_client.py`)**:
   - Manages local embedded storage (`./data/qdrant_storage`), in-memory testing (`:memory:`), and remote Qdrant servers.
   - Idempotent upserting using deterministic UUID5 point IDs derived from `chunk_id`.
   - **Strict RBAC Pre-filtering**: Enforces `(is_public == True) OR (allowed_roles IN user_roles) OR (allowed_users IN user_id) OR (allowed_groups IN user_groups)` at the database index layer before vector similarity scoring.
3. **`IngestionPipeline` (`backend/ingestion/pipeline.py`)**:
   - Master orchestrator: `Document / OKFConcept -> SmartOKFChunker -> LocalEmbedder -> QdrantVectorStore`.
   - Supports single concept ingestion, document lists, and complete `OKFBundle` ingestion.
4. **End-to-End Test Suite (`backend/ingestion/tests/test_pipeline.py`)**:
   - Verified 768-dimensional vector generation.
   - Validated that users with role `engineer` retrieve restricted internal secrets, while `guest` users are strictly pre-filtered out, while public company documents remain globally accessible.

### Files Created/Modified:
- [`backend/ingestion/embedder.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/embedder.py)
- [`backend/storage/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/__init__.py)
- [`backend/storage/qdrant_client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/qdrant_client.py)
- [`backend/ingestion/pipeline.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/pipeline.py)
- [`backend/ingestion/tests/test_pipeline.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/tests/test_pipeline.py)
- [`requirements.txt`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/requirements.txt)

### Next Step:
- Phase 3: BM25 Keyword Search Index (`backend/storage/bm25_index.py`) for exact ID lookups (PR #, Jira keys, error codes).

## Step 43: Implemented Context-Enriched Embedding & Model Analysis
- **Date:** 2026-09-10
- **Time:** 23:02 IST
- **Purpose:** Upgraded `LocalEmbedder` to construct context-enriched text inputs (Title + Breadcrumb Section Path + Step Info + Source + Content) for vector encoding, ensuring dense vectors reflect document hierarchy while preserving clean raw text for LLM generation. Documented open-weight embedding model evaluation (`Qwen3-Embedding-0.6B/4B`, `BGE-M3`) in `RESEARCH.md`.

### Files Modified:
- [`backend/ingestion/embedder.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/embedder.py)
- [`RESEARCH.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/RESEARCH.md)

### Next Step:
- Phase 3: BM25 Keyword Search Index (`backend/storage/bm25_index.py`) for exact ID lookups (PR #, Jira keys, error codes).

## Step 44: Configured `Qwen/Qwen3-Embedding-0.6B` as Primary Embedding Model
- **Date:** 2026-09-10
- **Time:** 23:08 IST
- **Purpose:** Configured `Qwen/Qwen3-Embedding-0.6B` (1024 dimensions) as the primary default embedding model across the system, updating `LocalEmbedder`, `QdrantVectorStore`, `.env.example`, and test suites.

### Key Updates:
1. **`LocalEmbedder` (`backend/ingestion/embedder.py`)**:
   - Default model set to `Qwen/Qwen3-Embedding-0.6B` (1024-dim normalized dense vector).
   - Generates context-enriched embeddings (Title + Breadcrumbs + Step + Source + Content).
2. **`QdrantVectorStore` (`backend/storage/qdrant_client.py`)**:
   - Default vector size updated to `1024` for Qwen cosine similarity collections.
3. **`test_pipeline.py` (`backend/ingestion/tests/test_pipeline.py`)**:
   - Validated 1024-dimensional Qwen embeddings and RBAC pre-filtered vector retrieval.

### Files Modified:
- [`backend/ingestion/embedder.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/embedder.py)
- [`backend/storage/qdrant_client.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/qdrant_client.py)
- [`.env.example`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/.env.example)
- [`backend/ingestion/tests/test_pipeline.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/tests/test_pipeline.py)

### Next Step:
- Phase 3: BM25 Keyword Search Index (`backend/storage/bm25_index.py`).

## Step 45: Implemented BM25 Keyword Search Index (`backend/storage/bm25_index.py`) (Phase 3)
- **Date:** 2026-09-11
- **Time:** 22:13 IST
- **Purpose:** Implemented Phase 3 of the Agentic RAG architecture: the BM25 Keyword Search Index for exact token/identifier lookups (Jira ticket keys, PR numbers, code symbol names, error codes), complete with enterprise-aware tokenization, RBAC pre-filtering, disk persistence, and dual-indexing integration with `IngestionPipeline`.

### Key Features Built:
1. **`BM25Index` (`backend/storage/bm25_index.py`)**:
   - Uses `BM25Plus` from `rank-bm25==0.2.2` (eliminating negative/zero IDF pathologies on small/heterogeneous corpora).
   - **Code & Identifier Aware Tokenizer**: Preserves `snake_case`, `kebab-case`, `camelCase`, ticket keys (`PAY-928`), PR identifiers (`#1842`), and error codes (`HTTP 401`).
   - **Strict RBAC Pre-Filtering**: Evaluates `is_public`, `allowed_roles`, `allowed_users`, and `allowed_groups` on keyword results before returning candidates.
   - **Disk Persistence**: Serializes corpus and metadata to JSON (`./data/bm25_index.json`), instant reload on application restart.
2. **Dual-Index Ingestion Pipeline (`backend/ingestion/pipeline.py`)**:
   - `IngestionPipeline.ingest_concept()` now automatically writes to **both Qdrant (dense vectors) and BM25 (sparse lexical index)** in a single call.
3. **Automated Test Suite (`backend/storage/tests/test_bm25_index.py`)**:
   - 5 comprehensive tests validating identifier extraction, exact searches, RBAC security boundaries, disk reload, and pipeline dual-indexing.

### Files Created/Modified:
- [`backend/storage/bm25_index.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/bm25_index.py)
- [`backend/storage/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/__init__.py)
- [`backend/ingestion/pipeline.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/pipeline.py)
- [`backend/storage/tests/test_bm25_index.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/tests/test_bm25_index.py)
- [`requirements.txt`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/requirements.txt)

### Next Step:
- Phase 4: Basic Agent + Semantic Search Retrieval Tool (M1 Milestone) (`backend/retrieval/semantic.py`, `backend/agent/tools.py`, `backend/agent/planner.py`, `backend/generation/context_builder.py`, `backend/generation/answer_generator.py`).

## Step 46: Documented Verification Guide in `docs/verify_phase2.md`
- **Date:** 2026-09-11
- **Time:** 22:14 IST
- **Purpose:** Created comprehensive documentation in `docs/verify_phase2.md` detailing the execution, test architecture, chunk structure inspection, local model caching, and RBAC pre-filtering verification provided by `scripts/verify_phase2.py`.

### Files Created:
- [`docs/verify_phase2.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/verify_phase2.md)

## Step 47: Added Dynamic `sys.path` Bootstrap to Test Suites
- **Date:** 2026-09-11
- **Time:** 22:31 IST
- **Purpose:** Added dynamic `sys.path` parent search bootstrap across test files (`test_smart_chunker.py`, `test_pipeline.py`, `test_bm25_index.py`), preventing `ModuleNotFoundError: No module named 'backend'` when tests are run directly or from varying subdirectories.

### Files Modified:
- [`backend/ingestion/tests/test_smart_chunker.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/tests/test_smart_chunker.py)
- [`backend/ingestion/tests/test_pipeline.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/tests/test_pipeline.py)
- [`backend/storage/tests/test_bm25_index.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/tests/test_bm25_index.py)

---

## Step 48: Autonomous Agent Reasoning Loop & Grounded Generation (Phase 4 / Milestone 1)
- **Date:** 2026-09-15
- **Time:** 15:15 IST
- **Purpose:** Implemented Phase 4 (Milestone 1) of the Enterprise Knowledge Agent architecture: the autonomous multi-turn tool calling reasoning loop (`AgentPlanner`), the retrieval tool registry (`ToolRegistry`), semantic search tool handler (`SemanticRetriever`), bracketed context builder (`ContextBuilder`), and fact-grounded answer synthesizer (`AnswerGenerator`).

### Key Components Built:
1. **`SemanticRetriever` (`backend/retrieval/semantic.py`)**:
   - Executes dense vector semantic retrieval over `QdrantVectorStore` using `LocalEmbedder` (`Qwen/Qwen3-Embedding-0.6B`).
   - Propagates user identity & roles for strict database-level RBAC pre-filtering.
   - Extracts and formats chunk breadcrumbs, source URLs, content types, and similarity scores.
2. **`ToolRegistry` (`backend/agent/tools.py`)**:
   - Provider-agnostic tool execution engine mapping standard JSON schema `ToolDefinition` to Python callable handlers.
   - Registered `semantic_search` with parameters (`query`, `top_k`, `score_threshold`).
   - Thread-safe user context injection (`roles`, `user_id`, `groups`) for downstream permission enforcement.
3. **`ContextBuilder` (`backend/generation/context_builder.py`)**:
   - Formats retrieved evidence chunks into structured, numbered blocks (`[1]`, `[2]`, etc.) with hierarchical breadcrumbs (`section_path`), content types, and source resource paths.
   - Deduplicates chunks and generates precise citation metadata objects for UI rendering.
4. **`AnswerGenerator` (`backend/generation/answer_generator.py`)**:
   - Synthesizes user-facing answers strictly grounded in retrieved evidence, preventing hallucinations and preserving step-by-step procedural order.
   - Enforces numbered citations (`[1]`, `[2]`) in LLM outputs.
5. **`AgentPlanner` (`backend/agent/planner.py`)**:
   - Autonomous multi-turn reasoning loop driving `LLMProvider.generate_with_tools()`.
   - Iteratively calls retrieval tools, inspects results, refines queries if needed, and formulates final grounded answers with structured citations.
6. **Automated End-to-End Test Suite (`backend/agent/tests/test_agent_loop.py`)**:
   - 3 unit tests verifying context formatting, multi-turn autonomous tool execution, and RBAC security boundaries.

### Files Created / Modified:
- [`backend/retrieval/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/retrieval/__init__.py)
- [`backend/retrieval/semantic.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/retrieval/semantic.py)
- [`backend/agent/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/__init__.py)
- [`backend/agent/tools.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/tools.py)
- [`backend/generation/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/generation/__init__.py)
- [`backend/generation/context_builder.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/generation/context_builder.py)
- [`backend/generation/answer_generator.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/generation/answer_generator.py)
- [`backend/agent/planner.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/planner.py)
- [`backend/agent/tests/test_agent_loop.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/tests/test_agent_loop.py)

### Next Step:
- Phase 5: Keyword Search Retrieval Tool (`backend/retrieval/keyword.py` + registering `keyword_search` in `ToolRegistry`).

---

## Step 49: Phase 3 Verification Scripts & Documentation (`verify_phase3.py`, `docs/verify_phase3.md`)
- **Date:** 2026-09-15
- **Time:** 15:33 IST
- **Purpose:** Created end-to-end visual inspection script [`scripts/verify_phase3.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase3.py), dedicated test runner [`backend/storage/tests/verify_phase3.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/tests/verify_phase3.py), and comprehensive documentation in [`docs/verify_phase3.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/verify_phase3.md) to test and visually validate all Phase 3 BM25 Keyword Search capabilities.

### Features Verified:
1. **Code & Identifier-Aware Tokenizer**: Decomposes snake_case (`refund_batch_processor`), camelCase (`AuthService`), Jira keys (`PAY-928`), PR numbers (`#1842`), and error codes (`HTTP 401`, `ECONNREFUSED`).
2. **BM25Plus Exact Search**: 100% precision exact retrieval across technical identifiers and function symbols.
3. **Strict RBAC Access Control Pre-Filtering**: Zero-leakage database-level access checks on keyword search.
4. **Disk Serialization & Instant Reload**: Validated `save_to_disk()` / `load_from_disk()` JSON persistence.
5. **Dual-Indexing Pipeline**: Single `IngestionPipeline.ingest_concept()` synchronizing both Qdrant and BM25 indexes simultaneously.

### Files Created:
- [`scripts/verify_phase3.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase3.py)
- [`backend/storage/tests/verify_phase3.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/storage/tests/verify_phase3.py)
- [`docs/verify_phase3.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/verify_phase3.md)

---

## Step 50: IngestionPipeline BM25 Auto-Persistence to `./data/bm25_index.json`
- **Date:** 2026-09-15
- **Time:** 18:24 IST
- **Purpose:** Configured `IngestionPipeline` and `verify_phase3.py` to automatically serialize the BM25 lexical index to disk at `./data/bm25_index.json` upon document ingestion, enabling persistent inspection of indexed chunks, tokenized corpus, and RBAC metadata.

### Files Modified:
- [`backend/ingestion/pipeline.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/ingestion/pipeline.py)
- [`scripts/verify_phase3.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase3.py)

---

## Step 51: Phase 4 Verification Scripts & Documentation (`verify_phase4.py`, `docs/verify_phase4.md`)
- **Date:** 2026-09-15
- **Time:** 18:31 IST
- **Purpose:** Created end-to-end visual inspection script [`scripts/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase4.py), dedicated test runner [`backend/agent/tests/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/tests/verify_phase4.py), and comprehensive documentation in [`docs/verify_phase4.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/verify_phase4.md) to test and visually validate all Phase 4 (Milestone 1) Agent Reasoning & Semantic Tool capabilities.

### Features Verified:
1. **`SemanticRetriever`**: Dense vector semantic retrieval over Qdrant with local Qwen embeddings and strict RBAC pre-filtering.
2. **`ContextBuilder`**: Formats evidence chunks into structured, bracketed references `[1]`, `[2]` with hierarchical breadcrumbs and citation cards.
3. **`ToolRegistry`**: Dynamic tool registration, JSON schema generation, and RBAC security context injection.
4. **`AnswerGenerator`**: Fact-grounded generation preventing hallucinations and enforcing bracketed source citations.
5. **`AgentPlanner`**: Autonomous multi-turn reasoning loop (User $\rightarrow$ Tool Call $\rightarrow$ Tool Execution $\rightarrow$ Result Feedback $\rightarrow$ Final Grounded Answer).

### Files Created / Modified:
- [`scripts/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase4.py)
- [`backend/agent/tests/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/tests/verify_phase4.py)
- [`docs/verify_phase4.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/verify_phase4.md)
- [`backend/retrieval/semantic.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/retrieval/semantic.py)
- [`backend/generation/context_builder.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/generation/context_builder.py)

---

## Step 52: Excluded Local Data & Storage Artifacts in `.gitignore`
- **Date:** 2026-09-15
- **Time:** 18:37 IST
- **Purpose:** Added `data/`, `**/data/`, `*.qdrant`, and `bm25_index.json` to `.gitignore` to prevent local vector database storage files, SQLite databases, and BM25 index JSON files from being tracked in Git.

### Files Modified:
- [`.gitignore`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/.gitignore)

---

## Step 53: Preserved 100% Payload Metadata in Semantic Vector Retrieval
- **Date:** 2026-09-15
- **Time:** 18:57 IST
- **Purpose:** Upgraded `SemanticRetriever.search()` result formatting to retain all underlying Qdrant payload fields (including `parent_id`, `chunk_index`, `total_chunks`, `created_at`, `updated_at`, `permissions`, and `point_id`), ensuring zero structural or security metadata is lost for downstream Small-to-Large expansion, recency scoring, and provenance tracking.

### Files Modified:
- [`backend/retrieval/semantic.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/retrieval/semantic.py)
- [`backend/generation/context_builder.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/generation/context_builder.py)

---

## Step 54: LangGraph State Machine Integration for Phase 4 Agent Loop
- **Date:** 2026-09-15
- **Time:** 19:15 IST
- **Purpose:** Integrated `langgraph` and `langchain-core` as the stateful orchestration engine for the Phase 4 Agent Reasoning Loop (`LangGraphAgentPlanner`), defining a compiled `StateGraph` with explicit nodes (`reasoner`, `tool_node`, `generator`), conditional routing edges, and strict RBAC context propagation through typed `AgentState`.

### Key Components Built:
1. **`AgentState` (`backend/agent/state.py`)**:
   - Typed state dictionary managing `query`, `user_context`, `messages` (with LangGraph `add_messages` reducer), `retrieved_chunks`, `citations`, `answer`, and `turn_count`.
2. **`LangGraphAgentPlanner` (`backend/agent/langgraph_planner.py`)**:
   - Stateful compiled `StateGraph` coordinating:
     - `reasoner`: Evaluates conversation state and available tools via `LLMProvider.generate_with_tools()`.
     - `_should_continue`: Conditional routing edge checking for `tool_calls` vs direct completion.
     - `tool_node`: Executes retrieval tools in `ToolRegistry` with user security context (`roles`, `user_id`, `groups`) and accumulates evidence chunks.
     - `generator`: Synthesizes final grounded answer with numbered citations via `AnswerGenerator`.
3. **Automated Test Suite (`backend/agent/tests/test_langgraph_agent.py`)**:
   - 3 unit tests verifying graph compilation, multi-turn state transitions, and RBAC isolation.
4. **Verification Script & Docs**:
   - Added Section 6 to [`scripts/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase4.py) and updated [`scripts/verify_phase4.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase4.md).

### Files Created / Modified:
- [`backend/agent/state.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/state.py)
- [`backend/agent/langgraph_planner.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/langgraph_planner.py)
- [`backend/agent/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/__init__.py)
- [`backend/agent/tests/test_langgraph_agent.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/tests/test_langgraph_agent.py)
- [`scripts/verify_phase4.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase4.py)
- [`scripts/verify_phase4.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/scripts/verify_phase4.md)
- [`requirements.txt`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/requirements.txt)

---

## Step 55: LangChain-Native Retrieval Tools & Pydantic Schemas (`backend/agent/langchain_tools.py`)
- **Date:** 2026-09-15
- **Time:** 19:27 IST
- **Purpose:** Implemented native LangChain `@tool` / `StructuredTool` definitions and Pydantic argument schemas (`SemanticSearchInput`, `KeywordSearchInput`, `ResourceLookupInput`) with RBAC security binding in `backend/agent/langchain_tools.py`, enabling direct execution in standard LangGraph `ToolNode` instances.

### Key Features Built:
1. **Pydantic Argument Validation**: Strongly-typed schemas validating queries, `top_k`, `source`, and `resource_type`.
2. **`create_langchain_tools()`**: Factory constructing LangChain `BaseTool` instances bound to user security context (`roles`, `user_id`, `groups`).
3. **`convert_registry_to_langchain_tools()`**: Bidirectional bridge converting our internal `ToolRegistry` into standard LangChain tools.
4. **Validation Test**: Added `test_04_native_langchain_tools_execution` in `backend/agent/tests/test_langgraph_agent.py` (4/4 tests passing).

### Files Created / Modified:
- [`backend/agent/langchain_tools.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/langchain_tools.py)
- [`backend/agent/__init__.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/__init__.py)
- [`backend/agent/tests/test_langgraph_agent.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/agent/tests/test_langgraph_agent.py)

---

## Step 56: Appended Future Improvements to Project Notes (`docs/NOTES.md`)
- **Date:** 2026-09-20
- **Time:** 23:42 IST
- **Purpose:** Added architectural notes and future improvements to `docs/NOTES.md` regarding LangGraph multi-tool execution concurrency.

### Key Topics Documented:
1. **Concurrent vs. Sequential Multi-Tool Execution in LangGraph**:
   - Documented the current design where the LLM plans multi-tool calls in a single turn and `_tool_node()` executes them sequentially in `< 5ms` with local Qdrant/BM25.
   - Outlined future upgrade path using `asyncio.gather` or `ThreadPoolExecutor` for remote HTTP APIs / network-bound connectors.

### Files Modified:
- [`docs/NOTES.md`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/docs/NOTES.md)









