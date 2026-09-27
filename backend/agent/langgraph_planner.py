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

from backend.agent.query_scope import parse_query_scope
from backend.agent.reformulator import QueryReformulator
from backend.agent.state import AgentState, trim_conversation_history
from backend.agent.tools import ToolRegistry, create_default_tool_registry
from backend.evaluation.evaluator import EvidenceEvaluator
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
from backend.models.evaluation import EvaluationResult, RecommendedAction
from backend.ranking.reranker import CrossEncoderReranker
from backend.storage.checkpointers import BaseCheckpointSaver, get_checkpointer


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
            result.append(Message(role=MessageRole.SYSTEM, content=str(m.content)))
    return result


class LangGraphAgentPlanner:
    """
    Stateful Enterprise Agent Planner orchestrated by LangGraph with Self-RAG Reflection (Phase 7).
    """

    SYSTEM_INSTRUCTION = """You are an Enterprise Knowledge Agent.
You have access to specialized enterprise retrieval tools to find documentation, architecture guides, code repositories, setup procedures, issues, and communications across GitHub, Notion, Gmail, Dropbox, Jira and Confluence.

Guidelines for Tool Selection:
1. `catalog_discovery`: PRIMARY MAP-FIRST DISCOVERY TOOL. Call this tool FIRST to inspect the Global Master Index (global_index.md) and knowledge sync ledger (global_log.md) across all 6 enterprise connectors (GitHub, Jira, Notion, Dropbox, Gmail, Confluence). It returns a high-density cross-connector manifest of matching documents, PRs, runbooks, and tickets with confidence scores (0.0 to 1.0) and recommended retrieval tools to guide your execution plan.
2. `hybrid_search`: Preferred general search tool. Combines dense vector semantics, BM25+ keywords, and graph entities via Reciprocal Rank Fusion (RRF). Use for queries containing both high-level concepts and exact technical tokens.
3. `semantic_search`: Use for natural language questions, conceptual understanding, high-level architecture explanations, setup procedures, runbooks, and policy guidelines.
4. `keyword_search`: Use for exact technical identifiers, Jira issue keys (e.g. 'PAY-928'), GitHub PR numbers (e.g. '#1842'), HTTP/system error codes (e.g. 'HTTP 401', 'ECONNREFUSED'), code symbols/classes (e.g. 'AuthService.charge'), or exact filenames.
5. `resource_lookup`: Use to retrieve full documents, runbooks, SOPs, specifications, or policies by document title, topic name (e.g. 'Disaster Recovery Runbook', 'Payments API Specification'), canonical URI (e.g. 'github://repo/owner/name', 'notion://vault/master', 'jira://issue/PAY-928', 'https://github.com/...'), direct URL, or chunk ID. Reconstructs multi-chunk documents in sequential reading order.
6. `graph_traversal`: Use to explore structural document hierarchies:
   - 'get_children': Find all child documents, repository files, sub-issues, or sub-pages under a known parent container.
   - 'get_neighbors': Expand preceding and succeeding sibling chunks around a matched step or section.
   - 'get_full_sequence': Assemble an entire ordered multi-step sequence, runbook, or workflow by its sequence ID.
7. `github_entity_search`: Use for developer relationships, code intelligence, and GitHub entities:
   - 'get_pr_details': Find PR author, reviewers, assignees, modified files, and closed issues.
   - 'get_user_activity': Find PRs authored, commits, reviews, and assigned issues for a developer.
   - 'get_file_contributors': Find commit authors, history, and PRs touching a specific file.
   - 'get_commit_details': Find commit author, message, touched files, and parent PR.
   - 'get_issue_details': Find issue reporter, assignees, labels, and closing PRs.
   - 'get_labeled_items': Find PRs and issues tagged with a specific label.
   - 'get_team_overview': Find team members and accessible repositories.
   - 'get_repo_overview': Find repository maintainers, open issues, and file counts.
   - 'get_neighbors' / 'find_path': Generalized multi-hop entity traversal and relationship path finding.
8. Multi-Tool & Multi-Hop Planning:
   - Map-First Exploration: Call `catalog_discovery` first to gain a global map of relevant enterprise documents and confidence scores across connectors.
   - Single-Turn Parallel: If catalog results identify multiple related documents across Jira, GitHub, Notion, or Dropbox, invoke multiple specialized tools in the same turn.
   - Multi-Turn Multi-Hop: Follow up with `github_entity_search`, `resource_lookup`, or `graph_traversal` to drill down into specifics.
9. Reflection & Quality Control:
   - If the EvidenceEvaluator identifies missing information or suggests a specific tool, adapt your query and call the recommended tool to bridge the knowledge gap.
10. Once sufficient evidence is gathered, formulate a clear, professional, and well-structured answer.
11. Always cite specific evidence when stating facts or steps using bracketed references (e.g. [1], [2]).
"""

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        tool_registry: Optional[ToolRegistry] = None,
        answer_generator: Optional[AnswerGenerator] = None,
        evidence_evaluator: Optional[EvidenceEvaluator] = None,
        query_reformulator: Optional[QueryReformulator] = None,
        reranker: Optional[CrossEncoderReranker] = None,
        checkpointer: Optional[Any] = None,
        global_catalog_manager: Optional[Any] = None,
        graph_retriever: Optional[Any] = None,
        enable_reranking: bool = True,
        rerank_threshold: float = 0.0,
        max_turns: int = 10,
        max_retrieval_attempts: int = 3,
        max_history_messages: int = 50,
        context_format: str = "standard",
    ) -> None:
        self.context_format = context_format
        self.llm_provider = llm_provider or get_llm_provider()
        self.tool_registry = tool_registry or create_default_tool_registry()
        self.answer_generator = answer_generator or AnswerGenerator(llm_provider=self.llm_provider, context_format=context_format)
        self.evidence_evaluator = evidence_evaluator or EvidenceEvaluator(llm_provider=self.llm_provider, context_format=context_format)
        self.query_reformulator = query_reformulator or QueryReformulator(llm_provider=self.llm_provider)
        self.enable_reranking = enable_reranking
        self.rerank_threshold = rerank_threshold
        self.reranker = reranker or (CrossEncoderReranker() if enable_reranking else None)
        self.checkpointer = checkpointer
        self.global_catalog_manager = global_catalog_manager
        self.graph_retriever = graph_retriever
        self.max_turns = max_turns
        self.max_retrieval_attempts = max_retrieval_attempts
        self.max_history_messages = max_history_messages
        self.graph = self._build_graph()

    # ── Graph Node Implementations ───────────────────────────────────────────

    REASONER_SYSTEM_PROMPT = (
        "You are an Enterprise Knowledge Assistant capable of searching internal systems "
        "(GitHub, Jira, Confluence, Notion, Dropbox, Gmail) and answering general questions.\n\n"
        "Guidelines for Execution:\n"
        "1. Direct Answers (NO Tools Needed):\n"
        "   - Meta-Conversational Queries: If the user asks about the conversation itself (e.g. 'what was the last question I asked?', "
        "     'what did we discuss earlier?', 'summarize our chat', 'repeat your previous answer', 'who are you?'), answer DIRECTLY from the conversation "
        "     history without calling any tools.\n"
        "   - General Knowledge & Concept Explanations: If the user asks general conceptual questions, programming principles, definitions, or "
        "     math (e.g. 'what is the meaning of authentication?', 'explain how quicksort works', 'what is OAuth PKCE conceptually?', 'hello'), "
        "     answer DIRECTLY from your general technical knowledge without calling enterprise tools. Do NOT search internal documents unless the user explicitly asks for proprietary company implementations, internal runbooks, or tickets.\n"
        "2. Enterprise Retrieval (Use Tools):\n"
        "   - If the user asks about company systems, code repos, PRs, tickets, incidents, runbooks, credentials, policies, or architecture, "
        "     use enterprise retrieval tools.\n"
        "   - Tool Preferences: Prefer 'hybrid_search' for searching runbooks, procedures, SOPs, and topic queries because it fuses vector semantics, "
        "     BM25 keywords, and cross-encoder reranking to find the best chunks. Only use 'resource_lookup' when you specifically need the full sequential "
        "     text of an already identified document or ticket (e.g. 'PAY-928'). Use 'catalog_discovery' when broad cross-connector exploration is needed.\n"
        "   - Search Filters: When calling 'hybrid_search', 'semantic_search', or 'keyword_search', do NOT pass arbitrary 'source' or 'resource_type' "
        "     filters unless the user explicitly named a specific platform in their question. Omit them to search all sources.\n"
        "   - Follow-up Resolution: If the user asks follow-up questions with pronouns ('it', 'that PR', 'that ticket', 'who approved it?'), "
        "     inspect previous turns to resolve them into concrete entity names before issuing tool calls."
    )

    def _get_reasoner_system_prompt(self) -> str:
        """Constructs reasoner system prompt, dynamically appending adaptive global manifest if available."""
        if not self.global_catalog_manager:
            return self.REASONER_SYSTEM_PROMPT

        manifest = ""
        if hasattr(self.global_catalog_manager, "get_adaptive_global_manifest"):
            manifest = self.global_catalog_manager.get_adaptive_global_manifest(max_tokens=400)
        elif hasattr(self.global_catalog_manager, "generate_global_index_toon"):
            manifest = self.global_catalog_manager.generate_global_index_toon()

        if manifest:
            return f"{self.REASONER_SYSTEM_PROMPT}\n\n[TOPOLOGICAL OVERVIEW]\n{manifest}"
        return self.REASONER_SYSTEM_PROMPT

    def _reasoner_node(self, state: AgentState) -> Dict[str, Any]:
        """
        LLM Reasoner Step: Analyzes conversation state, reflection feedback, and available tools,
        deciding whether to issue ToolCalls or formulate a final direct answer.
        """
        turn_count = state.get("turn_count", 0) + 1
        tools = self.tool_registry.get_definitions()
        
        # Trim historical messages to maintain token budget across extended multi-turn chat
        raw_messages = state.get("messages", [])
        trimmed_messages = trim_conversation_history(raw_messages, max_messages=self.max_history_messages)
        internal_messages = _to_internal_messages(trimmed_messages)

        system_prompt = self._get_reasoner_system_prompt()
        if not internal_messages or internal_messages[0].role != MessageRole.SYSTEM:
            internal_messages.insert(0, Message(role=MessageRole.SYSTEM, content=system_prompt))
        else:
            internal_messages[0] = Message(role=MessageRole.SYSTEM, content=system_prompt)

        # Check if the query has an explicit scope modifier (@general, @enterprise, @jira, etc.)
        active_query = state.get("current_query") or state.get("query") or ""
        is_first_turn = (state.get("turn_count", 0) == 0)
        
        cleaned_query, scope_directive, target_connector = parse_query_scope(active_query)
        
        is_meta_query = False
        if is_first_turn and active_query:
            if scope_directive == "general":
                is_meta_query = True
            elif scope_directive == "enterprise":
                is_meta_query = False
            else:
                import re
                q_clean = active_query.strip().lower()
                meta_patterns = [
                    r"\b(last|previous|earlier)\s+(question|message|query|prompt|turn)\b",
                    r"\bwhat\s+(was|were)\s+(the|my)\s+(last|previous|earlier)\b",
                    r"\bwhat\s+did\s+(i|we)\s+(ask|say|discuss|talk\s+about)\b",
                    r"\b(repeat|summarize)\s+(our\s+)?(chat|conversation|last\s+answer|previous\s+answer)\b",
                    r"^\s*(hello|hi|hey|greetings|good\s+(morning|afternoon|evening))\b",
                    r"^\s*(who\s+are\s+you|what\s+can\s+you\s+do|help)\s*\??$",
                ]
                if any(re.search(p, q_clean) for p in meta_patterns):
                    is_meta_query = True

        if is_meta_query:
            raw_content = self.llm_provider.generate(internal_messages)
            content_str = raw_content if isinstance(raw_content, str) else getattr(raw_content, "content", str(raw_content))
            ai_msg = AIMessage(content=content_str)
            return {
                "messages": [ai_msg],
                "answer": content_str,
                "turn_count": turn_count,
            }

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

            # Special handling for catalog_discovery to unroll discovery manifest
            if tool_name == "catalog_discovery" and isinstance(tool_output, dict):
                manifest = tool_output.get("manifest", [])
                for idx, m in enumerate(manifest):
                    if isinstance(m, dict):
                        uri = m.get("uri") or f"catalog_entry_{idx}"
                        chunk_item = {
                            "chunk_id": f"catalog:{uri}",
                            "title": m.get("title", f"Catalog Entry {idx}"),
                            "source": m.get("source", "catalog"),
                            "domain": m.get("domain", "General"),
                            "is_catalog": True,
                            "retrieved_by_tool": "catalog_discovery",
                            "confidence": m.get("confidence", 0.8),
                            "recommended_tool": m.get("recommended_tool"),
                            "recommended_arguments": m.get("recommended_arguments", {}),
                            "url": m.get("uri", ""),
                            "text": (
                                f"[CATALOG DISCOVERY SUMMARY - {m.get('source', '').upper()}]\n"
                                f"Title: {m.get('title')}\n"
                                f"Domain: {m.get('domain')}\n"
                                f"Key Entities: {', '.join(m.get('key_entities', []))}\n"
                                f"Summary: {m.get('summary')}\n"
                                f"Recommended Tool: `{m.get('recommended_tool')}({json.dumps(m.get('recommended_arguments', {}))})` (Confidence: {m.get('confidence', 0.8):.2f})\n"
                                f"Reasoning: {m.get('reasoning', '')}"
                            ),
                        }
                        if not any(existing.get("chunk_id") == chunk_item.get("chunk_id") for existing in accumulated_chunks):
                            accumulated_chunks.append(chunk_item)
            # Accumulate retrieved chunks if output is a list of chunk dicts or single entity dict
            elif isinstance(tool_output, list):
                for idx, c in enumerate(tool_output):
                    if isinstance(c, dict):
                        chunk_item = dict(c)
                        if "chunk_id" not in chunk_item:
                            chunk_item["chunk_id"] = chunk_item.get("node_id") or f"{tool_name}:{call_id}:{idx}"
                        chunk_item.setdefault("title", chunk_item.get("name") or chunk_item.get("title") or f"Result from {tool_name}")
                        chunk_item.setdefault("source", "github" if "github" in tool_name else "graph")
                        chunk_item.setdefault("retrieved_by_tool", tool_name)
                        chunk_item.setdefault("text", json.dumps(chunk_item, ensure_ascii=False, indent=2))
                        if not any(existing.get("chunk_id") == chunk_item.get("chunk_id") for existing in accumulated_chunks):
                            accumulated_chunks.append(chunk_item)
            elif isinstance(tool_output, dict) and not tool_output.get("error"):
                chunk_item = dict(tool_output)
                if "chunk_id" not in chunk_item:
                    chunk_item["chunk_id"] = chunk_item.get("node_id") or f"{tool_name}:{call_id}"
                chunk_item.setdefault("title", chunk_item.get("title") or chunk_item.get("name") or chunk_item.get("repository") or f"Result from {tool_name}")
                chunk_item.setdefault("source", "github" if "github" in tool_name else "graph")
                chunk_item.setdefault("retrieved_by_tool", tool_name)
                chunk_item.setdefault("text", json.dumps(chunk_item, ensure_ascii=False, indent=2))
                if not any(existing.get("chunk_id") == chunk_item.get("chunk_id") for existing in accumulated_chunks):
                    accumulated_chunks.append(chunk_item)

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

    def _reranker_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Local Cross-Encoder Reranker Node (Phase 9):
        Re-scores and re-orders candidate evidence chunks by computing cross-attention
        between the active query and candidate texts, rejecting irrelevant noise.
        """
        if not self.enable_reranking or not self.reranker:
            return {"rerank_applied": False, "rerank_scores": {}}

        query = state.get("current_query") or state.get("query")
        chunks = state.get("retrieved_chunks", [])
        if not chunks or not query:
            return {"rerank_applied": True, "rerank_scores": {}}

        # Scaled retrieval depth based on context format
        target_top_k = 15 if self.context_format == "toon" else 5

        reranked_results = self.reranker.rerank(
            query=query,
            chunks=chunks,
            top_k=target_top_k,
            score_threshold=self.rerank_threshold,
        )

        reranked_chunks = [r.to_dict() for r in reranked_results]
        scores = {r.chunk_id: round(r.score, 4) for r in reranked_results}

        # If threshold filtered everything out, preserve original chunks to prevent empty context
        if not reranked_chunks and chunks:
            reranked_chunks = chunks[:target_top_k]

        # Automatic Sequential Step & Procedure Expansion (Phase 2)
        if self.graph_retriever and hasattr(self.graph_retriever, "expand_sequential_windows"):
            user_context = state.get("user_context")
            max_total = 25 if self.context_format == "toon" else 10
            reranked_chunks = self.graph_retriever.expand_sequential_windows(
                chunks=reranked_chunks,
                window_size=1,
                user_context=user_context,
                max_total_chunks=max_total,
            )

        return {
            "retrieved_chunks": reranked_chunks,
            "rerank_scores": scores,
            "rerank_applied": True,
        }

    def _evaluator_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Evidence Evaluator Node (Phase 7):
        Inspects all accumulated evidence for relevance, completeness, and knowledge gaps.
        """
        attempts = state.get("retrieval_attempts", 0) + 1
        query = state.get("current_query") or state.get("query")
        chunks = state.get("retrieved_chunks", [])
        history = _to_internal_messages(state.get("messages", []))

        eval_result = self.evidence_evaluator.evaluate_evidence(
            query=query,
            chunks=chunks,
            conversation_history=history,
        )

        return {
            "evaluation": eval_result.to_dict(),
            "missing_information": eval_result.missing_information,
            "retrieval_attempts": attempts,
        }

    def _reformulator_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Query Reformulator Node (Phase 7):
        Transforms the active query using evaluator gap feedback and injects
        reflection guidance into the conversation history for the next reasoner turn.
        """
        eval_dict = state.get("evaluation") or {}
        eval_result = EvaluationResult.from_dict(eval_dict)
        original_query = state.get("query")
        chunks = state.get("retrieved_chunks", [])
        history = _to_internal_messages(state.get("messages", []))

        reformulation = self.query_reformulator.reformulate(
            original_query=original_query,
            current_evidence=chunks,
            evaluation=eval_result,
            conversation_history=history,
        )

        new_query = reformulation.get("reformulated_query", original_query)
        suggested_tool = reformulation.get("suggested_tool") or eval_result.recommended_tool

        # Construct reflection guidance message for Reasoner
        guidance_text = f"[Self-RAG Reflection]: The previous retrieval had knowledge gaps: {', '.join(eval_result.missing_information)}."
        if suggested_tool:
            guidance_text += f" Recommended tool: `{suggested_tool}`."
        guidance_text += f" Next targeted query: '{new_query}'."

        ref_queries = list(state.get("reformulated_queries", []))
        ref_queries.append(new_query)

        return {
            "current_query": new_query,
            "reformulated_queries": ref_queries,
            "messages": [HumanMessage(content=guidance_text)],
        }

    def _generator_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Grounded Answer Generator Node: Synthesizes final response using
        evidence chunks and structured citations if not already formulated.
        """
        chunks = state.get("retrieved_chunks", [])
        answer = state.get("answer")
        citations = []

        executed_any_tools = any(isinstance(m, AIMessage) and getattr(m, "tool_calls", None) for m in state.get("messages", []))

        if not answer:
            # Generate grounded answer from retrieved chunks
            if chunks:
                _, citations = ContextBuilder.build_context(chunks)
            gen_res = self.answer_generator.generate_answer(
                query=state["query"],
                chunks=chunks,
                conversation_history=_to_internal_messages(state.get("messages", [])),
            )
            answer = gen_res["answer"]
        elif chunks and executed_any_tools:
            # If answer was formulated directly and tools were executed in this turn
            _, citations = ContextBuilder.build_context(chunks)
        else:
            citations = []

        # Append final AIMessage to messages for state checkpointing across turns
        last_msg = state.get("messages", [])[-1] if state.get("messages") else None
        new_messages = []
        if not (isinstance(last_msg, AIMessage) and not getattr(last_msg, "tool_calls", None) and last_msg.content == answer):
            new_messages.append(AIMessage(content=answer))

        res: Dict[str, Any] = {
            "answer": answer,
            "citations": citations,
        }
        if new_messages:
            res["messages"] = new_messages
        return res

    # ── Conditional Routing ──────────────────────────────────────────────────

    def _reasoner_routing(self, state: AgentState) -> Literal["tool_node", "generator"]:
        """
        Determines routing from reasoner:
          - If reasoner returned tool_calls and max_turns not reached -> 'tool_node'
          - Otherwise -> 'generator'
        """
        last_message = state["messages"][-1]
        has_tools = bool(getattr(last_message, "tool_calls", None))
        turn_count = state.get("turn_count", 0)

        if has_tools and turn_count < self.max_turns:
            return "tool_node"
        return "generator"

    def _evaluator_routing(self, state: AgentState) -> Literal["generator", "reformulator"]:
        """
        Determines routing from evaluator (Self-RAG Reflection Loop):
          - If evidence is SUFFICIENT or max retrieval attempts / turns reached -> 'generator'
          - If evidence is INSUFFICIENT or REFORMULATE -> 'reformulator'
        """
        eval_dict = state.get("evaluation") or {}
        action = eval_dict.get("recommended_action", "GENERATE")
        is_sufficient = bool(eval_dict.get("evidence_sufficient", True))
        attempts = state.get("retrieval_attempts", 0)
        turns = state.get("turn_count", 0)

        # Stop reflection loop if max attempts or turns reached
        if attempts >= self.max_retrieval_attempts or turns >= self.max_turns:
            return "generator"

        if is_sufficient or action == RecommendedAction.GENERATE.value:
            return "generator"

        return "reformulator"

    # ── Graph Assembly ───────────────────────────────────────────────────────

    def _build_graph(self) -> Any:
        workflow = StateGraph(AgentState)

        # Add 6 specialized nodes
        workflow.add_node("reasoner", self._reasoner_node)
        workflow.add_node("tool_node", self._tool_node)
        workflow.add_node("reranker", self._reranker_node)
        workflow.add_node("evaluator", self._evaluator_node)
        workflow.add_node("reformulator", self._reformulator_node)
        workflow.add_node("generator", self._generator_node)

        # Connect edges
        workflow.add_edge(START, "reasoner")
        workflow.add_conditional_edges(
            "reasoner",
            self._reasoner_routing,
            {
                "tool_node": "tool_node",
                "generator": "generator",
            },
        )
        workflow.add_edge("tool_node", "reranker")
        workflow.add_edge("reranker", "evaluator")
        workflow.add_conditional_edges(
            "evaluator",
            self._evaluator_routing,
            {
                "generator": "generator",
                "reformulator": "reformulator",
            },
        )
        workflow.add_edge("reformulator", "reasoner")
        workflow.add_edge("generator", END)

        if self.checkpointer is not None:
            return workflow.compile(checkpointer=self.checkpointer)
        return workflow.compile()

    # ── Execution Entrypoint ─────────────────────────────────────────────────

    def run(
        self,
        query: str,
        user_context: Optional[Dict[str, Any]] = None,
        thread_id: Optional[str] = None,
        conversation_history: Optional[List[BaseMessage]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes the compiled LangGraph workflow with Self-RAG reflection and multi-turn checkpointing.

        Args:
            query: User query string.
            user_context: Security context dictionary (roles, user_id, groups).
            thread_id: Optional conversation session/thread identifier for multi-turn persistence.
            conversation_history: Optional explicit message list prepended to the turn.
            config: Optional LangGraph execution config dictionary.

        Returns:
            Result dictionary containing answer, citations, executed tool calls, and session metadata.
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
            "current_query": query,
            "user_context": user_context,
            "messages": initial_messages,
            "retrieved_chunks": [],
            "citations": [],
            "answer": "",
            "evaluation": None,
            "missing_information": [],
            "retrieval_attempts": 0,
            "reformulated_queries": [],
            "rerank_scores": {},
            "rerank_applied": False,
            "turn_count": 0,
            "thread_id": thread_id,
            "conversation_summary": None,
            "error": None,
        }

        exec_config = dict(config or {})
        if thread_id or self.checkpointer is not None:
            t_id = thread_id or "default_session"
            if "configurable" not in exec_config:
                exec_config["configurable"] = {}
            exec_config["configurable"].setdefault("thread_id", t_id)

        final_state = self.graph.invoke(initial_state, config=exec_config if exec_config else None)

        # Extract tool execution logs for the active query turn
        executed_tool_calls: List[Dict[str, Any]] = []
        messages = final_state.get("messages", [])
        last_user_idx = 0
        for i, m in enumerate(messages):
            if isinstance(m, HumanMessage) and getattr(m, "content", "") == query:
                last_user_idx = i

        for msg in messages[last_user_idx:]:
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
            "evaluation": final_state.get("evaluation"),
            "missing_information": final_state.get("missing_information", []),
            "retrieval_attempts": final_state.get("retrieval_attempts", 0),
            "reformulated_queries": final_state.get("reformulated_queries", []),
            "rerank_applied": final_state.get("rerank_applied", False),
            "rerank_scores": final_state.get("rerank_scores", {}),
            "turns": final_state.get("turn_count", 1),
            "thread_id": final_state.get("thread_id", thread_id),
            "messages": final_state.get("messages", []),
            "llm_provider": getattr(self.llm_provider, "__class__", type(self.llm_provider)).__name__,
        }
