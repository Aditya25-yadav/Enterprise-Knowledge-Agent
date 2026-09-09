"""
Connector-Aware and Structure-Preserving Chunker for OKF Concepts and Documents.

Splits OKFConcept or Document markdown into coherent, semantically bounded
chunks while preserving markdown structure (headers, code blocks, tables)
and propagating enterprise metadata and RBAC permission boundaries.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from backend.ingestion.chunk import Chunk
from backend.models.document import Document
from backend.models.okf import OKFConcept, OKFPermissions


class OKFChunker:
    """
    Chunker for turning OKFConcept or Document into searchable Chunk objects.

    Strategies:
      1. Header-based semantic splitting (splits at #, ##, ### headers).
      2. Code fence and markdown table preservation.
      3. Size-bounded windowing with configurable overlap for long sections.
      4. Complete inheritance of permissions, URLs, timestamps, and resource IDs.
    """

    def __init__(
        self,
        max_chunk_chars: int = 1500,
        min_chunk_chars: int = 100,
        overlap_chars: int = 200,
    ) -> None:
        """
        Args:
            max_chunk_chars: Target maximum character length per chunk (~300-400 words).
            min_chunk_chars: Minimum character length to avoid creating useless micro-chunks.
            overlap_chars: Number of characters to overlap between sequential sub-chunks.
        """
        self.max_chunk_chars = max_chunk_chars
        self.min_chunk_chars = min_chunk_chars
        self.overlap_chars = overlap_chars

    # ── High-Level Entrypoints ───────────────────────────────────────────────

    def chunk_okf_concept(self, concept: OKFConcept) -> List[Chunk]:
        """
        Split an OKFConcept into a list of Chunk objects.
        """
        body = concept.body or ""
        resource_id = concept.resource or f"concept:{concept.type.lower()}:{concept.title or 'untitled'}"
        
        # Extract source platform from tags or resource string
        source = "unknown"
        if concept.tags:
            source = concept.tags[0].lower()
        elif concept.resource and "://" in concept.resource:
            source = concept.resource.split("://")[0].lower()
        elif concept.sources and concept.sources[0].resource:
            res = concept.sources[0].resource
            if "github.com" in res:
                source = "github"
            elif "notion.so" in res or "notion.com" in res:
                source = "notion"
            elif "dropbox.com" in res:
                source = "dropbox"
            elif "gmail" in res:
                source = "gmail"

        # Resolve URL
        url = concept.resource if (concept.resource and concept.resource.startswith("http")) else None
        if not url and concept.sources and concept.sources[0].resource.startswith("http"):
            url = concept.sources[0].resource

        permissions_dict = (
            concept.permissions.to_dict()
            if isinstance(concept.permissions, OKFPermissions)
            else (concept.permissions or {})
        )

        return self.chunk_markdown(
            text=body,
            resource_id=resource_id,
            source=source,
            resource_type=concept.type.lower(),
            title=concept.title or "Untitled",
            url=url,
            permissions=permissions_dict,
            created_at=concept.created_at,
            updated_at=concept.updated_at,
            extra_metadata={
                "tags": concept.tags,
                "status": concept.status,
                "trust_tier": concept.trust_tier,
                "has_structured_data": len(concept.structured_data) > 0,
                **(concept.extra_metadata or {}),
            },
        )

    def chunk_document(self, doc: Document) -> List[Chunk]:
        """
        Split an intermediate Document into a list of Chunk objects
        by converting it to an OKFConcept first or chunking its markdown directly.
        """
        concept = OKFConcept.from_intermediate_document(doc)
        return self.chunk_okf_concept(concept)

    # ── Core Markdown Chunking Logic ─────────────────────────────────────────

    def chunk_markdown(
        self,
        text: str,
        resource_id: str,
        source: str,
        resource_type: str,
        title: str,
        url: Optional[str] = None,
        permissions: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Chunk]:
        """
        Splits markdown text into structured, header-aware chunks.
        """
        if not text or not text.strip():
            return []

        permissions = permissions or {
            "is_public": True,
            "allowed_roles": ["employee"],
            "allowed_users": [],
            "allowed_groups": [],
        }
        extra_metadata = extra_metadata or {}

        # 1. Break down into semantic sections based on Markdown headers
        raw_sections = self._split_by_headers(text)

        # 2. Refine sections (split oversized sections, merge undersized sections)
        refined_sections = self._refine_sections(raw_sections)

        # 3. Create Chunk objects
        chunks: List[Chunk] = []
        total_chunks = len(refined_sections)

        for idx, (heading, content) in enumerate(refined_sections):
            chunk_content = content.strip()
            # Deterministic chunk ID based on resource_id and index
            safe_res_id = re.sub(r"[^a-zA-Z0-9_:-]", "_", resource_id)
            chunk_id = f"{safe_res_id}#c{idx}"

            chunk = Chunk(
                chunk_id=chunk_id,
                resource_id=resource_id,
                source=source,
                resource_type=resource_type,
                text=chunk_content,
                title=title,
                url=url,
                section_heading=heading if heading else None,
                chunk_index=idx,
                total_chunks=total_chunks,
                permissions=permissions,
                created_at=created_at,
                updated_at=updated_at,
                extra_metadata=extra_metadata,
            )
            chunks.append(chunk)

        return chunks

    # ── Internal Splitting Helpers ───────────────────────────────────────────

    def _split_by_headers(self, markdown: str) -> List[tuple[Optional[str], str]]:
        """
        Splits markdown by `#`, `##`, `###`, etc. headers.
        Returns a list of (heading, section_text) tuples.
        """
        lines = markdown.splitlines()
        sections: List[tuple[Optional[str], str]] = []
        current_heading: Optional[str] = None
        current_lines: List[str] = []
        in_code_block = False

        header_regex = re.compile(r"^(#{1,4})\s+(.+)$")

        for line in lines:
            if line.strip().startswith("```"):
                in_code_block = not in_code_block

            # Check if this line is a markdown header outside a code block
            match = header_regex.match(line) if not in_code_block else None

            if match:
                # Flush previous section
                if current_lines:
                    sec_text = "\n".join(current_lines).strip()
                    if sec_text:
                        sections.append((current_heading, sec_text))
                    current_lines = []

                current_heading = line.strip()
                current_lines.append(line)
            else:
                current_lines.append(line)

        # Flush final section
        if current_lines:
            sec_text = "\n".join(current_lines).strip()
            if sec_text:
                sections.append((current_heading, sec_text))

        return sections

    def _refine_sections(
        self,
        sections: List[tuple[Optional[str], str]],
    ) -> List[tuple[Optional[str], str]]:
        """
        Processes sections to ensure each chunk is within [min_chunk_chars, max_chunk_chars].
        """
        result: List[tuple[Optional[str], str]] = []

        for heading, content in sections:
            if len(content) <= self.max_chunk_chars:
                # Fits within target size
                result.append((heading, content))
            else:
                # Oversized section: split further by paragraphs / sliding window
                sub_chunks = self._split_oversized_text(content, heading)
                for sub_c in sub_chunks:
                    result.append((heading, sub_c))

        # Merge adjacent undersized chunks if beneficial
        merged_result: List[tuple[Optional[str], str]] = []
        for heading, content in result:
            if not merged_result:
                merged_result.append((heading, content))
                continue

            last_heading, last_content = merged_result[-1]
            if (
                len(last_content) < self.min_chunk_chars
                and (len(last_content) + len(content) + 2) <= self.max_chunk_chars
            ):
                # Merge with previous
                merged_text = f"{last_content}\n\n{content}".strip()
                merged_result[-1] = (last_heading or heading, merged_text)
            else:
                merged_result.append((heading, content))

        return merged_result

    def _split_oversized_text(self, text: str, heading: Optional[str]) -> List[str]:
        """
        Splits text that exceeds max_chunk_chars using paragraph boundaries,
        falling back to sentence or character windows with overlap.
        """
        paragraphs = text.split("\n\n")
        chunks: List[str] = []
        current_chunk_paras: List[str] = []
        current_length = 0

        for para in paragraphs:
            para_len = len(para)
            if current_length + para_len + 2 <= self.max_chunk_chars:
                current_chunk_paras.append(para)
                current_length += para_len + 2
            else:
                if current_chunk_paras:
                    chunks.append("\n\n".join(current_chunk_paras).strip())
                    current_chunk_paras = []
                    current_length = 0

                if para_len > self.max_chunk_chars:
                    # Single paragraph is huge (e.g. long code or block) -> split with sliding window
                    step = self.max_chunk_chars - self.overlap_chars
                    for i in range(0, para_len, max(1, step)):
                        sub_slice = para[i : i + self.max_chunk_chars]
                        if sub_slice.strip():
                            chunks.append(sub_slice.strip())
                else:
                    current_chunk_paras.append(para)
                    current_length = para_len

        if current_chunk_paras:
            chunks.append("\n\n".join(current_chunk_paras).strip())

        return chunks
