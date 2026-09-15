"""
Agent Planner & Autonomous Reasoning Loop for Enterprise Knowledge Agent.

Orchestrates multi-turn tool calling:
  User Query
      │
      ▼
  [ LLM Planner ] ◄──► [ Tool Execution (semantic_search, etc.) ]
      │ (autonomous loop up to max_turns)
      ▼
  Grounded Answer + Evidence Citations
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.agent.tools import ToolRegistry, create_default_tool_registry
from backend.generation.answer_generator import AnswerGenerator
from backend.generation.context_builder import ContextBuilder
from backend.llm.base import LLMProvider, LLMResponse, Message, MessageRole
from backend.llm.factory import get_llm_provider
from backend.retrieval.semantic import SemanticRetriever


@dataclass
class AgentResult:
    """
    Structured outcome of an agent execution turn.
    """
    query: str
    answer: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list) # Audit log of tools called
    citations: List[Dict[str, str]] = field(default_factory=list)  # Numbered source citations
    retrieved_chunks: List[Dict[str, Any]] = field(default_factory=list)
    turns: int = 1
    llm_provider: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "tool_calls": self.tool_calls,
            "citations": self.citations,
            "chunks_retrieved_count": len(self.retrieved_chunks),
            "turns": self.turns,
            "llm_provider": self.llm_provider,
        }


class AgentPlanner:
    """
    Autonomous enterprise agent planner that dynamically invokes retrieval tools
    to resolve user questions with grounded evidence.
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

    def run(
        self,
        query: str,
        user_context: Optional[Dict[str, Any]] = None,
        conversation_history: Optional[List[Message]] = None,
    ) -> AgentResult:
        """
        Executes the autonomous agent reasoning loop for a user query.
        """
        user_context = user_context or {
            "roles": ["employee"],
            "user_id": "user@company.com",
            "groups": [],
        }

        messages: List[Message] = []
        if conversation_history:
            messages.extend(conversation_history)

        messages.append(Message(role=MessageRole.USER, content=query))

        tools = self.tool_registry.get_definitions()
        executed_tool_calls: List[Dict[str, Any]] = []
        accumulated_chunks: List[Dict[str, Any]] = []

        turn_count = 0
        final_answer: Optional[str] = None

        while turn_count < self.max_turns:
            turn_count += 1

            # Request LLM step with tools
            response: LLMResponse = self.llm_provider.generate_with_tools(
                messages=messages,
                tools=tools,
            )

            # Case A: LLM requested tool execution
            if response.has_tool_calls:
                # Add assistant message with tool calls to conversation history
                messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=response.content or "",
                        tool_calls=response.tool_calls,
                    )
                )

                for tc in response.tool_calls:
                    # Execute tool in registry
                    tool_output = self.tool_registry.execute(
                        tool_name=tc.tool_name,
                        arguments=tc.arguments,
                        user_context=user_context,
                    )

                    # Log execution
                    executed_tool_calls.append({
                        "turn": turn_count,
                        "tool": tc.tool_name,
                        "arguments": tc.arguments,
                        "results_count": len(tool_output) if isinstance(tool_output, list) else 1,
                    })

                    # Accumulate retrieved chunks if output is list of chunk dicts
                    if isinstance(tool_output, list):
                        for c in tool_output:
                            if isinstance(c, dict) and "chunk_id" in c:
                                if not any(existing.get("chunk_id") == c.get("chunk_id") for existing in accumulated_chunks):
                                    accumulated_chunks.append(c)

                    # Append tool result to conversation history
                    output_str = json.dumps(tool_output, ensure_ascii=False) if not isinstance(tool_output, str) else tool_output
                    messages.append(
                        Message(
                            role=MessageRole.TOOL_RESULT,
                            content=output_str,
                            tool_name=tc.tool_name,
                        )
                    )

            # Case B: LLM formulated final text response
            elif response.is_text:
                final_answer = response.content
                break

        # If LLM ended without generating text (or max turns reached), generate answer from evidence
        if not final_answer:
            gen_res = self.answer_generator.generate_answer(
                query=query,
                chunks=accumulated_chunks,
                conversation_history=conversation_history,
            )
            final_answer = gen_res["answer"]

        # Build structured citations
        _, citations = ContextBuilder.build_context(accumulated_chunks)

        provider_name = getattr(self.llm_provider, "__class__", type(self.llm_provider)).__name__

        return AgentResult(
            query=query,
            answer=final_answer,
            tool_calls=executed_tool_calls,
            citations=citations,
            retrieved_chunks=accumulated_chunks,
            turns=turn_count,
            llm_provider=provider_name,
        )
