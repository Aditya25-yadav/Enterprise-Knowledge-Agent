"""
Agent Tool Registry & Definitions for Enterprise Knowledge Retrieval.

Defines schemas and execution handlers for retrieval tools invoked by the
Agent Planner (LLM) during reasoning.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from backend.llm.base import ToolDefinition
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.semantic import SemanticRetriever


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
) -> ToolRegistry:
    """
    Creates and populates the standard ToolRegistry with enterprise retrieval tools
    including both dense semantic search and sparse BM25 keyword search.
    """
    registry = ToolRegistry()
    sem_retriever = semantic_retriever or SemanticRetriever()
    kw_retriever = keyword_retriever or KeywordRetriever()

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

    registry.register(semantic_search_def, handle_semantic_search)
    registry.register(keyword_search_def, handle_keyword_search)
    return registry

