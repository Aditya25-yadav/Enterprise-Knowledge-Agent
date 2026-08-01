# Enterprise Knowledge Agent

An AI assistant that behaves like a knowledgeable employee — it retrieves, reasons over, and answers questions using an organization's internal knowledge (docs, chats, tickets, code, wikis) while enforcing per-user access permissions. It combines vector search, keyword search, and a knowledge graph (Graph RAG), normalizes everything into a custom schema called the Open Knowledge Format (OKF), and uses an agentic planner to pick the right retrieval tool per query.

> **Note on scope:** the full version of this (8 live connectors + full graph RAG + RBAC sync) is a multi-person, multi-month build. This README is written for a **narrowed, demo-able version**: 2–3 connectors, synthetic data with realistic permission boundaries, and a working permission-aware hybrid retrieval pipeline. Expand from there once the core loop works.

---

## 1. Project Description

### What it does
- Answers natural-language questions using internal enterprise data
- Only returns information the *asking user* is authorized to see (RBAC-aware)
- Combines three retrieval strategies and fuses the results:
  - **Vector RAG** — semantic similarity search over embedded document chunks
  - **Keyword search** — exact/BM25-style matching for names, IDs, error codes, etc.
  - **Graph RAG** — traverses a knowledge graph of entities/relationships for multi-hop questions ("which projects does the team that owns Service X also support?")
- Cites its sources in every answer
- Exposes a planner/agent that decides which tool(s) to call per query instead of always doing plain vector search

### Core components
| Component | Purpose |
|---|---|
| **Connectors** | Pull data from source platforms via their APIs, normalize into OKF |
| **OKF (Open Knowledge Format)** | Your own standardized schema for chunks, entities, relationships, and permission metadata — not an industry standard, just a consistent internal contract |
| **Permission layer** | Stores/mirrors source-system ACLs, filters retrieval results by requesting user |
| **Retrieval layer** | Vector DB + keyword index + knowledge graph + metadata filters, fused into one ranked result set |
| **Knowledge graph** | Entities and relationships extracted from documents (people, projects, systems, teams) |
| **Agent/planner** | LLM-driven router that picks retrieval strategy per query, can chain multiple tools |
| **API/UI layer** | Where the user actually asks questions and gets cited answers |

### Recommended narrowed scope (for a solo/portfolio project)
- **Connectors:** Confluence + Jira (+ optionally GitHub) — relevant if you're targeting CLM/consulting/analytics roles
- **Data:** synthetic org data with deliberately overlapping and conflicting permissions (e.g. two teams, some shared docs, some restricted docs)
- **Goal:** prove permission-aware hybrid retrieval actually works end-to-end and that you can explain every part of it in an interview

---

## 2. Technical Difficulty (ranked, hardest first)

1. **Permission-aware retrieval** — hardest and most novel part. Two approaches:
   - *Post-filter:* retrieve top-K, then drop unauthorized results (simple, wastes compute, can leak rank signal)
   - *Pre-filter:* attach permission metadata to every chunk at index time, filter during the query itself (better, but requires permissions to stay in sync with the source system)
2. **Knowledge graph construction** — entity/relationship extraction from real text is noisy; entity resolution (is "Q3 report" in Jira the same entity as in Confluence?) is a real problem
3. **Hybrid search fusion** — combining vector score + keyword score + graph relevance into one ranked list (e.g. Reciprocal Rank Fusion, or a lightweight reranker)
4. **Connector maintenance** — each platform has its own auth flow, pagination, rate limits, data model — tedious more than deep, but real time cost
5. **Agent planning / tool selection** — most well-trodden part (function calling / ReAct-style loop); lowest risk

---

## 3. What You Need to Start

### Tech stack (suggested)
- **Backend:** Python (FastAPI)
- **Vector DB:** Qdrant or ChromaDB (both have free/local tiers — see below)
- **Graph DB:** Neo4j (free Community Edition, runs locally via Docker)
- **Keyword search:** Elasticsearch/OpenSearch, or start simpler with SQLite FTS5 or `rank_bm25` in Python
- **Embeddings:** see LLM/embedding table below
- **Orchestration:** LangChain, LlamaIndex, or a hand-rolled agent loop (hand-rolled is more work but you'll understand and be able to explain every part — worth it for an interview project)
- **Auth simulation:** a simple `users.json` / `roles.json` mapping users → groups → document permissions, since you're using synthetic data

### Prerequisites
- Docker (for Neo4j / vector DB / OpenSearch locally)
- Python 3.10+
- API keys for whichever connectors + LLM provider you choose (all free-tier, see below)

---

## 4. Connector APIs Available

| Platform | API | Auth | Permission signal |
|---|---|---|---|
| Google Drive | Drive API v3 + Permissions API | OAuth2 | File/folder-level ACLs |
| Slack | Web API (`conversations.history`, `search.messages`) | OAuth2 | Channel membership |
| Confluence | REST API (Cloud/DC), CQL search | OAuth2 / API token | Space & page-level permissions |
| Notion | REST API — pages, databases, search | Integration token | Coarser — workspace/page-share based |
| GitHub | REST + GraphQL API | OAuth2 / PAT | Repo-level, org-team permissions |
| SharePoint | Microsoft Graph API | Azure AD OAuth2 | Same Graph API covers Outlook/email too |
| Jira | REST API, JQL search | OAuth2 / API token | Project & issue-level permission schemes |
| Gmail/Outlook | Gmail API / Microsoft Graph | OAuth2 | Thread participants = ACL |

All of these are free to *call* (no paid API fee) — you just need a dev account on each platform (Atlassian dev sandbox for Confluence/Jira, a personal GitHub account, etc.).

---

## 5. Free LLM APIs You Can Use

Verified as of mid-2026. All rate-limited, none require a credit card unless noted. Free tiers can tighten or change without notice — build a fallback chain rather than depending on one provider.

| Provider | Free tier (approx.) | Best for | Notes |
|---|---|---|---|
| **Google AI Studio (Gemini)** | Gemini 2.5 Flash: ~1,500 requests/day, 1M-token context, multimodal (text/image/PDF) | Long documents, PDFs, general reasoning | Best overall free frontier-class model; free-tier prompts may be used for training — don't use with sensitive data |
| **Groq** | ~30 requests/min, 100K–500K tokens/day depending on model | Fast short calls, agent tool-routing steps | Hosts Llama 3.3/4, Qwen3, DeepSeek at very high inference speed; good default for the "planner" role in your agent loop |
| **Cerebras** | High-throughput free tier (up to ~2,000 tokens/sec) | Speed-critical calls | Runs open-weight models on custom hardware |
| **Mistral (La Plateforme)** | Free tier on Mistral Small, plus dedicated reasoning (Magistral) and coding (Devstral) models | Second opinion / coding tasks | Check per-model limits individually |
| **OpenRouter** | Aggregates many `:free` model slots behind one OpenAI-compatible API | Model variety, easy provider-swapping | Good as your fallback/router layer |
| **GitHub Models** | Free within dev rate limits | Prototyping with OpenAI/Llama models via GitHub account | Convenient if you're already using GitHub as a connector |
| **Cloudflare Workers AI** | ~10,000 "neurons"/day | Lightweight edge inference | Smaller model catalog |
| **Hugging Face Inference API** | Free tier, rate-limited | Open-weight models, embeddings | Also useful for self-hosting via `transformers`/Ollama if you outgrow hosted free tiers |

### Suggested free stack for this project
- **Planner/agent LLM:** Groq (fast, cheap on latency, good for frequent tool-routing calls)
- **Answer generation / long-context reasoning:** Google Gemini 2.5 Flash (huge context window, handles long retrieved chunks well)
- **Fallback:** OpenRouter free models, so a single provider's rate limit or outage doesn't kill your demo
- **Embeddings (for the vector store):** Gemini's embedding endpoint (free tier) or a local open-weight embedding model via Hugging Face/`sentence-transformers` (fully free, no rate limit, runs on CPU for small datasets)

**Practical tip:** build your LLM calls behind a thin wrapper function from day one (`call_llm(prompt, provider="groq")`) so switching providers when a free tier gets rate-limited is a one-line change, not a rewrite.

---

## 6. Suggested Build Order

1. Define your OKF schema (chunk, entity, relationship, permission fields) on paper first
2. Build 1 connector (Confluence) end-to-end: fetch → normalize to OKF → embed → index
3. Add synthetic permission metadata and prove post-filter retrieval works (user A sees X, user B doesn't)
4. Add a second connector (Jira), confirm cross-source retrieval works
5. Add keyword search, then fuse it with vector search (start with simple RRF)
6. Extract entities/relationships from your indexed docs, load into Neo4j, wire up basic graph traversal
7. Build the agent/planner loop that chooses between vector/keyword/graph tools
8. Add citations to every response
9. Only then consider adding a 3rd connector or a UI polish pass

---

## 7. Open Questions to Decide Before Coding
- Do you simulate permissions (synthetic `users.json`) or do you pull real permission metadata from Confluence/Jira dev accounts?
- Self-hosted vector DB (Qdrant/Chroma, free but you manage it) vs. hosted free tier (Pinecone Starter, has a real free tier with limits)?
- Hand-rolled agent loop vs. LangChain/LlamaIndex — hand-rolled is more work but much easier to explain and defend in an interview

---

*This README is a starting scaffold — update the stack choices and API details as free-tier terms change, and re-verify rate limits before you build anything you plan to demo live.*
