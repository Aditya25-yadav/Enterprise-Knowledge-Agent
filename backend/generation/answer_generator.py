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

Rules:
1. Grounding: Answer ONLY based on the facts present in the provided evidence. Do NOT hallucinate policies, keys, steps, or procedures.
2. Citations: When stating facts, cite the source using bracketed numbers corresponding to the evidence (e.g. [1], [2]).
3. Procedures: When explaining workflows or runbooks, present the steps in their correct sequential order.
4. Completeness: If the provided evidence is insufficient to answer the question, clearly state what information is available and what is missing.
"""

    def __init__(self, llm_provider: Optional[LLMProvider] = None) -> None:
        self.llm_provider = llm_provider or get_llm_provider()

    def generate_answer(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        conversation_history: Optional[List[Message]] = None,
    ) -> Dict[str, Any]:
        """
        Generates a grounded final answer for the user query using retrieved chunks.
        """
        context_str, citations = ContextBuilder.build_context(chunks)

        user_content = f"""USER QUESTION:
{query}

ENTERPRISE EVIDENCE CONTEXT:
{context_str}

Please provide a clear, well-structured answer with source citations [1], [2], etc. where applicable."""

        # Add system prompt if needed as part of message context
        prompt_messages: List[Message] = []
        if conversation_history:
            prompt_messages.extend(conversation_history)
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
