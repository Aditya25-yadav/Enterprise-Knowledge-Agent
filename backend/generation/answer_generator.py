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
        if not chunks:
            return {
                "answer": "The provided enterprise documentation does not contain information regarding this request.",
                "citations": [],
                "chunks_used": 0,
            }

        context_str, citations = ContextBuilder.build_context(chunks)

        user_content = f"""USER QUESTION:
{query}

ENTERPRISE EVIDENCE CONTEXT:
{context_str}

Instructions:
- Provide a clear, factual answer using ONLY the enterprise evidence context above.
- Cite your sources with bracketed numbers [1], [2], etc. matching the evidence chunks above.
- Do NOT include external citations, third-party references, or ungrounded claims."""

        prompt_messages: List[Message] = [
            Message(role=MessageRole.SYSTEM, content=self.SYSTEM_PROMPT)
        ]
        if conversation_history:
            # Only include prior human/user or system messages if needed
            for m in conversation_history:
                if m.role in (MessageRole.USER, MessageRole.SYSTEM):
                    prompt_messages.append(m)
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

