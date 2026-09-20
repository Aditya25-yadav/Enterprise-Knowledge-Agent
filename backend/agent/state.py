"""
State Definition for Enterprise Knowledge Agent in LangGraph.

Defines the central TypedDict state schema managed across graph nodes and edges.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """
    Central state schema for the LangGraph enterprise agent.

    Fields:
      - query: Original natural language user query.
      - user_context: Security context (roles, user_id, groups) for RBAC enforcement.
      - messages: List of conversation messages (managed with LangGraph's add_messages reducer).
      - retrieved_chunks: Accumulated, deduplicated evidence chunks.
      - citations: Structured citation cards for UI rendering and provenance attribution.
      - answer: Final grounded answer synthesized from evidence.
      - turn_count: Number of reasoning / execution turns.
      - error: Optional error message if execution encounters an exception.
    """
    query: str
    user_context: Dict[str, Any]
    messages: Annotated[List[Any], add_messages]
    retrieved_chunks: List[Dict[str, Any]]
    citations: List[Dict[str, Any]]
    answer: str
    turn_count: int
    error: Optional[str]
