"""
Structure-Aware, Semantic & Hierarchical Chunker for Enterprise Knowledge Ingestion.

Implements dedicated chunking strategies for:
  1. Hierarchical Documents (Notion, Drive, Confluence, Markdown with heading breadcrumbs)
  2. Sequential Procedures (Runbooks, Setup Steps, Numbered Workflows)
  3. Conversation Threads (Gmail, Slack messages and replies)
  4. Code Symbols (GitHub code files, functions, classes)
  5. Tabular Data (Databases, CSVs, Markdown tables)

Ensures all generated chunks carry bidirectional sibling links (prev_chunk_id, next_chunk_id),
parent-child relationships, breadcrumb paths (section_path), and enterprise RBAC metadata.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.ingestion.chunk import ContentType, SequenceInfo, SmartChunk
from backend.models.document import BlockType, ContentBlock, Document
from backend.models.okf import OKFConcept, OKFPermissions


class SmartOKFChunker:
    """
    Structure-first chunker that dispatches content to specialized chunking
    strategies based on document type, structural syntax, and semantic content.
    """

    def __init__(
        self,
        max_chunk_chars: int = 1500,
        min_chunk_chars: int = 80,
        overlap_chars: int = 150,
    ) -> None:
        self.max_chunk_chars = max_chunk_chars
        self.min_chunk_chars = min_chunk_chars
        self.overlap_chars = overlap_chars

    # ── High-Level Entrypoints ───────────────────────────────────────────────

    def chunk_okf_concept(self, concept: OKFConcept) -> List[SmartChunk]:
        """
        Main entrypoint: parses an OKFConcept into a cohesive list of linked SmartChunks.
        """
        body = concept.body or ""
        resource_id = concept.resource or f"concept:{concept.type.lower()}:{concept.title or 'untitled'}"

        # 1. Infer platform source
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
            elif "gmail" in res or "mail.google.com" in res:
                source = "gmail"

        # 2. Resolve URL
        url = concept.resource if (concept.resource and concept.resource.startswith("http")) else None
        if not url and concept.sources and concept.sources[0].resource.startswith("http"):
            url = concept.sources[0].resource

        # 3. Resolve permissions & extra metadata
        permissions_dict = (
            concept.permissions.to_dict()
            if isinstance(concept.permissions, OKFPermissions)
            else (concept.permissions or {})
        )
        extra_meta = {
            "tags": concept.tags,
            "status": concept.status,
            "trust_tier": concept.trust_tier,
            "has_structured_data": len(concept.structured_data) > 0,
            **(concept.extra_metadata or {}),
        }

        # 4. Route to appropriate specialized chunking strategy
        chunks = self._route_and_chunk(
            text=body,
            resource_id=resource_id,
            source=source,
            resource_type=concept.type.lower(),
            title=concept.title or "Untitled",
            url=url,
            permissions=permissions_dict,
            created_at=concept.created_at,
            updated_at=concept.updated_at,
            extra_metadata=extra_meta,
            structured_data=concept.structured_data,
        )

        # 5. Link sibling chunks (prev_chunk_id, next_chunk_id) and indices
        return self._link_sibling_chunks(chunks)

    def chunk_document(self, doc: Document) -> List[SmartChunk]:
        """
        Converts an intermediate Document into an OKFConcept, then chunks it.
        """
        concept = OKFConcept.from_intermediate_document(doc)
        return self.chunk_okf_concept(concept)

    # ── Strategy Router ──────────────────────────────────────────────────────

    def _route_and_chunk(
        self,
        text: str,
        resource_id: str,
        source: str,
        resource_type: str,
        title: str,
        url: Optional[str],
        permissions: Dict[str, Any],
        created_at: Optional[str],
        updated_at: Optional[str],
        extra_metadata: Dict[str, Any],
        structured_data: List[Dict[str, Any]],
    ) -> List[SmartChunk]:
        """
        Inspects content and metadata to route to one or more strategy handlers.
        """
        if not text or not text.strip():
            return []

        # Check for Email / Conversation thread
        if source in ("gmail", "slack") or "email" in resource_type or "thread" in resource_type:
            return self._chunk_conversation_thread(
                text=text,
                resource_id=resource_id,
                source=source,
                resource_type=resource_type,
                title=title,
                url=url,
                permissions=permissions,
                created_at=created_at,
                updated_at=updated_at,
                extra_metadata=extra_metadata,
            )

        # Check for pure Code file (e.g. .py, .js, .ts, .go, .rs in GitHub)
        if source == "github" and any(ext in resource_id for ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp")):
            return self._chunk_code_symbols(
                text=text,
                resource_id=resource_id,
                source=source,
                resource_type=resource_type,
                title=title,
                url=url,
                permissions=permissions,
                created_at=created_at,
                updated_at=updated_at,
                extra_metadata=extra_metadata,
            )

        # General / Structured Document Strategy (Hierarchical + Procedure + Table detection)
        return self._chunk_hierarchical_document(
            text=text,
            resource_id=resource_id,
            source=source,
            resource_type=resource_type,
            title=title,
            url=url,
            permissions=permissions,
            created_at=created_at,
            updated_at=updated_at,
            extra_metadata=extra_metadata,
            structured_data=structured_data,
        )

    # ── Strategy 1: Hierarchical Document Chunker ────────────────────────────

    def _chunk_hierarchical_document(
        self,
        text: str,
        resource_id: str,
        source: str,
        resource_type: str,
        title: str,
        url: Optional[str],
        permissions: Dict[str, Any],
        created_at: Optional[str],
        updated_at: Optional[str],
        extra_metadata: Dict[str, Any],
        structured_data: List[Dict[str, Any]],
    ) -> List[SmartChunk]:
        """
        Maintains heading breadcrumb stack (# -> ## -> ###) and detects
        procedural steps and tables within sections.
        """
        sections = self._split_by_hierarchy(text)
        chunks: List[SmartChunk] = []

        # 1. Pre-scan for multi-section procedure steps (e.g. ### Step 1:, ### Step 2:)
        step_header_re = re.compile(r"^(?:#{1,6}\s+)?(?:Step\s+(\d+)[:\-.]?\s*(.*?))$", re.IGNORECASE)
        step_section_indices: Dict[int, Tuple[int, str]] = {}
        for s_idx, sec in enumerate(sections):
            heading = sec["heading"] or ""
            m = step_header_re.match(heading.strip())
            if m:
                step_num = int(m.group(1))
                detail = m.group(2).strip()
                step_title = f"Step {step_num}: {detail}" if detail else f"Step {step_num}"
                step_section_indices[s_idx] = (step_num, step_title)

        total_header_steps = len(step_section_indices)
        seq_id = f"seq_{_clean_id(title or resource_id)}"

        for idx, sec in enumerate(sections):
            heading_stack = sec["heading_stack"]  # e.g. ["Authentication", "OAuth 2.0", "PKCE Setup"]
            section_heading = sec["heading"]
            content = sec["content"].strip()
            if not content:
                continue

            # If section content is only a single header line with no body text, skip making an empty chunk
            non_empty_lines = [l.strip() for l in content.splitlines() if l.strip()]
            if len(non_empty_lines) <= 1 and non_empty_lines[0].startswith("#") and idx < len(sections) - 1:
                continue

            # Case A: This section itself is an explicit Step section from headers
            if total_header_steps >= 2 and idx in step_section_indices:
                step_num, step_title = step_section_indices[idx]
                c_id = f"{_clean_id(resource_id)}#step_{step_num}"
                chunks.append(
                    SmartChunk(
                        chunk_id=c_id,
                        resource_id=resource_id,
                        source=source,
                        resource_type=resource_type,
                        title=title,
                        url=url,
                        text=content,
                        content_type=ContentType.PROCEDURE_STEP,
                        parent_id=f"{_clean_id(resource_id)}#parent",
                        section_path=list(heading_stack),
                        section_heading=section_heading,
                        sequence=SequenceInfo(
                            sequence_id=seq_id,
                            step=step_num,
                            total_steps=total_header_steps,
                            step_title=step_title,
                        ),
                        permissions=permissions,
                        created_at=created_at,
                        updated_at=updated_at,
                        extra_metadata=extra_metadata,
                    )
                )
                continue

            # Case B: Check if this section contains inline ordered procedure steps
            proc_steps = self._extract_procedure_steps(content, heading_stack, resource_id)
            if proc_steps:
                for step_idx, p_step in enumerate(proc_steps):
                    c_id = f"{_clean_id(resource_id)}#s{idx}_p{step_idx}"
                    chunks.append(
                        SmartChunk(
                            chunk_id=c_id,
                            resource_id=resource_id,
                            source=source,
                            resource_type=resource_type,
                            title=title,
                            url=url,
                            text=p_step["text"],
                            content_type=ContentType.PROCEDURE_STEP,
                            parent_id=f"{_clean_id(resource_id)}#sec_{idx}",
                            parent_text=content[:300] if len(content) > 300 else None,
                            section_path=list(heading_stack),
                            section_heading=section_heading,
                            sequence=p_step["sequence"],
                            permissions=permissions,
                            created_at=created_at,
                            updated_at=updated_at,
                            extra_metadata=extra_metadata,
                        )
                    )
                continue

            # Case C: Check if this section is a Markdown Table
            if self._is_table_block(content):
                c_id = f"{_clean_id(resource_id)}#tbl_{idx}"
                chunks.append(
                    SmartChunk(
                        chunk_id=c_id,
                        resource_id=resource_id,
                        source=source,
                        resource_type=resource_type,
                        title=title,
                        url=url,
                        text=content,
                        content_type=ContentType.TABLE_RECORD,
                        parent_id=f"{_clean_id(resource_id)}#sec_{idx}",
                        section_path=list(heading_stack),
                        section_heading=section_heading,
                        permissions=permissions,
                        created_at=created_at,
                        updated_at=updated_at,
                        extra_metadata=extra_metadata,
                    )
                )
                continue

            # Case D: Standard Document Section (split if oversized)
            sub_texts = self._refine_chunk_size(content)
            for sub_idx, sub_t in enumerate(sub_texts):
                c_id = f"{_clean_id(resource_id)}#sec_{idx}_{sub_idx}" if len(sub_texts) > 1 else f"{_clean_id(resource_id)}#sec_{idx}"
                chunks.append(
                    SmartChunk(
                        chunk_id=c_id,
                        resource_id=resource_id,
                        source=source,
                        resource_type=resource_type,
                        title=title,
                        url=url,
                        text=sub_t,
                        content_type=ContentType.DOCUMENT_SECTION,
                        parent_id=resource_id,
                        section_path=list(heading_stack),
                        section_heading=section_heading,
                        permissions=permissions,
                        created_at=created_at,
                        updated_at=updated_at,
                        extra_metadata=extra_metadata,
                    )
                )

        return chunks

    # ── Strategy 2: Conversation & Email Thread Chunker ──────────────────────

    def _chunk_conversation_thread(
        self,
        text: str,
        resource_id: str,
        source: str,
        resource_type: str,
        title: str,
        url: Optional[str],
        permissions: Dict[str, Any],
        created_at: Optional[str],
        updated_at: Optional[str],
        extra_metadata: Dict[str, Any],
    ) -> List[SmartChunk]:
        """
        Chunks message turns while keeping the conversation context and metadata intact.
        """
        # Slices by headers or message blocks
        sections = self._split_by_hierarchy(text)
        chunks: List[SmartChunk] = []

        for idx, sec in enumerate(sections):
            content = sec["content"].strip()
            if not content:
                continue

            c_id = f"{_clean_id(resource_id)}#msg_{idx}"
            chunks.append(
                SmartChunk(
                    chunk_id=c_id,
                    resource_id=resource_id,
                    source=source,
                    resource_type=resource_type,
                    title=title,
                    url=url,
                    text=content,
                    content_type=ContentType.CONVERSATION_THREAD,
                    parent_id=resource_id,
                    section_path=sec["heading_stack"],
                    section_heading=sec["heading"],
                    permissions=permissions,
                    created_at=created_at,
                    updated_at=updated_at,
                    extra_metadata=extra_metadata,
                )
            )

        return chunks

    # ── Strategy 3: Code Symbol Chunker ──────────────────────────────────────

    def _chunk_code_symbols(
        self,
        text: str,
        resource_id: str,
        source: str,
        resource_type: str,
        title: str,
        url: Optional[str],
        permissions: Dict[str, Any],
        created_at: Optional[str],
        updated_at: Optional[str],
        extra_metadata: Dict[str, Any],
    ) -> List[SmartChunk]:
        """
        Splits code along class, function, or logical definition boundaries.
        """
        lines = text.splitlines()
        symbol_blocks: List[Tuple[str, List[str]]] = []
        current_symbol = "module_header"
        current_lines: List[str] = []

        # Matches python 'def ', 'class ', 'async def ', or JS/TS 'function ', 'class '
        symbol_pattern = re.compile(r"^(class\s+\w+|def\s+\w+|async\s+def\s+\w+|export\s+function\s+\w+|function\s+\w+)")

        for line in lines:
            match = symbol_pattern.match(line.strip())
            if match and current_lines:
                symbol_blocks.append((current_symbol, current_lines))
                current_symbol = match.group(1)
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            symbol_blocks.append((current_symbol, current_lines))

        chunks: List[SmartChunk] = []
        for idx, (sym_name, sym_lines) in enumerate(symbol_blocks):
            code_text = "\n".join(sym_lines).strip()
            if not code_text:
                continue

            c_id = f"{_clean_id(resource_id)}#sym_{idx}"
            chunks.append(
                SmartChunk(
                    chunk_id=c_id,
                    resource_id=resource_id,
                    source=source,
                    resource_type=resource_type,
                    title=title,
                    url=url,
                    text=code_text,
                    content_type=ContentType.CODE_SYMBOL,
                    parent_id=resource_id,
                    section_path=[title, sym_name],
                    section_heading=sym_name,
                    permissions=permissions,
                    created_at=created_at,
                    updated_at=updated_at,
                    extra_metadata={
                        **extra_metadata,
                        "symbol_name": sym_name,
                    },
                )
            )

        return chunks

    # ── Internal Structural Helpers ──────────────────────────────────────────

    def _split_by_hierarchy(self, markdown: str) -> List[Dict[str, Any]]:
        """
        Parses Markdown tracking a running breadcrumb stack of headings.
        Returns list of dicts: {'heading': str, 'heading_stack': List[str], 'content': str}
        """
        lines = markdown.splitlines()
        sections: List[Dict[str, Any]] = []

        heading_stack: List[Tuple[int, str]] = []  # List of (level, title)
        current_lines: List[str] = []
        current_heading: Optional[str] = None
        in_code_block = False

        header_regex = re.compile(r"^(#{1,6})\s+(.+)$")

        for line in lines:
            if line.strip().startswith("```"):
                in_code_block = not in_code_block

            match = header_regex.match(line) if not in_code_block else None
            if match:
                # Flush previous section
                if current_lines:
                    sec_text = "\n".join(current_lines).strip()
                    if sec_text:
                        sections.append({
                            "heading": current_heading,
                            "heading_stack": [h[1] for h in heading_stack],
                            "content": sec_text,
                        })
                    current_lines = []

                level = len(match.group(1))
                h_title = match.group(2).strip()

                # Pop headings at equal or deeper level from stack
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, h_title))

                current_heading = line.strip()
                current_lines.append(line)
            else:
                current_lines.append(line)

        # Flush final section
        if current_lines:
            sec_text = "\n".join(current_lines).strip()
            if sec_text:
                sections.append({
                    "heading": current_heading,
                    "heading_stack": [h[1] for h in heading_stack],
                    "content": sec_text,
                })

        return sections

    def _extract_procedure_steps(
        self,
        text: str,
        heading_stack: List[str],
        resource_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Detects if text contains ordered steps (e.g. 'Step 1: ...', '1. ...')
        Returns list of step dicts with SequenceInfo if >=2 ordered steps exist.
        """
        # Check for explicit 'Step N:' or 'Step N -'
        step_regex = re.compile(r"(?:^|\n)(?:###?\s+)?(?:Step\s+(\d+)[:\-.]?\s*(.*?))(?=\n(?:###?\s+)?Step\s+\d+[:\-.]|\Z)", re.DOTALL | re.IGNORECASE)
        matches = list(step_regex.finditer(text))

        if len(matches) >= 2:
            seq_id = f"seq_{_clean_id(heading_stack[-1] if heading_stack else resource_id)}"
            total = len(matches)
            results = []
            for m in matches:
                step_num = int(m.group(1))
                step_body = m.group(0).strip()
                step_title = m.group(2).splitlines()[0].strip() if m.group(2) else f"Step {step_num}"
                results.append({
                    "text": step_body,
                    "sequence": SequenceInfo(
                        sequence_id=seq_id,
                        step=step_num,
                        total_steps=total,
                        step_title=step_title,
                    ),
                })
            return results

        return []

    def _is_table_block(self, text: str) -> bool:
        """Returns True if block contains a Markdown table."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        for i in range(len(lines) - 1):
            if "|" in lines[i] and any(sep in lines[i+1] for sep in ("|---", "| ---", "|:---", "| :---", "|-", "| -")):
                return True
        return False

    def _refine_chunk_size(self, text: str) -> List[str]:
        """
        Splits oversized text using paragraph boundaries while ensuring each chunk
        remains within [min_chunk_chars, max_chunk_chars].
        """
        if len(text) <= self.max_chunk_chars:
            return [text]

        paragraphs = text.split("\n\n")
        chunks: List[str] = []
        current_paras: List[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para)
            if current_len + para_len + 2 <= self.max_chunk_chars:
                current_paras.append(para)
                current_len += para_len + 2
            else:
                if current_paras:
                    chunks.append("\n\n".join(current_paras).strip())
                    current_paras = []
                    current_len = 0

                if para_len > self.max_chunk_chars:
                    # Single massive paragraph -> character sliding window with overlap
                    step = self.max_chunk_chars - self.overlap_chars
                    for i in range(0, para_len, max(1, step)):
                        slice_t = para[i : i + self.max_chunk_chars].strip()
                        if slice_t:
                            chunks.append(slice_t)
                else:
                    current_paras.append(para)
                    current_len = para_len

        if current_paras:
            chunks.append("\n\n".join(current_paras).strip())

        return chunks

    def _link_sibling_chunks(self, chunks: List[SmartChunk]) -> List[SmartChunk]:
        """
        Second pass: wires bidirectional sibling pointers (prev_chunk_id, next_chunk_id)
        and global index metadata across the entire sequence.
        """
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
            chunk.total_chunks = total
            chunk.prev_chunk_id = chunks[i - 1].chunk_id if i > 0 else None
            chunk.next_chunk_id = chunks[i + 1].chunk_id if i < (total - 1) else None
        return chunks


# Alias for backward compatibility
OKFChunker = SmartOKFChunker


def _clean_id(s: str) -> str:
    """Sanitizes resource string into a safe identifier key."""
    return re.sub(r"[^a-zA-Z0-9_:-]", "_", s)
