"""
Simple fixed-size chunker with overlap. Good enough for v1 — swap in a
sentence/paragraph-aware splitter later if retrieval quality needs it.
"""

from __future__ import annotations
import re


def clean_text(text: str) -> str:
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[str]:
    """
    Splits text into overlapping chunks, preferring to break on paragraph
    or sentence boundaries near the target size rather than mid-word.
    """
    text = clean_text(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)

        if end < text_len:
            # try to break on a paragraph, then sentence, then space
            window = text[start:end]
            break_point = max(
                window.rfind("\n\n"),
                window.rfind(". "),
                window.rfind(" "),
            )
            if break_point > chunk_size * 0.5:
                end = start + break_point + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break
        start = max(end - overlap, start + 1)

    return chunks
