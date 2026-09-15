# Enterprise Knowledge Agent - Official Documentation & Developer Portal Directory

This document consolidates all essential external documentation, API references, developer consoles, credential management portals, and library guides needed to build, configure, and maintain the **Enterprise Knowledge Agent** across all data sources, AI models, and database engines.

---

## 📌 Table of Contents

1. [Email Connectors (Gmail API & Google Workspace)](#1-email-connectors-gmail-api--google-workspace)
2. [Cloud Storage Connectors (Dropbox & Google Drive)](#2-cloud-storage-connectors-dropbox--google-drive)
3. [Notion Connector](#3-notion-connector)
4. [Atlassian Connectors (Confluence & Jira)](#4-atlassian-connectors-confluence--jira)
5. [Code & Version Control Connectors (GitHub)](#5-code--version-control-connectors-github)
6. [Team Collaboration Connectors (Slack)](#6-team-collaboration-connectors-slack)
7. [LLMs, Embedding Models & AI Providers](#7-llms-embedding-models--ai-providers)
8. [Vector & Graph Database Infrastructure](#8-vector--graph-database-infrastructure)
9. [Binary Document Parsing Engines](#9-binary-document-parsing-engines)

---

## 📬 1. Email Connectors (Gmail API & Google Workspace)

> [!NOTE]
> For Gmail integration, use the least-privilege OAuth scope (`https://www.googleapis.com/auth/gmail.readonly`). Tokens and credentials must never be committed to Git.

| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Google Cloud Console** | Central project dashboard, billing, and API enablement | [console.cloud.google.com](https://console.cloud.google.com/) |
| **Google API Library** | Enable Gmail API, Drive API, and Google Workspace APIs | [console.cloud.google.com/apis/library](https://console.cloud.google.com/apis/library) |
| **Google Auth Platform** | Configure OAuth 2.0 consent screen, scopes, and test users | [console.cloud.google.com/auth/overview](https://console.cloud.google.com/auth/overview) |
| **Google Credentials Console** | Create Desktop OAuth client IDs and download `credentials.json` | [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials) |
| **Gmail API Quickstart** | Official Python quickstart and OAuth flow setup | [developers.google.com/gmail/api/quickstart/python](https://developers.google.com/gmail/api/quickstart/python) |
| **Gmail API REST Reference** | Endpoints for `users.messages.list`, `users.messages.get`, and threads | [developers.google.com/gmail/api/reference/rest/v1/users.messages](https://developers.google.com/gmail/api/reference/rest/v1/users.messages) |
| **Gmail OAuth Scopes** | Complete list of Gmail scopes and security guidelines | [developers.google.com/gmail/api/auth/scopes](https://developers.google.com/gmail/api/auth/scopes) |
| **Google Auth Python Docs** | SDK guide for `google-auth` and `google-auth-oauthlib` | [google-auth.readthedocs.io](https://google-auth.readthedocs.io/) |

---

## 📦 2. Cloud Storage Connectors (Dropbox & Google Drive)

> [!TIP]
> For Dropbox, use offline OAuth with `DROPBOX_REFRESH_TOKEN` so your access tokens refresh automatically without expiring after 4 hours.

### Dropbox API v2
| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Dropbox App Console** | Create developer applications, configure permissions, and get App Key / Secret | [dropbox.com/developers/apps](https://www.dropbox.com/developers/apps) |
| **Dropbox OAuth 2.0 Guide** | Guide for offline refresh tokens and PKCE authorization code flow | [dropbox.com/developers/reference/oauth-guide](https://www.dropbox.com/developers/reference/oauth-guide) |
| **Dropbox Python SDK** | Official SDK documentation for `dropbox.Dropbox` client | [dropbox-sdk-python.readthedocs.io](https://dropbox-sdk-python.readthedocs.io/) |
| **Dropbox HTTP API Reference** | Full endpoint documentation for `files/list_folder`, `files/download`, etc. | [dropbox.com/developers/documentation/http/documentation](https://www.dropbox.com/developers/documentation/http/documentation) |
| **Dropbox API Explorer** | Interactive API testing sandbox for Dropbox API calls | [dropbox.github.io/dropbox-api-v2-explorer](https://dropbox.github.io/dropbox-api-v2-explorer/) |

### Google Drive API
| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Google Drive API Python** | Quickstart and reference for Google Docs and Drive folder traversal | [developers.google.com/drive/api/quickstart/python](https://developers.google.com/drive/api/quickstart/python) |
| **Google Drive REST API** | Endpoints for `files.list`, `files.get`, and export formats | [developers.google.com/drive/api/reference/rest/v3](https://developers.google.com/drive/api/reference/rest/v3) |
| **Google Drive Scopes** | Least-privilege drive scopes (`drive.readonly`, `drive.metadata.readonly`) | [developers.google.com/drive/api/guides/api-specific-auth](https://developers.google.com/drive/api/guides/api-specific-auth) |

---

## 📝 3. Notion Connector

| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Notion Developer Portal** | Create internal integrations and retrieve `NOTION_TOKEN` | [notion.so/profile/integrations](https://www.notion.so/profile/integrations) |
| **Notion Connections Manager** | Manage workspace internal connections & grant page access | [app.notion.com/developers/connections](https://app.notion.com/developers/connections) |
| **Notion API Documentation** | Official Notion Developer Documentation hub | [developers.notion.com](https://developers.notion.com/) |
| **Notion Search Reference** | OpenAPI spec & guide for `POST /v1/search` workspace discovery | [developers.notion.com/reference/post-search](https://developers.notion.com/reference/post-search) |
| **Notion Block Objects Reference** | Semantic block schema for headings, toggles, callouts, and tables | [developers.notion.com/reference/block](https://developers.notion.com/reference/block) |
| **Notion Database Query Reference** | API documentation for `POST /v1/databases/{id}/query` | [developers.notion.com/reference/post-database-query](https://developers.notion.com/reference/post-database-query) |
| **Notion API Versioning** | Release notes and breaking change guides across API versions | [developers.notion.com/reference/versioning](https://developers.notion.com/reference/versioning) |

---

## 📦 3. Dropbox Connector

| Resource | Purpose | Official URL |
| :--- | :--- | :--- |
| **Dropbox Developer App Console** | Create Dropbox Apps, manage permissions, and view App Key/Secret | [dropbox.com/developers/apps](https://www.dropbox.com/developers/apps) |
| **Dropbox OAuth 2.0 Guide** | Documentation on short-lived tokens and `token_access_type=offline` | [developers.dropbox.com/oauth-guide](https://developers.dropbox.com/oauth-guide) |
| **Dropbox Python SDK Docs** | Official Python SDK reference (`dropbox.Dropbox`, OAuth flows) | [dropbox-sdk-python.readthedocs.io](https://dropbox-sdk-python.readthedocs.io/) |
| **Dropbox API HTTP Reference** | Endpoints for `/files/list_folder`, `/files/download`, `/users/get_current_account` | [dropbox.com/developers/documentation/http/documentation](https://www.dropbox.com/developers/documentation/http/documentation) |
| **Dropbox API Explorer** | Interactive API testing tool for Dropbox endpoints | [dropbox.github.io/dropbox-api-v2-explorer](https://dropbox.github.io/dropbox-api-v2-explorer/) |

---

## 💼 4. Atlassian Connectors (Confluence & Jira)

| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Atlassian Security Tokens** | Generate and manage API tokens for Confluence & Jira authentication | [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| **Confluence Cloud REST API (v2)** | Official Confluence API documentation for pages, spaces, and content trees | [developer.atlassian.com/cloud/confluence/rest/v2/intro](https://developer.atlassian.com/cloud/confluence/rest/v2/intro/) |
| **Jira Cloud REST API (v3)** | Official Jira API documentation for issues, epics, sprints, and projects | [developer.atlassian.com/cloud/jira/platform/rest/v3/intro](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/) |
| **Atlassian Document Format (ADF)** | Specification for parsing structured rich text in Jira & Confluence | [developer.atlassian.com/cloud/jira/platform/apis/document/structure](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/) |
| **Atlassian Security Tokens** | Generate and manage API tokens for Confluence & Jira | [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| **Atlassian API Authorization** | Basic Auth / API token usage across all Atlassian APIs | [developer.atlassian.com/cloud/confluence/basic-auth](https://developer.atlassian.com/cloud/confluence/basic-auth) |
| **Confluence Cloud REST API (v1)** | Official Confluence API documentation for pages & spaces (used by this connector) | [developer.atlassian.com/cloud/confluence/rest/v1/intro](https://developer.atlassian.com/cloud/confluence/rest/v1/intro/) |
| **Confluence Get Content by ID** | Fetch single page with `expand=body.storage,version,space,history,ancestors` | [developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/#api-wiki-rest-api-content-id-get](https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/#api-wiki-rest-api-content-id-get) |
| **Confluence Get Spaces** | List spaces for discovery (`GET /rest/api/space`) | [developer.atlassian.com/cloud/confluence/rest/v1/api-group-space/#api-wiki-rest-api-space-get](https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-space/#api-wiki-rest-api-space-get) |
| **Confluence Storage Format** | Body.storage XHTML representation parsed by the connector | [developer.atlassian.com/cloud/confluence/storage-format](https://developer.atlassian.com/cloud/confluence/storage-format/) |
| **Jira Cloud REST API (v3)** | Official Jira API documentation for issues, epics, & sprints | [developer.atlassian.com/cloud/jira/platform/rest/v3/intro](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/) |
| **Jira Get Issue by Key** | Fetch single issue with fields (`GET /rest/api/3/issue/{key}`) | [developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get) |
| **Jira Search Issues (JQL)** | Search issues with `startAt` pagination (`GET /rest/api/3/search`) | [developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-get](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-get) |
| **Jira Get Projects** | List projects for discovery (`GET /rest/api/3/project`) | [developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-projects/#api-rest-api-3-project-get](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-projects/#api-rest-api-3-project-get) |
| **Jira Get Myself** | Verify credentials via current user (`GET /rest/api/3/myself`) — used by `test_connection()` | [developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-users/#api-rest-api-3-myself-get](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-users/#api-rest-api-3-myself-get) |
| **Jira Basic Auth** | Basic Auth / API token usage for Jira Cloud REST calls | [developer.atlassian.com/cloud/jira/platform/basic-auth/](https://developer.atlassian.com/cloud/jira/platform/basic-auth/) |
| **Atlassian Document Format (ADF)** | Specification for parsing rich structured text (Jira descriptions) | [developer.atlassian.com/cloud/jira/platform/apis/document/structure](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/) |

---

## 🐙 5. Code & Version Control Connectors (GitHub)
## 🐙 5. Code & Collaboration Connectors (GitHub, Slack, Google Drive)

> [!NOTE]
> You can authenticate with either Classic Personal Access Tokens (`ghp_...`) or Fine-Grained Personal Access Tokens (`github_pat_...`). For fine-grained tokens, only read-only permissions are required.

| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **GitHub Token Settings** | Generate Personal Access Tokens (Classic or Fine-Grained) | [github.com/settings/tokens](https://github.com/settings/tokens) |
| **GitHub REST API Docs** | Endpoints for repositories, file trees, pull requests, issues, and commits | [docs.github.com/en/rest](https://docs.github.com/en/rest) |
| **GitHub Repositories API** | Guide for `GET /user/repos` and `GET /repos/{owner}/{repo}` | [docs.github.com/en/rest/repos](https://docs.github.com/en/rest/repos) |
| **GitHub Git Trees API** | Recursive tree enumeration for fast file discovery (`GET /git/trees/{branch}`) | [docs.github.com/en/rest/git/trees](https://docs.github.com/en/rest/git/trees) |
| **GitHub Issues & PRs API** | Endpoints for listing issues, discussions, comments, and PRs | [docs.github.com/en/rest/issues](https://docs.github.com/en/rest/issues) |
| **GitHub Rate Limiting** | Best practices for secondary rate limits and `X-RateLimit-Reset` | [docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) |

---

## 💬 6. Team Collaboration Connectors (Slack)

| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Slack App Management** | Create Slack apps, configure bot users, and retrieve tokens (`xoxb-...`) | [api.slack.com/apps](https://api.slack.com/apps) |
| **Slack Bot Scopes** | Documentation for `channels:read`, `channels:history`, and `users:read` | [api.slack.com/scopes](https://api.slack.com/scopes) |
| **Slack Web API Reference** | Methods for `conversations.list`, `conversations.history`, and `conversations.replies` | [api.slack.com/methods](https://api.slack.com/methods) |
| **Slack Bolt for Python** | Official Python SDK framework for building Slack apps | [slack.dev/bolt-python](https://slack.dev/bolt-python/) |

---

## 🤖 7. LLMs, Embedding Models & AI Providers
## 🤖 6. LLMs, Embedding Models & AI Providers

| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Google AI Studio** | Generate Gemini API keys (`GEMINI_API_KEY`) and inspect models | [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) |
| **Google GenAI Python SDK** | Official SDK documentation for `google-genai` and `text-embedding-004` | [ai.google.dev/gemini-api/docs](https://ai.google.dev/gemini-api/docs) |
| **OpenAI Developer Platform** | Manage OpenAI API keys (`OPENAI_API_KEY`) and usage | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| **OpenAI Embeddings Guide** | Reference for `text-embedding-3-small` and `text-embedding-3-large` | [platform.openai.com/docs/guides/embeddings](https://platform.openai.com/docs/guides/embeddings) |
| **Anthropic Console** | Manage Claude API keys (`ANTHROPIC_API_KEY`) and usage | [console.anthropic.com](https://console.anthropic.com/) |

---

## 🗄️ 8. Vector & Graph Database Infrastructure
## 🗄️ 7. Vector & Graph Database Infrastructure

| Resource | Description | Official URL |
| :--- | :--- | :--- |
| **Qdrant Documentation** | High-performance vector search engine, payload filtering, and indexing | [qdrant.tech/documentation](https://qdrant.tech/documentation/) |
| **Qdrant Cloud Console** | Managed Qdrant cluster provisioning and API key credentials | [cloud.qdrant.io](https://cloud.qdrant.io/) |
| **ChromaDB Documentation** | Lightweight embedded vector database documentation | [docs.trychroma.com](https://docs.trychroma.com/) |
| **Neo4j Aura Console** | Cloud-managed Neo4j Graph Database instances | [console.neo4j.io](https://console.neo4j.io/) |
| **Neo4j Cypher Manual** | Cypher Query Language reference for GraphRAG entity retrieval | [neo4j.com/docs/cypher-manual/current](https://neo4j.com/docs/cypher-manual/current/) |
| **Neo4j Python Driver** | Official Python client library for connecting to Neo4j instances | [neo4j.com/docs/python-manual/current](https://neo4j.com/docs/python-manual/current/) |

---

## 📄 9. Binary Document Parsing Engines

These core Python libraries power our universal multi-format extraction pipeline in [`backend/parsers/document_extractors.py`](file:///Users/ompatil/Desktop/Enterprise-Knowledge-Agent/backend/parsers/document_extractors.py):

| Library | Description | Documentation URL |
| :--- | :--- | :--- |
| **pypdf** | Pure-Python PDF text and structure extraction engine | [pypdf.readthedocs.io](https://pypdf.readthedocs.io/) |
| **python-docx** | Microsoft Word (.docx) document, paragraph, style, and table parser | [python-docx.readthedocs.io](https://python-docx.readthedocs.io/) |
| **openpyxl** | Microsoft Excel (.xlsx) workbook, sheet, formula evaluation, and table extractor | [openpyxl.readthedocs.io](https://openpyxl.readthedocs.io/) |
