# Confluence Connector - Test Suite & Execution Guide

This directory contains test runners and verification scripts for the **Confluence Connector** and the **Open Knowledge Format (OKF v0.2)** pipeline.

---

## 📁 File-by-File Breakdown

| Script File | Purpose | Main Output / Result |
|:---|:---|:---|
| [`test_run_okf.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/tests/test_run_okf.py) | **End-to-End OKF v0.2 Bundle Generator**. Ingests Confluence spaces/pages and builds a complete, standardized Knowledge Bundle. | `test_data/okf_bundle/` (`.okf.md`, `.okf.json`, `index.md`, `log.md`) |
| [`test_run_connector.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/connectors/confluence/tests/test_run_connector.py) | **Live Connector & Normalization Runner**. Runs `ConfluenceConnector`, parses Storage-Format bodies, and generates intermediate structured JSON + rendered Markdown. | `test_data/output_document_*.json`<br>`test_data/output_document_*.md` |

---

## 🚀 How to Run

Make sure you are in the workspace root directory:
```bash
cd /Users/ompatil/Desktop/Enterprise-Knowledge-Agent
```

### 1. Generate & Test the OKF v0.2 Knowledge Bundle (Recommended)
Generates full OKF v0.2 concept files with YAML frontmatter, footnote citations, `index.md`, and `log.md`.

- **Run on all spaces**:
  ```bash
  python backend/connectors/confluence/tests/test_run_okf.py
  ```
- **Run on specific spaces (comma-separated)**:
  ```bash
  python backend/connectors/confluence/tests/test_run_okf.py --spaces ENG,PROJ
  ```
- **Include space-level overview documents**:
  ```bash
  python backend/connectors/confluence/tests/test_run_okf.py --include-spaces
  ```

---

### 2. Test the Live Confluence Connector (Intermediate Representation)
Validates connection, parses Storage-Format (XHTML) bodies, and outputs intermediate Document JSON and Markdown.

- **Run on all spaces**:
  ```bash
  python backend/connectors/confluence/tests/test_run_connector.py
  ```
- **Run on specific spaces**:
  ```bash
  python backend/connectors/confluence/tests/test_run_connector.py --spaces ENG,PROJ
  ```

---

## 💾 Where the Data is Stored

All test outputs and generated files are saved inside **`backend/connectors/confluence/test_data/`**:

```
backend/connectors/confluence/test_data/
│
├── okf_bundle/                        <-- OKF v0.2 Knowledge Bundle
│   ├── index.md                       <-- Directory listing for Progressive Disclosure (§8)
│   ├── log.md                         <-- Chronological update history (§9)
│   ├── <concept_name>.okf.md          <-- Concept document (YAML Frontmatter + Markdown Body)
│   └── <concept_name>.okf.json        <-- Machine-readable JSON schema (for Vector DB / Neo4j)
│
├── output_document_<page>.json        <-- Intermediate structured Document JSON
└── output_document_<page>.md          <-- Rendered Markdown document preview
```

---

## ⚙️ Prerequisites (.env Configuration)

Ensure your `.env` file at the root of the project contains:

```env
CONFLUENCE_URL="https://your-domain.atlassian.net/wiki"
CONFLUENCE_USERNAME="your-email@company.com"
CONFLUENCE_API_TOKEN="your_confluence_api_token"
```

> **Note:** Confluence Cloud requires an API token generated at
> [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
> Use your account e-mail as `CONFLUENCE_USERNAME` — never your Atlassian password.

---

## 🔐 Required Confluence Permissions

The connector relies on read-only REST endpoints. The API token only needs access
to spaces the account can already view:

- `GET /rest/api/user/current` (connection test)
- `GET /rest/api/space` (space discovery)
- `GET /rest/api/content` (page listing per space)
- `GET /rest/api/content/{id}?expand=body.storage,version,space,history,ancestors` (full page)