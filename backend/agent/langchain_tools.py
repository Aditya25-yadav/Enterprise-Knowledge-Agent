"""
LangChain / LangGraph Native Enterprise Retrieval Tools.

Provides standard LangChain `@tool` definitions and `BaseTool` wrappers for:
  1. `semantic_search`: Dense vector search over Qdrant with local Qwen embeddings.
  2. `keyword_search`: BM25Plus sparse lexical search for exact IDs, PRs, and code symbols.
  3. `resource_lookup`: Exact document lookup by canonical URI or document ID.

Compatible with LangGraph's `ToolNode` and standard tool-calling agent graphs.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from langchain_core.tools import BaseTool, StructuredTool, tool
from pydantic import BaseModel, Field

from backend.agent.tools import ToolRegistry
from backend.retrieval.semantic import SemanticRetriever
from backend.storage.bm25_index import BM25Index


# ── Pydantic Schemas for LangChain Tool Arguments ─────────────────────────────

class SemanticSearchInput(BaseModel):
    """Input parameters for semantic vector search."""
    query: str = Field(
        description="Natural language query describing technical concepts, runbooks, architecture, or policies."
    )
    top_k: int = Field(
        default=5,
        description="Maximum number of relevant chunks to retrieve (default: 5)."
    )
    source: Optional[str] = Field(
        default=None,
        description="Optional filter by platform: 'github', 'notion', 'dropbox', 'gmail', 'slack'."
    )
    resource_type: Optional[str] = Field(
        default=None,
        description="Optional filter by resource type: 'repository', 'file', 'issue', 'page', 'email', 'playbook'."
    )


class KeywordSearchInput(BaseModel):
    """Input parameters for BM25 exact keyword search."""
    query: str = Field(
        description="Exact identifier, ticket ID (e.g. 'PAY-928'), PR number ('#1842'), error code ('HTTP 401'), or symbol ('AuthService.validate_token')."
    )
    top_k: int = Field(
        default=5,
        description="Maximum number of exact matches to retrieve (default: 5)."
    )
    source: Optional[str] = Field(
        default=None,
        description="Optional filter by platform: 'github', 'notion', 'dropbox', 'gmail', 'slack'."
    )


class ResourceLookupInput(BaseModel):
    """Input parameters for direct resource lookup."""
    resource_id: str = Field(
        description="Canonical URI or resource ID (e.g. 'notion://page/123' or 'github://repo/owner/name')."
    )


# ── LangChain Tool Factory with RBAC Binding ──────────────────────────────────

def create_langchain_tools(
    semantic_retriever: Optional[SemanticRetriever] = None,
    bm25_index: Optional[BM25Index] = None,
    user_context: Optional[Dict[str, Any]] = None,
) -> List[BaseTool]:
    """
    Creates standard LangChain BaseTool instances with bound RBAC security context.
    
    Can be passed directly into LangGraph's ToolNode:
        tools = create_langchain_tools(semantic_retriever, bm25_index, user_context)
        tool_node = ToolNode(tools)
    """
    retriever = semantic_retriever or SemanticRetriever()
    keyword_index = bm25_index or BM25Index()
    ctx = user_context or {"roles": ["employee"], "user_id": "user@enterprise.com", "groups": []}

    user_roles = ctx.get("roles") or ctx.get("allowed_roles")
    user_id = ctx.get("user_id")
    user_groups = ctx.get("groups")

    def run_semantic_search(
        query: str,
        top_k: int = 5,
        source: Optional[str] = None,
        resource_type: Optional[str] = None,
    ) -> str:
        results = retriever.search(
            query=query,
            top_k=top_k,
            user_roles=user_roles,
            user_id=user_id,
            user_groups=user_groups,
            source=source,
            resource_type=resource_type,
        )
        return json.dumps(results, ensure_ascii=False)

    def run_keyword_search(
        query: str,
        top_k: int = 5,
        source: Optional[str] = None,
    ) -> str:
        results = keyword_index.search(
            query=query,
            top_k=top_k,
            user_roles=user_roles,
            user_id=user_id,
            user_groups=user_groups,
            source=source,
        )
        return json.dumps(results, ensure_ascii=False)

    semantic_tool = StructuredTool.from_function(
        name="semantic_search",
        description=(
            "Search enterprise documentation, setup guides, and architecture using semantic vector similarity. "
            "Best for conceptual inquiries, runbook workflows, and policy guidelines."
        ),
        func=run_semantic_search,
        args_schema=SemanticSearchInput,
    )

    keyword_tool = StructuredTool.from_function(
        name="keyword_search",
        description=(
            "Search for exact technical identifiers: Jira keys (PAY-928), PR numbers (#1842), "
            "error codes (HTTP 401, ECONNREFUSED), code symbols (AuthService.charge), or file paths."
        ),
        func=run_keyword_search,
        args_schema=KeywordSearchInput,
    )

    return [semantic_tool, keyword_tool]


def convert_registry_to_langchain_tools(
    tool_registry: ToolRegistry,
    user_context: Optional[Dict[str, Any]] = None,
) -> List[BaseTool]:
    """
    Bridges our existing ToolRegistry into LangChain BaseTools.
    """
    ctx = user_context or {}
    lc_tools = []

    for tool_def in tool_registry.get_definitions():
        name = tool_def.name
        desc = tool_def.description

        def make_handler(t_name: str):
            def handler(**kwargs) -> str:
                res = tool_registry.execute(tool_name=t_name, arguments=kwargs, user_context=ctx)
                if isinstance(res, str):
                    return res
                return json.dumps(res, ensure_ascii=False)
            return handler

        lc_tool = StructuredTool.from_function(
            name=name,
            description=desc,
            func=make_handler(name),
        )
        lc_tools.append(lc_tool)

    return lc_tools
