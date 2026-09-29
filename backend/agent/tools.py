"""
Agent Tool Registry & Definitions for Enterprise Knowledge Retrieval.

Defines schemas and execution handlers for retrieval tools invoked by the
Agent Planner (LLM) during reasoning.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from backend.llm.base import ToolDefinition
from backend.retrieval.catalog import CatalogRetriever
from backend.retrieval.entity_graph import EntityGraphRetriever
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.hybrid import HybridRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index
from backend.storage.qdrant_client import QdrantVectorStore


class ToolRegistry:
    """
    Registry that manages tool definitions and their execution handlers.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, Callable[..., Any]] = {}

    def register(
        self,
        definition: ToolDefinition,
        handler: Callable[..., Any],
    ) -> None:
        """Registers a tool definition and its runtime callable handler."""
        self._tools[definition.name] = definition
        self._handlers[definition.name] = handler

    def get_definitions(self) -> List[ToolDefinition]:
        """Returns list of ToolDefinitions to pass to the LLM."""
        return list(self._tools.values())

    def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Executes the registered handler for the given tool name, passing arguments
        and user security context (roles, user_id, groups).
        """
        if tool_name not in self._handlers:
            return {
                "error": f"Tool '{tool_name}' not found. Available tools: {list(self._tools.keys())}"
            }

        handler = self._handlers[tool_name]
        user_ctx = user_context or {}
        
        import inspect
        sig = inspect.signature(handler)
        params = sig.parameters

        if "arguments" in params and "user_context" in params:
            return handler(arguments=arguments, user_context=user_ctx)
        elif "arguments" in params:
            return handler(arguments=arguments)
        else:
            call_kwargs = dict(arguments)
            if "user_context" in params:
                call_kwargs["user_context"] = user_ctx
            return handler(**call_kwargs)


def create_default_tool_registry(
    semantic_retriever: Optional[SemanticRetriever] = None,
    keyword_retriever: Optional[KeywordRetriever] = None,
    resource_lookup_retriever: Optional[ResourceLookupRetriever] = None,
    graph_retriever: Optional[GraphRetriever] = None,
    entity_graph_retriever: Optional[EntityGraphRetriever] = None,
    hybrid_retriever: Optional[HybridRetriever] = None,
    catalog_retriever: Optional[CatalogRetriever] = None,
) -> ToolRegistry:
    """
    Creates and populates the standard ToolRegistry with all enterprise retrieval tools:
    1. `catalog_discovery`: PRIMARY MAP-FIRST DISCOVERY TOOL across all 6 platforms.
    2. `hybrid_search`: Unified multi-modal fusion search combining vector, BM25, and graph via RRF.
    3. `semantic_search`: Dense vector search (concepts, guides, policies).
    4. `keyword_search`: Sparse BM25+ search (exact IDs, error codes, symbols).
    5. `resource_lookup`: Direct lookup by canonical URI/URL to fetch full documents.
    6. `graph_traversal`: Parent-child hierarchy navigation & sibling expansion.
    7. `github_entity_search`: Developer intelligence, PRs, commits, reviews & graph paths.
    """
    registry = ToolRegistry()
    cat_retriever = catalog_retriever or CatalogRetriever()
    sem_retriever = semantic_retriever or SemanticRetriever()
    shared_vector_store = getattr(sem_retriever, "vector_store", None)
    shared_bm25 = getattr(keyword_retriever, "bm25_index", None) or (
        BM25Index() if keyword_retriever is None else None
    )

    kw_retriever = keyword_retriever or KeywordRetriever(bm25_index=shared_bm25)
    if shared_bm25 is None:
        shared_bm25 = getattr(kw_retriever, "bm25_index", None)

    res_retriever = resource_lookup_retriever or ResourceLookupRetriever(
        bm25_index=shared_bm25,
        vector_store=shared_vector_store,
    )
    grp_retriever = graph_retriever or GraphRetriever(
        bm25_index=shared_bm25,
        vector_store=shared_vector_store,
    )
    ent_retriever = entity_graph_retriever or EntityGraphRetriever()
    hyb_retriever = hybrid_retriever or HybridRetriever(
        semantic_retriever=sem_retriever,
        keyword_retriever=kw_retriever,
        entity_graph_retriever=ent_retriever,
        graph_retriever=grp_retriever,
    )

    # ── 0. Catalog Discovery Tool (Map-First Navigation) ──────────────────────
    catalog_discovery_def = ToolDefinition(
        name="catalog_discovery",
        description=(
            "Inspect the Global Master Index (global_index.md) and knowledge sync ledger (global_log.md) across all 6 "
            "platforms (GitHub, Jira, Notion, Dropbox, Gmail, Confluence) to discover relevant enterprise assets, runbooks, "
            "PRs, and tickets for enterprise-specific queries. DO NOT use for general definitions or conversation history."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language question, topic, system name, or entity keywords to discover across the global index.",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional domain filter (e.g. 'Payments & Checkout', 'Infrastructure & Disaster Recovery', 'Security & Cryptography', 'Data Platform & Messaging', 'Incident Response & Reliability').",
                },
                "connector": {
                    "type": "string",
                    "description": "Optional source platform filter ('github', 'jira', 'notion', 'dropbox', 'gmail', 'confluence').",
                    "enum": ["github", "jira", "notion", "dropbox", "gmail", "confluence"],
                },
            },
            "required": ["query"],
        },
    )

    def handle_catalog_discovery(
        arguments: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        args = dict(arguments or {})
        args.update(kwargs)
        query = str(args.get("query") or "")
        domain = args.get("domain")
        connector = args.get("connector")

        return cat_retriever.discover(
            query=query,
            user_context=user_context,
            domain=domain,
            connector=connector,
        )

    # ── 1. Semantic Search Tool ──────────────────────────────────────────────
    semantic_search_def = ToolDefinition(
        name="semantic_search",
        description=(
            "Search enterprise knowledge base for documents, runbooks, SOPs, setup steps, architectural guides, "
            "policies, and files across all platforms (Dropbox, Notion, Confluence, Gmail, Jira, GitHub) using semantic vector search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query describing the concepts, topics, or instructions to find.",
                },
                "source": {
                    "type": "string",
                    "description": "DO NOT set or guess this parameter unless the user EXPLICITLY requested a specific platform in their query (e.g. 'search in Jira'). Omit or leave empty to search all platforms (RECOMMENDED).",
                    "enum": ["github", "notion", "dropbox", "gmail", "confluence", "jira"],
                },
                "resource_type": {
                    "type": "string",
                    "description": "DO NOT set or guess this parameter unless the user EXPLICITLY asked for a specific type. Omit or leave empty to search all (RECOMMENDED).",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of relevant chunks to retrieve (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )

    def handle_semantic_search(
        arguments: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        args = dict(arguments or {})
        args.update(kwargs)
        query = str(args.get("query") or "")
        try:
            top_k = int(args.get("top_k", 5))
        except (ValueError, TypeError):
            top_k = 5
        source = args.get("source")
        resource_type = args.get("resource_type")

        return sem_retriever.search(
            query=query,
            top_k=top_k,
            source=source,
            resource_type=resource_type,
            user_context=user_context,
        )

    # ── 2. Keyword Search Tool (BM25) ─────────────────────────────────────────
    keyword_search_def = ToolDefinition(
        name="keyword_search",
        description=(
            "Search for exact technical identifiers: Jira issue keys (e.g. 'PAY-928'), GitHub PR numbers "
            "(e.g. '#142'), HTTP error codes ('HTTP 401', 'ECONNREFUSED'), symbol names "
            "('AuthService.charge'), or specific filenames using BM25+ keyword search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Exact identifier, ticket key, error code, symbol name, or filename to find.",
                },
                "source": {
                    "type": "string",
                    "description": "DO NOT set or guess this parameter unless the user EXPLICITLY requested a specific platform in their query (e.g. 'search in Jira'). Omit or leave empty to search all platforms (RECOMMENDED).",
                    "enum": ["github", "notion", "dropbox", "gmail", "confluence", "jira"],
                },
                "resource_type": {
                    "type": "string",
                    "description": "DO NOT set or guess this parameter unless the user EXPLICITLY asked for a specific type. Omit or leave empty to search all (RECOMMENDED).",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of matching chunks to retrieve (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )

    def handle_keyword_search(
        arguments: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        args = dict(arguments or {})
        args.update(kwargs)
        query = str(args.get("query") or "")
        try:
            top_k = int(args.get("top_k", 5))
        except (ValueError, TypeError):
            top_k = 5
        source = args.get("source")
        resource_type = args.get("resource_type")

        return kw_retriever.search(
            query=query,
            top_k=top_k,
            source=source,
            resource_type=resource_type,
            user_context=user_context,
        )

    # ── 3. Resource Lookup Tool ───────────────────────────────────────────────
    resource_lookup_def = ToolDefinition(
        name="resource_lookup",
        description=(
            "Retrieve the complete sequential text of a specific known document, runbook, or ticket by exact "
            "document title, canonical URI (e.g. 'jira://issue/PAY-928', 'https://...'), URL, or document ID. "
            "Use ONLY when you already know the specific document title or ticket key to read. "
            "For general topic searching, finding relevant chunks, or broad inquiries, use hybrid_search instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "Exact document title, Jira ticket key (e.g. 'PAY-928'), canonical URI, URL, or chunk ID to retrieve in full.",
                },
            },
            "required": ["resource_id"],
        },
    )

    def handle_resource_lookup(
        arguments: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        args = dict(arguments or {})
        args.update(kwargs)
        resource_id = str(args.get("resource_id") or "")
        doc = res_retriever.get_document(resource_id=resource_id, user_context=user_context)
        if doc:
            return doc.get("chunks", [])
        return res_retriever.lookup(resource_id=resource_id, user_context=user_context)

    # ── 4. Graph Traversal Tool ───────────────────────────────────────────────
    graph_traversal_def = ToolDefinition(
        name="graph_traversal",
        description=(
            "Traverse knowledge relationships and hierarchies: find child resources under a parent container "
            "(operation: 'get_children'), expand preceding and succeeding sibling chunks around a matched step "
            "(operation: 'get_neighbors'), or assemble an entire multi-step procedure by sequence ID "
            "(operation: 'get_full_sequence')."
        ),
        parameters={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "Graph traversal operation: 'get_children', 'get_neighbors', or 'get_full_sequence'.",
                    "enum": ["get_children", "get_neighbors", "get_full_sequence"],
                },
                "target_id": {
                    "type": "string",
                    "description": "The parent resource ID (for get_children), chunk ID (for get_neighbors), or sequence ID (for get_full_sequence).",
                },
                "window_before": {
                    "type": "integer",
                    "description": "Number of preceding sibling chunks to retrieve (for get_neighbors, default: 1).",
                    "default": 1,
                },
                "window_after": {
                    "type": "integer",
                    "description": "Number of succeeding sibling chunks to retrieve (for get_neighbors, default: 1).",
                    "default": 1,
                },
                "max_children": {
                    "type": "integer",
                    "description": "Maximum child documents to return (for get_children, default: 20).",
                    "default": 20,
                },
            },
            "required": ["operation", "target_id"],
        },
    )

    def handle_graph_traversal(
        arguments: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        args = dict(arguments or {})
        args.update(kwargs)
        operation = str(args.get("operation") or "get_children")
        target_id = str(args.get("target_id") or "")

        if operation == "get_children":
            try:
                max_children = int(args.get("max_children", 20))
            except (ValueError, TypeError):
                max_children = 20
            return grp_retriever.get_children(
                parent_id=target_id,
                max_children=max_children,
                user_context=user_context,
            )
        elif operation == "get_neighbors":
            try:
                window_before = int(args.get("window_before", 1))
            except (ValueError, TypeError):
                window_before = 1
            try:
                window_after = int(args.get("window_after", 1))
            except (ValueError, TypeError):
                window_after = 1
            return grp_retriever.get_neighbors(
                chunk_id=target_id,
                window_before=window_before,
                window_after=window_after,
                user_context=user_context,
            )
        elif operation == "get_full_sequence":
            return grp_retriever.get_full_sequence(
                sequence_id=target_id,
                user_context=user_context,
            )
        return []

    # ── 5. GitHub Entity Graph & Code Intelligence Tool ───────────────────────
    github_entity_search_def = ToolDefinition(
        name="github_entity_search",
        description=(
            "Query the GitHub knowledge graph for developer relationships, code intelligence, "
            "pull request reviews, author activity, commit diffs, and issue-to-PR links. "
            "Supported operations: 'get_pr_details', 'get_user_activity', 'get_file_contributors', "
            "'get_commit_details', 'get_issue_details', 'get_labeled_items', 'get_team_overview', "
            "'get_repo_overview', 'get_neighbors', 'search_nodes', 'find_path', or 'raw_cypher'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "Graph operation to execute.",
                    "enum": [
                        "get_pr_details",
                        "get_user_activity",
                        "get_file_contributors",
                        "get_commit_details",
                        "get_issue_details",
                        "get_labeled_items",
                        "get_team_overview",
                        "get_repo_overview",
                        "get_neighbors",
                        "search_nodes",
                        "find_path",
                        "raw_cypher",
                    ],
                },
                "target": {
                    "type": "string",
                    "description": "Target identifier (e.g. PR number '#142', username 'alice', file path 'engine.py', commit SHA, label name, or Cypher query).",
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional parameters dictionary (e.g. direction='both', rel_types=['REVIEWED'], max_depth=2, property_filters={}).",
                },
            },
            "required": ["operation", "target"],
        },
    )

    def handle_entity_search(
        arguments: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        args = dict(arguments or {})
        args.update(kwargs)
        operation = str(args.get("operation") or "get_pr_details")
        target = str(args.get("target") or "")
        params = args.get("parameters")
        return ent_retriever.search(
            operation=operation,
            target=target,
            parameters=params,
            user_context=user_context,
        )

    # ── 6. Hybrid Multi-Modal Search Tool (RRF) ───────────────────────────────
    hybrid_search_def = ToolDefinition(
        name="hybrid_search",
        description=(
            "Preferred default search tool: Executes unified multi-modal hybrid search across vector embeddings, BM25+ keywords, "
            "and knowledge graphs using Reciprocal Rank Fusion (RRF). Ideal for searching runbooks, SOPs, procedures, error codes, "
            "and technical documentation across all enterprise platforms (Dropbox, Notion, Confluence, Gmail, Jira, GitHub)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language question or search query combining concepts and identifiers.",
                },
                "modalities": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["vector", "keyword", "graph"]},
                    "description": "Optional list of modalities to run (default: ['vector', 'keyword', 'graph']).",
                },
                "source": {
                    "type": "string",
                    "description": "DO NOT set or guess this parameter unless the user EXPLICITLY requested a specific platform in their query (e.g. 'search in Jira'). Omit or leave empty to search all platforms (RECOMMENDED).",
                    "enum": ["github", "notion", "dropbox", "gmail", "confluence", "jira"],
                },
                "resource_type": {
                    "type": "string",
                    "description": "DO NOT set or guess this parameter unless the user EXPLICITLY asked for a specific type. Omit or leave empty to search all (RECOMMENDED).",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of fused results to return (default: 5).",
                    "default": 5,
                },
                "k": {
                    "type": "integer",
                    "description": "RRF smoothing constant (default: 60).",
                    "default": 60,
                },
            },
            "required": ["query"],
        },
    )

    def handle_hybrid_search(
        arguments: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        args = dict(arguments or {})
        args.update(kwargs)
        query = str(args.get("query") or "")
        try:
            top_k = int(args.get("top_k", 5))
        except (ValueError, TypeError):
            top_k = 5
        modalities = args.get("modalities")
        source = args.get("source")
        resource_type = args.get("resource_type")
        try:
            k = int(args.get("k", 60))
        except (ValueError, TypeError):
            k = 60

        metadata_filters = {}
        if source:
            metadata_filters["source"] = source
        if resource_type:
            metadata_filters["resource_type"] = resource_type

        return hyb_retriever.search(
            query=query,
            top_k=top_k,
            modalities=modalities,
            k=k,
            user_context=user_context,
            metadata_filters=metadata_filters if metadata_filters else None,
        )

    registry.register(catalog_discovery_def, handle_catalog_discovery)
    registry.register(hybrid_search_def, handle_hybrid_search)
    registry.register(semantic_search_def, handle_semantic_search)
    registry.register(keyword_search_def, handle_keyword_search)
    registry.register(resource_lookup_def, handle_resource_lookup)
    registry.register(graph_traversal_def, handle_graph_traversal)
    registry.register(github_entity_search_def, handle_entity_search)
    return registry


