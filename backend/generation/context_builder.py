"""
Evidence Context Builder for Enterprise Knowledge Generation.

Formats retrieved chunks into clean, structured, citation-ready context blocks
for the LLM reasoning and answer generation prompts, supporting both standard Markdown
and high-density TOON (Token-Oriented Object Notation) formats.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Tuple

from backend.serialization.toon import serialize_toon_context


class ContextBuilder:
    """
    Constructs citation-referenced evidence context from retrieved chunks.
    """

    @staticmethod
    def build_context(
        chunks: List[Dict[str, Any]],
        format: Literal["standard", "toon"] = "standard",
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Builds numbered evidence context string and a list of citation source references.

        Args:
            chunks: List of candidate chunk dictionaries.
            format: "standard" (Markdown) or "toon" (Token-Oriented Object Notation).

        Returns:
            (context_string, citations_list)
        """
        if format == "toon":
            return ContextBuilder.build_toon_context(chunks)
        return ContextBuilder.build_standard_context(chunks)

    @staticmethod
    def build_standard_context(
        chunks: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Builds human-readable standard Markdown evidence context.
        """
        if not chunks:
            return "No relevant enterprise documents found.", []

        context_lines: List[str] = []
        citations: List[Dict[str, Any]] = []

        for idx, chunk in enumerate(chunks, 1):
            source = str(chunk.get("source", "doc")).upper()
            title = str(chunk.get("title", "Untitled"))
            url = str(chunk.get("url", ""))
            path_list = chunk.get("section_path", [])
            path_str = " > ".join(str(p) for p in path_list) if path_list else str(chunk.get("section_heading", ""))
            text = str(chunk.get("text", "")).strip()

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

            tool = chunk.get("retrieved_by_tool") or chunk.get("tool") or ""
            citations.append({
                "id": str(idx),
                "index": str(idx),
                "citation_index": idx,
                "source": source,
                "title": title,
                "url": url,
                "path": path_str,
                "chunk_id": chunk.get("chunk_id", ""),
                "tool": tool,
            })

        return "\n".join(context_lines).strip(), citations

    @staticmethod
    def build_toon_context(
        chunks: List[Dict[str, Any]],
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Builds high-density TOON (Token-Oriented Object Notation) evidence context,
        reducing prompt overhead by 60-75% for LLM inference.
        """
        return serialize_toon_context(chunks, numbered=True)
