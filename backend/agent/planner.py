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
You have access to specialized enterprise retrieval tools to find documentation, architecture guides, code repositories, setup procedures, issues, and communications across GitHub, Notion, Gmail, Dropbox, Jira and Confluence.

Guidelines for Tool Selection:
1. `semantic_search`: Use for natural language questions, conceptual understanding, high-level architecture explanations, setup procedures, runbooks, and policy guidelines.
2. `keyword_search`: Use for exact technical identifiers, Jira issue keys (e.g. 'PAY-928'), GitHub PR numbers (e.g. '#1842'), HTTP/system error codes (e.g. 'HTTP 401', 'ECONNREFUSED'), code symbols/classes (e.g. 'AuthService.charge'), or exact filenames.
3. `resource_lookup`: Use when you already know or discover a specific canonical URI (e.g. 'github://repo/owner/name', 'notion://vault/master', 'jira://issue/PAY-928', 'https://github.com/...'), direct URL, chunk ID, or exact document title, or when you need the complete stitched document content.
4. `graph_traversal`: Use to explore structural document hierarchies:
   - 'get_children': Find all child documents, repository files, sub-issues, or sub-pages under a known parent container.
   - 'get_neighbors': Expand preceding and succeeding sibling chunks around a matched step or section.
   - 'get_full_sequence': Assemble an entire ordered multi-step sequence, runbook, or workflow by its sequence ID.
5. `github_entity_search`: Use for developer relationships, code intelligence, and GitHub entities:
   - 'get_pr_details': Find PR author, reviewers, assignees, modified files, and closed issues.
   - 'get_user_activity': Find PRs authored, commits, reviews, and assigned issues for a developer.
   - 'get_file_contributors': Find commit authors, history, and PRs touching a specific file.
   - 'get_commit_details': Find commit author, message, touched files, and parent PR.
   - 'get_issue_details': Find issue reporter, assignees, labels, and closing PRs.
   - 'get_labeled_items': Find PRs and issues tagged with a specific label.
   - 'get_team_overview': Find team members and accessible repositories.
   - 'get_neighbors' / 'find_path': Generalized multi-hop entity traversal and relationship path finding.
6. Multi-Tool & Multi-Hop Planning:
   - Single-Turn Parallel: If a query combines concepts, identifiers, or developer questions, you may invoke multiple tools in the same turn.
   - Multi-Turn Multi-Hop: If initial search results identify a key PR, commit, or document, make follow-up calls in subsequent turns with `github_entity_search`, `resource_lookup`, or `graph_traversal`.
7. If initial search results are empty or lack specific details, refine your query or traverse adjacent graph nodes.
8. Once sufficient evidence is gathered, formulate a clear, professional, and well-structured answer.
9. Always cite specific evidence when stating facts or steps using bracketed references (e.g. [1], [2]).
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

                    # Accumulate retrieved chunks if output is list of chunk dicts or single entity dict
                    if isinstance(tool_output, list):
                        for idx, c in enumerate(tool_output):
                            if isinstance(c, dict):
                                chunk_item = dict(c)
                                if "chunk_id" not in chunk_item:
                                    chunk_item["chunk_id"] = chunk_item.get("node_id") or f"{tc.tool_name}:{tc.call_id or ''}:{idx}"
                                chunk_item.setdefault("title", chunk_item.get("name") or chunk_item.get("title") or f"Result from {tc.tool_name}")
                                chunk_item.setdefault("source", "github" if "github" in tc.tool_name else "graph")
                                chunk_item.setdefault("text", json.dumps(chunk_item, ensure_ascii=False, indent=2))
                                if not any(existing.get("chunk_id") == chunk_item.get("chunk_id") for existing in accumulated_chunks):
                                    accumulated_chunks.append(chunk_item)
                    elif isinstance(tool_output, dict) and not tool_output.get("error"):
                        chunk_item = dict(tool_output)
                        if "chunk_id" not in chunk_item:
                            chunk_item["chunk_id"] = chunk_item.get("node_id") or f"{tc.tool_name}:{tc.call_id or ''}"
                        chunk_item.setdefault("title", chunk_item.get("title") or chunk_item.get("name") or chunk_item.get("repository") or f"Result from {tc.tool_name}")
                        chunk_item.setdefault("source", "github" if "github" in tc.tool_name else "graph")
                        chunk_item.setdefault("text", json.dumps(chunk_item, ensure_ascii=False, indent=2))
                        if not any(existing.get("chunk_id") == chunk_item.get("chunk_id") for existing in accumulated_chunks):
                            accumulated_chunks.append(chunk_item)

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
