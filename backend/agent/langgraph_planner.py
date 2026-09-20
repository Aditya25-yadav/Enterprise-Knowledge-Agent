r"""
LangGraph Agent Planner & State Machine for Enterprise Knowledge Retrieval.

Orchestrates multi-turn reasoning, parallel tool execution, and evidence-grounded
synthesis using a stateful LangGraph workflow.

Architecture:
               ┌─────────────┐
               │    START    │
               └──────┬──────┘
                      │
                      ▼
               ┌─────────────┐
               │   reasoner  │ ◄──────────┐
               └──────┬──────┘            │
                      │                   │
         [ tools_condition ]              │
          /               \               │
(has tool calls)     (direct answer)      │
        /                   \             │
       ▼                     ▼            │
┌─────────────┐       ┌─────────────┐     │
│  tool_node  │       │  generator  │     │
└──────┬──────┘       └──────┬──────┘     │
       │                     │            │
       └─────────────────────┴────────────┘
                             │
                             ▼
                        ┌─────────┐
                        │   END   │
                        └─────────┘
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph

from backend.agent.state import AgentState
from backend.agent.tools import ToolRegistry, create_default_tool_registry
from backend.generation.answer_generator import AnswerGenerator
from backend.generation.context_builder import ContextBuilder
from backend.llm.base import (
    LLMProvider,
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
)
from backend.llm.factory import get_llm_provider


def _to_internal_messages(langchain_msgs: List[BaseMessage]) -> List[Message]:
    """Converts LangChain messages into provider-agnostic Message objects."""
    result: List[Message] = []
    for m in langchain_msgs:
        if isinstance(m, HumanMessage):
            result.append(Message(role=MessageRole.USER, content=str(m.content)))
        elif isinstance(m, AIMessage):
            tool_calls = [
                ToolCall(
                    tool_name=tc["name"],
                    arguments=tc["args"],
                    call_id=tc.get("id", tc["name"]),
                )
                for tc in getattr(m, "tool_calls", []) or []
            ]
            result.append(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=str(m.content or ""),
                    tool_calls=tool_calls,
                )
            )
        elif isinstance(m, ToolMessage):
            result.append(
                Message(
                    role=MessageRole.TOOL_RESULT,
                    content=str(m.content),
                    tool_name=m.name or "",
                    tool_call_id=m.tool_call_id or "",
                )
            )
        elif isinstance(m, SystemMessage):
            result.append(Message(role=MessageRole.USER, content=f"[System]: {m.content}"))
    return result


class LangGraphAgentPlanner:
    """
    Stateful Enterprise Agent Planner orchestrated by LangGraph.
    """

    SYSTEM_INSTRUCTION = """You are an Enterprise Knowledge Agent.
You have access to specialized enterprise retrieval tools to find documentation, architecture guides, code repositories, setup procedures, and tickets across GitHub, Notion, Gmail, and Dropbox.

Guidelines for Tool Usage:
1. When asked about specific technical setups, architecture, runbooks, or policies, call `semantic_search` with an informative query.
2. If initial search results are empty or lack specific details, refine your query and search again.
3. Once sufficient evidence is gathered, formulate a clear, professional, and well-structured answer.
4. Always cite specific evidence when stating facts or steps (e.g. [1], [2]).
"""

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        tool_registry: Optional[ToolRegistry] = None,
        answer_generator: Optional[AnswerGenerator] = None,
        max_turns: int = 5,
    ) -> None:
        self.llm_provider = llm_provider or get_llm_provider()
        self.tool_registry = tool_registry or create_default_tool_registry()
        self.answer_generator = answer_generator or AnswerGenerator(llm_provider=self.llm_provider)
        self.max_turns = max_turns
        self.graph = self._build_graph()

    # ── Graph Node Implementations ───────────────────────────────────────────

    def _reasoner_node(self, state: AgentState) -> Dict[str, Any]:
        """
        LLM Reasoner Step: Analyzes conversation state and available tools,
        deciding whether to issue ToolCalls or synthesize a direct answer.
        """
        turn_count = state.get("turn_count", 0) + 1
        tools = self.tool_registry.get_definitions()
        internal_messages = _to_internal_messages(state.get("messages", []))

        # Invoke LLM with available tools
        response: LLMResponse = self.llm_provider.generate_with_tools(
            messages=internal_messages,
            tools=tools,
        )

        if response.has_tool_calls:
            # Map ToolCall to LangChain AIMessage tool_calls format
            lc_tool_calls = [
                {
                    "name": tc.tool_name,
                    "args": tc.arguments,
                    "id": tc.call_id or f"call_{tc.tool_name}_{turn_count}",
                }
                for tc in response.tool_calls
            ]
            ai_msg = AIMessage(content=response.content or "", tool_calls=lc_tool_calls)
            return {
                "messages": [ai_msg],
                "turn_count": turn_count,
            }

        # Direct text formulated
        ai_msg = AIMessage(content=response.content or "")
        return {
            "messages": [ai_msg],
            "answer": response.content or "",
            "turn_count": turn_count,
        }

    def _tool_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Tool Execution Node: Executes requested tools in parallel / sequence,
        enforcing user security context (RBAC) and collecting evidence chunks.
        """
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", []) or []
        user_context = state.get("user_context", {})

        new_tool_messages: List[ToolMessage] = []
        accumulated_chunks = list(state.get("retrieved_chunks", []))

        for tc in tool_calls:
            tool_name = tc["name"]
            tool_args = tc["args"]
            call_id = tc["id"]

            # Execute tool handler in registry with RBAC injection
            tool_output = self.tool_registry.execute(
                tool_name=tool_name,
                arguments=tool_args,
                user_context=user_context,
            )

            # Accumulate retrieved chunks if output is a list of chunk dicts
            if isinstance(tool_output, list):
                for c in tool_output:
                    if isinstance(c, dict) and "chunk_id" in c:
                        if not any(existing.get("chunk_id") == c.get("chunk_id") for existing in accumulated_chunks):
                            accumulated_chunks.append(c)

            output_str = json.dumps(tool_output, ensure_ascii=False) if not isinstance(tool_output, str) else tool_output
            new_tool_messages.append(
                ToolMessage(
                    content=output_str,
                    name=tool_name,
                    tool_call_id=call_id,
                )
            )

        return {
            "messages": new_tool_messages,
            "retrieved_chunks": accumulated_chunks,
        }

    def _generator_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Grounded Answer Generator Node: Synthesizes final response using
        evidence chunks and structured citations if not already formulated.
        """
        chunks = state.get("retrieved_chunks", [])
        _, citations = ContextBuilder.build_context(chunks)

        answer = state.get("answer")
        if not answer:
            gen_res = self.answer_generator.generate_answer(
                query=state["query"],
                chunks=chunks,
                conversation_history=_to_internal_messages(state.get("messages", [])),
            )
            answer = gen_res["answer"]

        return {
            "answer": answer,
            "citations": citations,
        }

    # ── Conditional Routing ──────────────────────────────────────────────────

    def _should_continue(self, state: AgentState) -> Literal["tool_node", "generator_node"]:
        """
        Determines the next edge:
          - If reasoner returned tool_calls and max_turns not reached -> 'tool_node'
          - Otherwise -> 'generator_node'
        """
        last_message = state["messages"][-1]
        has_tools = bool(getattr(last_message, "tool_calls", None))
        turn_count = state.get("turn_count", 0)

        if has_tools and turn_count < self.max_turns:
            return "tool_node"
        return "generator_node"

    # ── Graph Assembly ───────────────────────────────────────────────────────

    def _build_graph(self) -> Any:
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("reasoner", self._reasoner_node)
        workflow.add_node("tool_node", self._tool_node)
        workflow.add_node("generator", self._generator_node)

        # Connect edges
        workflow.add_edge(START, "reasoner")
        workflow.add_conditional_edges(
            "reasoner",
            self._should_continue,
            {
                "tool_node": "tool_node",
                "generator_node": "generator",
            },
        )
        workflow.add_edge("tool_node", "reasoner")
        workflow.add_edge("generator", END)

        return workflow.compile()

    # ── Execution Entrypoint ─────────────────────────────────────────────────

    def run(
        self,
        query: str,
        user_context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[BaseMessage]] = None,
    ) -> Dict[str, Any]:
        """
        Executes the compiled LangGraph workflow for a user query.
        """
        user_context = user_context or {
            "roles": ["employee"],
            "user_id": "user@enterprise.com",
            "groups": [],
        }

        initial_messages: List[BaseMessage] = []
        if conversation_history:
            initial_messages.extend(conversation_history)
        initial_messages.append(HumanMessage(content=query))

        initial_state: AgentState = {
            "query": query,
            "user_context": user_context,
            "messages": initial_messages,
            "retrieved_chunks": [],
            "citations": [],
            "answer": "",
            "turn_count": 0,
            "error": None,
        }

        final_state = self.graph.invoke(initial_state)

        # Extract tool execution logs from messages
        executed_tool_calls: List[Dict[str, Any]] = []
        for msg in final_state.get("messages", []):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    executed_tool_calls.append({
                        "tool": tc["name"],
                        "arguments": tc["args"],
                    })

        return {
            "query": query,
            "answer": final_state.get("answer", ""),
            "tool_calls": executed_tool_calls,
            "citations": final_state.get("citations", []),
            "retrieved_chunks": final_state.get("retrieved_chunks", []),
            "turns": final_state.get("turn_count", 1),
            "llm_provider": getattr(self.llm_provider, "__class__", type(self.llm_provider)).__name__,
        }
