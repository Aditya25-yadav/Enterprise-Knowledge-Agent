"""
Agent Tool Registry & Definitions for Enterprise Knowledge Retrieval.

Defines schemas and execution handlers for retrieval tools invoked by the
Agent Planner (LLM) during reasoning.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from backend.llm.base import ToolDefinition
from backend.retrieval.entity_graph import EntityGraphRetriever
from backend.retrieval.graph import GraphRetriever
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
        try:
            # Pass user_context if the handler accepts it
            return handler(arguments=arguments, user_context=user_context or {})
        except TypeError:
            # Fallback if handler only takes arguments
            return handler(**arguments)


def create_default_tool_registry(
    semantic_retriever: Optional[SemanticRetriever] = None,
    keyword_retriever: Optional[KeywordRetriever] = None,
    resource_lookup_retriever: Optional[ResourceLookupRetriever] = None,
    graph_retriever: Optional[GraphRetriever] = None,
    entity_graph_retriever: Optional[EntityGraphRetriever] = None,
) -> ToolRegistry:
    """
    Creates and populates the standard ToolRegistry with all enterprise retrieval tools:
    1. `semantic_search`: Dense vector search (concepts, guides, policies).
    2. `keyword_search`: Sparse BM25+ search (exact IDs, error codes, symbols).
    3. `resource_lookup`: Direct lookup by canonical URI/URL to fetch full documents.
    4. `graph_traversal`: Parent-child hierarchy navigation & sibling expansion.
    5. `github_entity_search`: Developer intelligence, PRs, commits, reviews & graph paths.
    """
    registry = ToolRegistry()
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

    # ── 1. Semantic Search Tool ──────────────────────────────────────────────
    semantic_search_def = ToolDefinition(
        name="semantic_search",
        description=(
            "Search the enterprise knowledge base for documents, architectural guides, setup steps, "
            "runbooks, and repositories using semantic vector search. Use this tool for conceptual "
            "questions, architecture explanations, setup procedures, and policy inquiries."
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
                    "description": "Optional filter by platform: 'github', 'notion', 'dropbox', 'gmail', 'slack'.",
                    "enum": ["github", "notion", "dropbox", "gmail", "slack"],
                },
                "resource_type": {
                    "type": "string",
                    "description": "Optional filter by resource type: 'repository', 'file', 'issue', 'page', 'email', 'playbook'.",
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

    def handle_semantic_search(arguments: Dict[str, Any], user_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        source = arguments.get("source")
        resource_type = arguments.get("resource_type")

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
            "(e.g. '#1842'), HTTP error codes ('HTTP 401', 'ECONNREFUSED'), symbol names "
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
                    "description": "Optional filter by platform: 'github', 'notion', 'dropbox', 'gmail', 'slack'.",
                    "enum": ["github", "notion", "dropbox", "gmail", "slack"],
                },
                "resource_type": {
                    "type": "string",
                    "description": "Optional filter by resource type: 'repository', 'file', 'issue', 'page', 'email', 'playbook'.",
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

    def handle_keyword_search(arguments: Dict[str, Any], user_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        query = arguments.get("query", "")
        top_k = arguments.get("top_k", 5)
        source = arguments.get("source")
        resource_type = arguments.get("resource_type")

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
            "Retrieve the complete text and metadata of a specific document or resource by its "
            "canonical URI (e.g. 'github://repo/owner/name', 'notion://vault/master', 'jira://issue/PAY-928', "
            "'https://github.com/...'), direct URL, chunk ID, or exact title."
        ),
        parameters={
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "Canonical URI, URL, chunk ID, or exact document title to look up.",
                },
            },
            "required": ["resource_id"],
        },
    )

    def handle_resource_lookup(arguments: Dict[str, Any], user_context: Dict[str, Any]) -> Any:
        resource_id = arguments.get("resource_id", "")
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

    def handle_graph_traversal(arguments: Dict[str, Any], user_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        operation = arguments.get("operation", "get_children")
        target_id = arguments.get("target_id", "")

        if operation == "get_children":
            max_children = arguments.get("max_children", 20)
            return grp_retriever.get_children(
                parent_id=target_id,
                max_children=max_children,
                user_context=user_context,
            )
        elif operation == "get_neighbors":
            window_before = arguments.get("window_before", 1)
            window_after = arguments.get("window_after", 1)
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

    def handle_entity_search(arguments: Dict[str, Any], user_context: Dict[str, Any]) -> Any:
        operation = arguments.get("operation", "get_pr_details")
        target = arguments.get("target", "")
        params = arguments.get("parameters")
        return ent_retriever.search(
            operation=operation,
            target=target,
            parameters=params,
            user_context=user_context,
        )

    registry.register(semantic_search_def, handle_semantic_search)
    registry.register(keyword_search_def, handle_keyword_search)
    registry.register(resource_lookup_def, handle_resource_lookup)
    registry.register(graph_traversal_def, handle_graph_traversal)
    registry.register(github_entity_search_def, handle_entity_search)
    return registry


