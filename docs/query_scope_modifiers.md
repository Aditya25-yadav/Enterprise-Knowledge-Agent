# Query Scope Modifiers & Directives Specification

## 1. Overview & Motivation

The Enterprise Knowledge Agent operates across two distinct knowledge domains:
1. **Proprietary Enterprise Knowledge**: Internal SOPs, runbooks, code repositories, pull requests, Jira bug tickets, Notion workspaces, Dropbox archives, Gmail communications, and Confluence RFCs.
2. **General / Foundational Knowledge**: Standard programming languages, algorithms, conceptual definitions, architecture principles, syntax reference, and conversational history.

While the LangGraph Planner dynamically infers user intent using the `REASONER_SYSTEM_PROMPT` and heuristic pattern routing, giving users **explicit scope modifiers** (e.g. `@enterprise`, `@general`, `@docs`, `@web`, `@jira`) provides deterministic, zero-latency control over the agent's reasoning path.

---

## 2. Command Directives & Scoping Matrix

| Directive / Prefix | Target Knowledge Domain | Tool Execution Behavior | Example Query |
| :--- | :--- | :--- | :--- |
| **`@enterprise`** / **`@docs`** | Internal Enterprise Knowledge | **Forces Enterprise Tools** (`catalog_discovery`, `hybrid_search`, `resource_lookup`, `github_entity_search`). Bypasses direct-answer heuristics. | `@enterprise how do I failover Postgres during DR?` |
| **`@general`** / **`@llm`** | Base LLM Knowledge & Conversation History | **Bypasses All Enterprise Tools**. Answers immediately from LLM weights or chat context with **0s retrieval latency** and **0 tool calls**. | `@general what is the meaning of authentication?` |
| **`@web`** / **`/web`** | Public Web / Internet Documentation | Triggers external web search tool (e.g. Tavily / Google Search / MDN / Python Docs) for public libraries and open standards. | `@web what is the latest release of LangGraph?` |
| **`@github`** | GitHub Connector Only | Pre-filters `hybrid_search` and `github_entity_search` with `source="github"`. | `@github show me PRs authored by alice` |
| **`@jira`** | Jira Connector Only | Pre-filters `hybrid_search` and `resource_lookup` with `source="jira"`. | `@jira status of PAY-928 3DS timeout` |
| **`@notion`** | Notion Connector Only | Pre-filters `hybrid_search` and `resource_lookup` with `source="notion"`. | `@notion setup guide for new engineer workstation` |
| **`@dropbox`** | Dropbox Connector Only | Pre-filters `hybrid_search` and `resource_lookup` with `source="dropbox"`. | `@dropbox OpenSearch cluster reindexing SOP` |
| **`@gmail`** | Gmail Connector Only | Pre-filters `hybrid_search` and `resource_lookup` with `source="gmail"`. | `@gmail post-mortem checkout latency email` |
| **`@confluence`** | Confluence Connector Only | Pre-filters `hybrid_search` and `resource_lookup` with `source="confluence"`. | `@confluence RFC-402 distributed event ingestion` |

---

## 3. End-to-End Architectural Flow

```mermaid
flowchart TD
    UserQuery["User Query<br>e.g. '@general what is authentication?' or '@jira PAY-928'"] --> Parser["Scope Directive Extractor<br>(Regex Pre-Processor)"]
    
    Parser -->|Directive Detected| RouteDecision{"Directive Type?"}
    Parser -->|No Directive| AutoRoute["Dynamic Classifier / Reasoner Node<br>(LangGraph Autonomous Routing)"]
    
    RouteDecision -->|@general / @llm| DirectLLM["Direct LLM Generator Node<br>(0 Tools, 0 Citations, 0.5s Latency)"]
    RouteDecision -->|@enterprise / @docs| EnterpriseTools["Enterprise Map-First Retrieval<br>(catalog_discovery -> hybrid_search)"]
    RouteDecision -->|@connector e.g. @jira| ScopedTools["Targeted Scoped Retrieval<br>(source='jira')"]
    RouteDecision -->|@web| WebSearch["Web Search Provider<br>(External Engine)"]
    
    DirectLLM --> EndNode["Final Answer"]
    EnterpriseTools --> CrossEncoder["Cross-Encoder Reranker"] --> EvidenceEvaluator["Evidence Evaluator"] --> AnswerGen["Grounded Generator"] --> EndNode
    ScopedTools --> CrossEncoder
    WebSearch --> AnswerGen
```

---

## 4. Implementation Specification

### 4.1. Regex Scope Extractor & Query Normalizer

```python
import re
from typing import Optional, Tuple, Dict, Any

SCOPE_PREFIX_PATTERN = re.compile(
    r"^(?:@|/)(enterprise|docs|general|llm|web|github|jira|notion|dropbox|gmail|confluence)\b[\s:]*",
    re.IGNORECASE
)

def parse_query_scope(raw_query: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Extracts explicit scope modifier from user query.
    
    Returns:
        (cleaned_query, scope_directive, target_connector)
        e.g. ("what is authentication?", "general", None)
             ("status of PAY-928", "connector", "jira")
    """
    match = SCOPE_PREFIX_PATTERN.match(raw_query.strip())
    if not match:
        return raw_query.strip(), None, None

    prefix = match.group(1).lower()
    cleaned_query = raw_query[match.end():].strip()

    if prefix in ("general", "llm"):
        return cleaned_query, "general", None
    elif prefix in ("enterprise", "docs"):
        return cleaned_query, "enterprise", None
    elif prefix == "web":
        return cleaned_query, "web", None
    elif prefix in ("github", "jira", "notion", "dropbox", "gmail", "confluence"):
        return cleaned_query, "connector", prefix

    return raw_query.strip(), None, None
```

### 4.2. LangGraph `_reasoner_node` Scope Binding

When `scope_directive == "general"`:
```python
if scope_directive == "general":
    # Immediately formulate direct answer from LLM base knowledge / chat history
    response = self.llm_provider.generate(internal_messages)
    return {
        "messages": [AIMessage(content=response)],
        "answer": response,
        "turn_count": state.get("turn_count", 0) + 1,
    }
```

When `scope_directive == "connector"` (e.g. `@jira`):
```python
if target_connector:
    # Reasoner or tool execution automatically injects source=target_connector
    state["user_context"]["forced_source"] = target_connector
```

---

## 5. TOON (Token-Oriented Object Notation) Formatting Specification

### 5.1. Problem Statement
Standard JSON and verbose Markdown headers consume substantial token budgets ($> 500$ tokens per chunk) for structural metadata (breadcrumb lists, source platforms, permission arrays, timestamps, and schema wrappers). On local models (e.g. LLaMA 3.1 8B, Qwen 2.5 7B), this metadata bloat reduces available context for actual evidence content and increases prompt processing latency.

### 5.2. Proposed TOON Schema
TOON compresses structured concept bundles and candidate chunks into a high-density, delimiter-separated line format:

```toon
[C:sre_dr_v3|S:dropbox|D:Infra|R:employee,engineer|T:Playbook]
# SRE DR Failover Runbook
> Step 1: Verify lag via `patronictl topology`
> Step 2: Failover via `patronictl switchover --master db-node-01`
> Step 3: Switch PgBouncer to `db-node-02`
```

#### TOON Header Key Legend:
* `C:<id>`: Chunk or Concept Identifier
* `S:<source>`: Source Platform (`github`, `jira`, `notion`, `dropbox`, `gmail`, `confluence`)
* `D:<domain>`: Enterprise Domain (`Payments`, `Infra`, `Security`, `Data`, `SRE`)
* `R:<roles>`: Allowed RBAC Roles comma-separated
* `T:<type>`: Asset Type (`File`, `Issue`, `PR`, `Runbook`, `Email`, `Policy`)
* `K:<score>`: Confidence or Relevance Score ($0.0 \dots 1.0$)

### 5.3. Token Savings Matrix
* **Standard JSON Chunk Representation**: $\sim 340\text{ tokens}$ per chunk.
* **Standard Markdown Block Representation**: $\sim 210\text{ tokens}$ per chunk.
* **TOON Dense Representation**: $\sim 85\text{ tokens}$ per chunk (**$\approx 60-75\%$ Token Reduction**).
