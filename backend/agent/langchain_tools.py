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
from backend.retrieval.entity_graph import EntityGraphRetriever
from backend.retrieval.graph import GraphRetriever
from backend.retrieval.keyword import KeywordRetriever
from backend.retrieval.resource_lookup import ResourceLookupRetriever
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
    """Input parameters for BM25+ keyword search."""
    query: str = Field(
        description="Exact technical identifier (e.g. Jira issue key 'PAY-928', PR number '#1842', error code 'HTTP 401', symbol 'AuthService.charge')."
    )
    top_k: int = Field(
        default=5,
        description="Maximum number of exact matches to retrieve (default: 5)."
    )
    source: Optional[str] = Field(
        default=None,
        description="Optional filter by platform: 'github', 'notion', 'dropbox', 'gmail', 'slack'."
    )
    resource_type: Optional[str] = Field(
        default=None,
        description="Optional filter by resource type: 'repository', 'file', 'issue', 'page', 'email', 'playbook'."
    )


class ResourceLookupInput(BaseModel):
    """Input parameters for direct resource lookup."""
    resource_id: str = Field(
        description="Canonical URI or resource ID (e.g. 'github://repo/owner/name', 'notion://vault/master', 'jira://issue/PAY-928', 'https://...')."
    )


class GraphTraversalInput(BaseModel):
    """Input parameters for graph relationship and hierarchy traversal."""
    operation: str = Field(
        description="Graph traversal operation to perform: 'get_children', 'get_neighbors', or 'get_full_sequence'."
    )
    target_id: str = Field(
        description="Parent resource ID (for 'get_children'), chunk ID (for 'get_neighbors'), or sequence ID (for 'get_full_sequence')."
    )
    window_before: int = Field(
        default=1,
        description="Number of preceding sibling chunks to retrieve when operation is 'get_neighbors' (default: 1)."
    )
    window_after: int = Field(
        default=1,
        description="Number of succeeding sibling chunks to retrieve when operation is 'get_neighbors' (default: 1)."
    )
    max_children: int = Field(
        default=20,
        description="Maximum child documents to return when operation is 'get_children' (default: 20)."
    )


class GithubEntitySearchInput(BaseModel):
    """Input parameters for GitHub developer and entity knowledge graph queries."""
    operation: str = Field(
        description=(
            "Graph operation to execute: 'get_pr_details', 'get_user_activity', 'get_file_contributors', "
            "'get_commit_details', 'get_issue_details', 'get_labeled_items', 'get_team_overview', "
            "'get_repo_overview', 'get_neighbors', 'search_nodes', 'find_path', or 'raw_cypher'."
        )
    )
    target: str = Field(
        description="Target identifier (e.g. PR number '#142', username 'alice', file path 'engine.py', commit SHA, label, or Cypher query)."
    )
    parameters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional parameters dict (e.g. direction='both', rel_types=['REVIEWED'], max_depth=2)."
    )


# ── LangChain Tool Factory with RBAC Binding ──────────────────────────────────

def create_langchain_tools(
    semantic_retriever: Optional[SemanticRetriever] = None,
    keyword_retriever: Optional[KeywordRetriever] = None,
    resource_lookup_retriever: Optional[ResourceLookupRetriever] = None,
    graph_retriever: Optional[GraphRetriever] = None,
    entity_graph_retriever: Optional[EntityGraphRetriever] = None,
    bm25_index: Optional[BM25Index] = None,
    user_context: Optional[Dict[str, Any]] = None,
) -> List[BaseTool]:
    """
    Creates standard LangChain BaseTool instances with bound RBAC security context
    across all 5 enterprise retrieval modalities:
      1. `semantic_search`: Dense vector search.
      2. `keyword_search`: Sparse BM25+ search.
      3. `resource_lookup`: Direct URI / document lookup.
      4. `graph_traversal`: Parent-child hierarchy navigation & sibling expansion.
      5. `github_entity_search`: Developer intelligence, PRs, commits, reviews & graph paths.
    """
    retriever = semantic_retriever or SemanticRetriever()
    shared_vector_store = getattr(retriever, "vector_store", None)
    shared_bm25 = bm25_index or getattr(keyword_retriever, "bm25_index", None) or (
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
        resource_type: Optional[str] = None,
    ) -> str:
        results = kw_retriever.search(
            query=query,
            top_k=top_k,
            user_roles=user_roles,
            user_id=user_id,
            user_groups=user_groups,
            source=source,
            resource_type=resource_type,
        )
        return json.dumps(results, ensure_ascii=False)

    def run_resource_lookup(resource_id: str) -> str:
        doc = res_retriever.get_document(resource_id=resource_id, user_context=ctx)
        if doc:
            return json.dumps(doc.get("chunks", []), ensure_ascii=False)
        res = res_retriever.lookup(resource_id=resource_id, user_context=ctx)
        return json.dumps(res, ensure_ascii=False)

    def run_graph_traversal(
        operation: str,
        target_id: str,
        window_before: int = 1,
        window_after: int = 1,
        max_children: int = 20,
    ) -> str:
        if operation == "get_children":
            res = grp_retriever.get_children(
                parent_id=target_id,
                max_children=max_children,
                user_context=ctx,
            )
        elif operation == "get_neighbors":
            res = grp_retriever.get_neighbors(
                chunk_id=target_id,
                window_before=window_before,
                window_after=window_after,
                user_context=ctx,
            )
        elif operation == "get_full_sequence":
            res = grp_retriever.get_full_sequence(
                sequence_id=target_id,
                user_context=ctx,
            )
        else:
            res = []
        return json.dumps(res, ensure_ascii=False)

    def run_entity_search(
        operation: str,
        target: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> str:
        res = ent_retriever.search(
            operation=operation,
            target=target,
            parameters=parameters,
            user_context=ctx,
        )
        if isinstance(res, str):
            return res
        return json.dumps(res, ensure_ascii=False)

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

    resource_tool = StructuredTool.from_function(
        name="resource_lookup",
        description=(
            "Retrieve complete text and metadata of a specific document or resource by its canonical URI "
            "(e.g. 'github://repo/owner/name', 'notion://vault/master', 'jira://issue/PAY-928', 'https://...'), URL, or chunk ID."
        ),
        func=run_resource_lookup,
        args_schema=ResourceLookupInput,
    )

    graph_tool = StructuredTool.from_function(
        name="graph_traversal",
        description=(
            "Traverse knowledge relationships: find child documents under a parent container (get_children), "
            "expand preceding/succeeding sibling chunks (get_neighbors), or assemble multi-step procedures (get_full_sequence)."
        ),
        func=run_graph_traversal,
        args_schema=GraphTraversalInput,
    )

    entity_tool = StructuredTool.from_function(
        name="github_entity_search",
        description=(
            "Query the GitHub knowledge graph for developer relationships, code intelligence, "
            "pull request reviews, author activity, commit diffs, and issue-to-PR links. "
            "Supported operations: 'get_pr_details', 'get_user_activity', 'get_file_contributors', "
            "'get_commit_details', 'get_issue_details', 'get_labeled_items', 'get_team_overview', "
            "'get_repo_overview', 'get_neighbors', 'search_nodes', 'find_path', or 'raw_cypher'."
        ),
        func=run_entity_search,
        args_schema=GithubEntitySearchInput,
    )

    return [semantic_tool, keyword_tool, resource_tool, graph_tool, entity_tool]




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
