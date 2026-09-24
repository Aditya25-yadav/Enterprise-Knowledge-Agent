# Enterprise Knowledge Agent

## Developer Setup & API Documentation

This document explains all external services used by the **Enterprise Knowledge Agent**, what each service is used for, what credentials are required, and where to obtain them.

---

# 1. 🧠 Understand the Architecture First

The project connects to multiple enterprise data sources and converts their data into a common format.

```text
                         USER
                           │
                           ▼
                    ┌─────────────┐
                    │ AI AGENT    │
                    │ / PLANNER   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          Vector        Keyword       Graph
          Search        Search        Search
              │            │            │
              └────────────┼────────────┘
                           ▼
                   Permission Check
                           │
                           ▼
                         LLM
                           │
                           ▼
                  Answer + Citations


        DATA SOURCES
        ────────────

   Notion ───────┐
   GitHub ───────┤
   Jira ─────────┤
   Confluence ───┤
   Google Drive ─┤
   Gmail ────────┤
   Dropbox ──────┤
   Slack ────────┘
          │
          ▼
      CONNECTORS
          │
          ▼
         OKF
   (Common Data Format)
          │
          ▼
   Knowledge Pipeline
```

The external services fall into four categories:

| Category           | Services                                                       | Purpose                                   |
| ------------------ | -------------------------------------------------------------- | ----------------------------------------- |
| **Data Sources**   | Notion, GitHub, Jira, Confluence, Drive, Gmail, Dropbox, Slack | Provide enterprise information            |
| **AI**             | Gemini, OpenAI, Anthropic                                      | Reasoning, planning and answer generation |
| **Storage**        | Qdrant, ChromaDB, Neo4j                                        | Store/search knowledge                    |
| **Authentication** | OAuth, API Keys, Access Tokens                                 | Allow the agent to access services        |

---

# 2. 🚀 What Should I Set Up First?

You **do not need every service immediately**.

For the first working version, use:

```text
1. Gemini
2. Notion
3. Qdrant
4. Neo4j
```

This gives you:

```text
Notion
   ↓
Connector
   ↓
OKF
   ↓
Qdrant + Neo4j
   ↓
Agent
   ↓
Gemini
   ↓
Answer
```

After this works, add:

```text
5. GitHub
6. Jira
7. Google Drive
8. Gmail
9. Slack
10. Confluence
```

---

# 3. 🤖 LLM Provider

## 3.1 Google Gemini

### What is it?

Gemini is the AI model used by the agent.

It can be used for:

* Understanding user questions
* Agent planning
* Selecting tools
* Reasoning over retrieved information
* Generating final answers
* Potentially extracting entities from documents

### Credential required

```env
GEMINI_API_KEY=your_api_key
```

### Where do I get it?

**Google AI Studio**

https://aistudio.google.com/app/apikey

### Setup

1. Open Google AI Studio.
2. Sign in with your Google account.
3. Create or select a project.
4. Create an API key.
5. Copy the key.
6. Add it to `.env`.

Example:

```env
GEMINI_API_KEY=AIzaSyXXXXXXXXXXXX
```

### Documentation

https://ai.google.dev/gemini-api/docs

---

# 4. 📝 Notion Connector

## What is it?

Notion is one of the knowledge sources for the agent.

The agent can retrieve information from Notion pages and databases.

Architecture:

```text
Notion
   │
   ▼
Notion API
   │
   ▼
Notion Connector
   │
   ▼
OKF
```

### Credential required

```env
NOTION_TOKEN=your_notion_token
```

### Where do I get it?

**Notion Connections**

https://app.notion.com/developers/connections

### Setup

1. Create a Notion integration/connection.
2. Copy the integration token.
3. Give the integration access to the Notion pages you want it to read.
4. Put the token in `.env`.

Example:

```env
NOTION_TOKEN=secret_xxxxxxxxxxxxx
```

### Page ID

You may also specify a page for testing:

```env
NOTION_PAGE_ID=your_page_id
```

The Page ID is **not an API key**.

It identifies the Notion page you want to ingest.

### Documentation

Main API documentation:

https://developers.notion.com/

Search API:

https://developers.notion.com/reference/post-search

Block API:

https://developers.notion.com/reference/block

Database API:

https://developers.notion.com/reference/post-database-query

---

# 5. 🐙 GitHub Connector

## What is it?

GitHub provides coding-related information to the agent.

The agent can potentially retrieve:

* Repositories
* Source code
* Issues
* Pull requests
* Commits
* Repository metadata

Architecture:

```text
GitHub
   │
   ▼
GitHub API
   │
   ▼
GitHub Connector
   │
   ▼
OKF
   │
   ├──► Vector Search
   └──► Knowledge Graph
```

### Credential required

```env
GITHUB_TOKEN=your_github_token
```

### Where do I get it?

GitHub token settings:

https://github.com/settings/tokens

### Recommended approach

Use a **fine-grained Personal Access Token** and give it only the permissions your connector requires.

For a knowledge/retrieval system, prefer read-only access whenever possible.

### Documentation

https://docs.github.com/en/rest

---

# 6. 💼 Jira Connector

## What is it?

Jira provides project-management information.

The agent can retrieve:

* Issues
* Epics
* Sprints
* Assignees
* Project information
* Issue descriptions
* Status information

Example:

```text
User:
"What are the open issues related to authentication?"

Agent
   ↓
Jira Connector
   ↓
Jira API
   ↓
Issues
   ↓
OKF
   ↓
Agent
```

### Credentials required

```env
JIRA_URL=https://your-domain.atlassian.net
JIRA_USERNAME=your-email@company.com
JIRA_API_TOKEN=your_api_token
```

### Where do I get the API token?

Atlassian account security:

https://id.atlassian.com/manage-profile/security/api-tokens

### Documentation

Jira REST API:

https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/

---

# 7. 📚 Confluence Connector

## What is it?

Confluence contains company documentation and knowledge.

Examples:

```text
Architecture documents
Engineering documentation
Company processes
Technical specifications
Project documentation
```

### Credentials

Confluence Cloud can use Atlassian authentication.

```env
CONFLUENCE_URL=https://your-domain.atlassian.net/wiki
CONFLUENCE_USERNAME=your-email@company.com
CONFLUENCE_API_TOKEN=your_api_token
```

### API documentation

https://developer.atlassian.com/cloud/confluence/rest/v2/intro/

### Important

Jira and Confluence belong to the Atlassian ecosystem, so their authentication can often be managed through the same Atlassian account/token infrastructure.

---

# 8. 📁 Google Drive Connector

## What is it?

Google Drive provides access to enterprise documents.

Potential sources include:

```text
PDFs
Google Docs
Spreadsheets
Presentations
Folders
```

Architecture:

```text
Google Drive
     ↓
Google Drive API
     ↓
Drive Connector
     ↓
Document Parser
     ↓
OKF
```

### Credential

Your `.env` contains:

```env
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
```

This is **not an API key**.

It is a Google Cloud service-account credential file.

### Setup

1. Create a Google Cloud project.
2. Enable the Google Drive API.
3. Create the required credentials.
4. Download the credentials JSON.
5. Store it outside the Git repository.
6. Set the environment variable to its location.

### Google Cloud Console

https://console.cloud.google.com/

### API Library

https://console.cloud.google.com/apis/library

### Drive API documentation

https://developers.google.com/drive/api/quickstart/python

---

# 9. 📧 Gmail Connector

## What is it?

Gmail allows the agent to search and retrieve authorized email information.

Example:

```text
User:
"What did the team decide about the deployment?"

Agent
   ↓
Gmail
   ↓
Relevant emails
   ↓
OKF
   ↓
Retrieval
   ↓
Answer
```

### Authentication

Gmail normally uses **OAuth 2.0**, rather than simply putting a password/API key in `.env`.

You may need:

```text
credentials.json
```

and an OAuth flow that produces user authorization/token information.

### Google Cloud Console

https://console.cloud.google.com/

### Enable Gmail API

https://console.cloud.google.com/apis/library

### OAuth configuration

https://console.cloud.google.com/auth/overview

### Create credentials

https://console.cloud.google.com/apis/credentials

### Gmail Python Quickstart

https://developers.google.com/gmail/api/quickstart/python

### Gmail API reference

https://developers.google.com/gmail/api/reference/rest/v1/users.messages

### OAuth scopes

https://developers.google.com/gmail/api/auth/scopes

For read-only access, a scope such as:

```text
gmail.readonly
```

is preferable to unnecessarily broad permissions.

---

# 10. 📦 Dropbox Connector

## What is it?

Dropbox provides enterprise file and folder storage.

The agent can retrieve:

* Folders and directory hierarchies
* Code and plaintext files (.md, .py, .txt, .json, .yaml, .csv)
* Structured metadata and revision timestamps

Architecture:

```text
Dropbox
   │
   ▼
Dropbox API / SDK
   │
   ▼
Dropbox Connector
   │
   ▼
OKF
   │
   ├──► Vector Search
   └──► Knowledge Graph
```

### Authentication & Credentials

The connector supports permanent OAuth 2.0 with automatic background refresh:

```env
DROPBOX_APP_KEY=your_app_key
DROPBOX_APP_SECRET=your_app_secret
DROPBOX_REFRESH_TOKEN=your_refresh_token
```

Or a short-lived developer token:

```env
DROPBOX_ACCESS_TOKEN=sl.u.your_access_token
```

### Dropbox Developer Console

https://www.dropbox.com/developers/apps

### Required Permissions

Under the **Permissions** tab, enable:
* `files.metadata.read`
* `files.content.read`
* `account_info.read`

---

# 11. 💬 Slack Connector

## What is it?

Slack provides communication and collaboration information.

The agent could retrieve:

```text
Channels
Messages
Threads
Users
Relevant discussions
```

Example:

```text
User:
"What did the backend team discuss about the API outage?"

Agent
   ↓
Slack
   ↓
Relevant messages
   ↓
OKF
   ↓
Agent
```

### Credentials

Slack may require:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

### Where do I create the Slack application?

https://api.slack.com/apps

### Slack permissions

https://api.slack.com/scopes

Only request the scopes your connector actually needs.

---

# 11. 🗄️ Vector Database

The vector database stores embeddings of your knowledge.

It allows the system to perform **semantic search**.

Example:

```text
User:
"How does authentication work?"

          ↓

Embedding

          ↓

Vector Search

          ↓

Relevant document chunks
```

---

## 11.1 Qdrant

Your current configuration uses Qdrant:

```env
VECTOR_DB_TYPE=qdrant
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
```

### Local development

You can run Qdrant locally.

In this case:

```env
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
```

You don't necessarily need a cloud API key.

### Qdrant documentation

https://qdrant.tech/documentation/

### Qdrant Cloud

https://cloud.qdrant.io/

If you use Qdrant Cloud, you will receive:

```env
QDRANT_URL=https://your-cluster-url
QDRANT_API_KEY=your_api_key
```

---

## 11.2 ChromaDB

ChromaDB is an alternative vector database.

Your project allows:

```env
VECTOR_DB_TYPE=chromadb
```

Documentation:

https://docs.trychroma.com/

For the first version, choose **one**:

```text
Qdrant OR ChromaDB
```

You don't need both.

---

# 12. 🕸️ Knowledge Graph

## Neo4j

Neo4j stores relationships between entities.

For example:

```text
Payment Service
       │
       ├── OWNED_BY ──► Payments Team
       │
       ├── DEPENDS_ON ──► Kafka
       │
       └── TRACKED_BY ──► PAY-123
```

This allows the agent to answer relationship-based questions.

### Local configuration

```env
NEO4J_URI=neo4j://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
```

### Neo4j Cloud

https://console.neo4j.io/

### Cypher documentation

https://neo4j.com/docs/cypher-manual/current/

---

# 13. 🔑 OpenAI

OpenAI is an **optional alternative AI provider**.

If you want to use OpenAI instead of Gemini, configure:

```env
OPENAI_API_KEY=your_api_key
```

API key management:

https://platform.openai.com/api-keys

Embeddings documentation:

https://platform.openai.com/docs/guides/embeddings

You do **not** need Gemini and OpenAI simultaneously unless your application intentionally supports multiple providers.

---

# 14. 🧠 Anthropic / Claude

Anthropic is another optional LLM provider.

Credential:

```env
ANTHROPIC_API_KEY=your_api_key
```

Console:

https://console.anthropic.com/

You can ignore this during the initial implementation.

---

# 15. 🔐 Environment Variables

Your `.env.example` should contain placeholders only.

A clean initial `.env` could look like:

```env
# ==========================================
# AI
# ==========================================

GEMINI_API_KEY=your_real_key


# ==========================================
# NOTION
# ==========================================

NOTION_TOKEN=your_real_token
NOTION_PAGE_ID=your_page_id


# ==========================================
# VECTOR DATABASE
# ==========================================

VECTOR_DB_TYPE=qdrant
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=


# ==========================================
# KNOWLEDGE GRAPH
# ==========================================

NEO4J_URI=neo4j://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password


# ==========================================
# OPTIONAL CONNECTORS
# ==========================================

GITHUB_TOKEN=

DROPBOX_APP_KEY=
DROPBOX_APP_SECRET=
DROPBOX_REFRESH_TOKEN=
DROPBOX_ACCESS_TOKEN=

JIRA_URL=
JIRA_USERNAME=
JIRA_API_TOKEN=

CONFLUENCE_URL=
CONFLUENCE_USERNAME=
CONFLUENCE_API_TOKEN=

SLACK_BOT_TOKEN=
SLACK_APP_TOKEN=

GOOGLE_APPLICATION_CREDENTIALS=


# ==========================================
# OPTIONAL LLM PROVIDERS
# ==========================================

OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

---

# 16. 🔒 Security Rules

Never commit real credentials to GitHub.

Your repository should contain:

```text
.env.example     ✅
.env             ❌ DO NOT COMMIT
credentials.json ❌ DO NOT COMMIT
```

Your `.gitignore` should contain at least:

```gitignore
.env
```

Also make sure service-account credential files and OAuth token files are excluded.

### If a real API key is accidentally committed:

```text
Revoke / rotate the key
        ↓
Create a new key
        ↓
Update .env
        ↓
Remove the secret from Git history if necessary
```

---

# 17. 🏗️ Recommended Implementation Order

Do not implement everything simultaneously.

### Phase 1: Core AI

```text
Gemini
  ↓
Basic Agent
  ↓
Simple question → answer
```

---

### Phase 2: First Knowledge Source

```text
Notion
   ↓
Notion Connector
   ↓
OKF
```

---

### Phase 3: Semantic Retrieval

```text
OKF
 ↓
Chunking
 ↓
Embeddings
 ↓
Qdrant
 ↓
Vector Search
```

---

### Phase 4: Knowledge Graph

```text
OKF
 ↓
Entities + Relationships
 ↓
Neo4j
 ↓
Graph Search
```

---

### Phase 5: Agentic Retrieval

```text
User Query
     ↓
Agent Planner
     ↓
 ┌───┴────┐
 ↓        ↓
Vector   Graph
Search   Search
 └───┬────┘
     ↓
Result Fusion
     ↓
Gemini
     ↓
Answer
```

---

### Phase 6: Coding Knowledge

Add GitHub:

```text
GitHub
   ↓
GitHub Connector
   ↓
OKF
   ↓
Qdrant + Neo4j
```

---

### Phase 7: Project Management

Add Jira:

```text
Jira
   ↓
Jira Connector
   ↓
OKF
   ↓
Knowledge System
```

---

### Phase 8: Enterprise Sources

Finally add:

```text
Google Drive
Gmail
Dropbox
Slack
Confluence
```

---

# 18. 🎯 Final System

Once everything is implemented, the architecture becomes:

```text
                         USER
                           │
                           ▼
                    ┌─────────────┐
                    │ API / AUTH  │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │    AGENT    │
                    │   PLANNER   │
                    └──────┬──────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
        Vector          Keyword         Graph
        Search          Search          Search
            │              │              │
            └──────────────┼──────────────┘
                           ▼
                   Permission Filter
                           │
                           ▼
                    Result Ranking
                           │
                           ▼
                         LLM
                           │
                           ▼
                  Answer + Citations


DATA SOURCES
────────────

 Notion ────────┐
 GitHub ────────┤
 Jira ──────────┤
 Confluence ────┤
 Google Drive ──┤
 Gmail ─────────┤
 Dropbox ───────┤
 Slack ─────────┘
        │
        ▼
   CONNECTORS
        │
        ▼
       OKF
        │
        ├──────────► Qdrant
        │
        ├──────────► Keyword Index
        │
        └──────────► Neo4j
```

## The core idea

Every external service follows the same pattern:

```text
External Service
       ↓
    Connector
       ↓
       OKF
       ↓
Knowledge Storage
       ↓
Retrieval
       ↓
Agent
```

This means adding a new enterprise service does **not** require rebuilding your entire AI system.

You simply add a new connector that converts that service's data into your common **OKF format**.
