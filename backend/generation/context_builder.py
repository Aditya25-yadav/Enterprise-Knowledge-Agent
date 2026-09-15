"""
Evidence Context Builder for Enterprise Knowledge Generation.

Formats retrieved chunks into clean, structured, citation-ready context blocks
for the LLM reasoning and answer generation prompts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


class ContextBuilder:
    """
    Constructs citation-referenced evidence context from retrieved chunks.
    """

    @staticmethod
    def build_context(chunks: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, str]]]:
        """
        Builds numbered evidence context string and a list of citation source references.
        Returns:
          (context_string, citations_list)
        """
        if not chunks:
            return "No relevant enterprise documents found.", []

        context_lines: List[str] = []
        citations: List[Dict[str, str]] = []

        for idx, chunk in enumerate(chunks, 1):
            source = chunk.get("source", "doc").upper()
            title = chunk.get("title", "Untitled")
            url = chunk.get("url", "")
            path_list = chunk.get("section_path", [])
            path_str = " > ".join(path_list) if path_list else chunk.get("section_heading", "")
            text = chunk.get("text", "").strip()

            header = f"[{idx}] [{source}] {title}"
            if path_str:
                header += f" > {path_str}"
            if url:
                header += f" ({url})"

            # If sequential procedure step
            seq = chunk.get("sequence")
            if seq and isinstance(seq, dict):
                header += f" [Step {seq.get('step')}/{seq.get('total_steps')}]"

            context_lines.append(f"{header}\n{text}\n")

            citations.append({
                "index": str(idx),
                "source": source,
                "title": title,
                "url": url,
                "path": path_str,
                "chunk_id": chunk.get("chunk_id", ""),
            })

        return "\n".join(context_lines).strip(), citations
