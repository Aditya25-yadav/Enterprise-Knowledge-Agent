"""
Generates a cited answer from retrieved chunks using Groq's free-tier API
(fast inference, generous rate limits, OpenAI-compatible client).

Swap providers by writing a new call_llm()-shaped function — everything
else in this file is provider-agnostic.
"""

from __future__ import annotations
import os
from groq import Groq

DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are an enterprise knowledge assistant. Answer the user's question using ONLY the numbered sources provided below. \
Follow these rules strictly:
- Every factual claim must be followed by a citation marker like [1] or [2], referencing the source number it came from.
- If the sources don't contain enough information to answer, say so plainly. Do not use outside knowledge.
- Be concise and direct.
"""


def _get_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Get a free key at console.groq.com and put it in your .env file."
        )
    return Groq(api_key=api_key)


def _format_sources(hits: list[dict]) -> str:
    lines = []
    for i, hit in enumerate(hits, start=1):
        meta = hit["metadata"]
        lines.append(
            f"[{i}] Title: {meta['title']}\n"
            f"Source: {meta['source_system']}\n"
            f"Content: {hit['text']}\n"
        )
    return "\n---\n".join(lines)


def generate_answer(question: str, hits: list[dict], model: str = DEFAULT_MODEL) -> dict:
    """
    Takes retrieved chunks and produces a cited natural-language answer.
    Returns the answer text plus the source list, so the caller can render
    citation markers as real links.
    """
    if not hits:
        return {
            "answer": "I couldn't find anything relevant in the indexed documents to answer that.",
            "sources": [],
        }

    client = _get_client()
    sources_block = _format_sources(hits)

    user_prompt = f"Sources:\n\n{sources_block}\n\nQuestion: {question}"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    answer = response.choices[0].message.content

    sources = [
        {
            "index": i,
            "title": hit["metadata"]["title"],
            "url": hit["metadata"]["url"],
            "source_system": hit["metadata"]["source_system"],
            "score": round(hit["score"], 3),
        }
        for i, hit in enumerate(hits, start=1)
    ]

    return {"answer": answer, "sources": sources}
