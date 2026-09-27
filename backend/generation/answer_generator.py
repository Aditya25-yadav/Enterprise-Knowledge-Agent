"""
Grounded Answer Generator for Enterprise Knowledge Agent.

Synthesizes final user-facing answers with strict citation grounding
and source attribution.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.generation.context_builder import ContextBuilder
from backend.llm.base import LLMProvider, Message, MessageRole
from backend.llm.factory import get_llm_provider


class AnswerGenerator:
    """
    Generates grounded answers from retrieved enterprise context.
    """

    SYSTEM_PROMPT = """You are an Enterprise Knowledge Assistant.
Your task is to answer the user's inquiry accurately, professionally, and strictly grounded in the provided enterprise knowledge context.

Strict Grounding & Citation Rules:
1. Grounding: Answer ONLY based on the facts present in the provided ENTERPRISE EVIDENCE CONTEXT. Do NOT assume, extrapolate, or hallucinate policies, keys, steps, procedures, or external knowledge.
2. Citations: Use bracketed citation numbers (e.g. [1], [2]) corresponding ONLY to the numbered evidence chunks provided above.
3. No External Citations: NEVER invent external citations, standards, textbooks, government agencies (e.g. NIST, FEMA, ISO, AWS external links), or unindexed URLs.
4. Procedures: When explaining runbooks or workflows, present the exact steps from the evidence in their correct sequential order.
5. Missing Information: If the provided evidence does not contain sufficient information to answer the question, state: "The provided enterprise documentation does not contain information regarding this request." Do NOT make up steps or policies.
6. Conflict & Supersession Awareness: If the evidence contains conflicting facts or indicates that an earlier document/RFC has been superseded by a newer PR, post-mortem, or commit (e.g. timeout changed from 10s to 60s), explicitly note the supersession and identify the authoritative active version.
"""

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        context_format: str = "standard",
    ) -> None:
        self.llm_provider = llm_provider or get_llm_provider()
        self.context_format = context_format

    def generate_answer(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Message]] = None,
        context_format: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generates a grounded final answer for the user query using retrieved chunks.
        """
        if not chunks:
            return {
                "answer": "The provided enterprise documentation does not contain information regarding this request.",
                "citations": [],
                "chunks_used": 0,
            }

        fmt = context_format or self.context_format
        context_str, citations = ContextBuilder.build_context(chunks, format=fmt)

        user_content = f"""USER QUESTION:
{query}

ENTERPRISE EVIDENCE CONTEXT:
{context_str}

Instructions:
- Provide a clear, factual answer answering ONLY the current USER QUESTION above using the provided ENTERPRISE EVIDENCE CONTEXT.
- Do NOT re-answer, summarize, or address questions from earlier conversation turns. Focus strictly on the current inquiry.
- Cite your sources with bracketed numbers [1], [2], etc. matching the evidence chunks above.
- Do NOT include external citations, third-party references, or ungrounded claims."""

        prompt_messages: List[Message] = [
            Message(role=MessageRole.SYSTEM, content=self.SYSTEM_PROMPT)
        ]
        if conversation_history:
            # Include clean prior dialog turns (USER and ASSISTANT pairs only) for conversational continuity
            clean_history = []
            for m in conversation_history:
                if m.role in (MessageRole.USER, MessageRole.ASSISTANT) and m.content:
                    clean_history.append(m)
            # Exclude the very last user message if it duplicates user_content
            if clean_history and clean_history[-1].role == MessageRole.USER and clean_history[-1].content == query:
                clean_history.pop()
            prompt_messages.extend(clean_history)

        prompt_messages.append(Message(role=MessageRole.USER, content=user_content))

        response = self.llm_provider.generate(
            messages=prompt_messages,
        )

        answer_text = response if isinstance(response, str) else getattr(response, "content", str(response))

        return {
            "answer": answer_text or "No response generated.",
            "citations": citations,
            "chunks_used": len(chunks),
        }

