"""
Confluence Data Extraction & Normalization Parser.

Transforms raw Confluence REST API JSON (spaces, pages, Storage-Format bodies)
into our standardized intermediate representation, retaining essential metadata,
structured records (tables), and source attribution. Converts the Confluence
Storage Format (XHTML) into semantic block dictionaries.
"""

import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

# Confluence Storage Format element tags that carry no textual knowledge
IGNORED_CONFLUENCE_TAGS = {
    "ac:image", "ac:thumbnail", "ri:attachment", "ri:url", "img",
    "ac:link", "ac:plain-text-link-body", "ac:task", "ac:history",
    "ac:metadata", "ac:status", "ac:status-attribute",
    "media", "media-single", "media-group", "mention", "image",
}

# Heading tags mapped to internal heading levels
HEADING_TAG_LEVELS = {
    "h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6,
}

# Unordered / ordered list tag kinds
LIST_TAG_KINDS = {
    "ul": "bulleted_list_item",
    "ol": "numbered_list_item",
}


def _slug(text: str, prefix: str = "block") -> str:
    """Generates a short stable identifier from arbitrary text."""
    clean = re.sub(r"[^\w\s-]", "", text).strip().lower()
    clean = re.sub(r"[-\s]+", "_", clean)[:60]
    return clean or prefix


def build_heading(text: str, level: int = 1) -> Dict[str, Any]:
    """Builds a normalized heading block dictionary."""
    clean = text.strip()
    return {
        "type": f"heading_{min(max(level, 1), 4)}",
        "text": clean,
        "block_id": _slug(clean, "heading"),
        "level": min(max(level, 1), 4),
        "properties": {"level": min(max(level, 1), 4)},
    }


def build_paragraph(text: str) -> Dict[str, Any]:
    """Builds a normalized paragraph block dictionary."""
    clean = text.strip()
    return {
        "type": "paragraph",
        "text": clean,
        "block_id": _slug(clean, "para"),
    }


class _TreeNode:
    """
    Minimal lightweight tree node produced by the storage-format parser.

    ``content`` preserves document order: entries are either raw ``str`` text
    segments or nested ``_TreeNode`` instances for child elements.
    """

    __slots__ = ("tag", "attrs", "content")

    def __init__(self, tag: str = "root", attrs: Optional[List[Any]] = None):
        self.tag = tag
        self.attrs = attrs or []
        self.content: List[Any] = []

    @property
    def children(self) -> List["_TreeNode"]:
        return [c for c in self.content if isinstance(c, _TreeNode)]

    @property
    def text(self) -> str:
        return "".join(c for c in self.content if isinstance(c, str))


class _StorageTreeParser(HTMLParser):
    """
    Converts Confluence Storage-Format XHTML into a simple element tree.
    Uses convert_charrefs so entities (&amp;, &#39;) are decoded automatically.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _TreeNode("root")
        self.stack: List[_TreeNode] = [self.root]

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        node = _TreeNode(tag, attrs)
        self.stack[-1].content.append(node)
        self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: List[Any]) -> None:
        node = _TreeNode(tag, attrs)
        self.stack[-1].content.append(node)
        if tag == "br":
            self.stack[-1].content.append("\n")

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].content.append(data)


def _parse_storage_tree(storage_html: str) -> _TreeNode:
    """Parses Storage-Format XHTML into a tree and returns its root body node."""
    parser = _StorageTreeParser()
    parser.feed(storage_html or "")
    parser.close()
    return parser.root


def _attrs_map(node: _TreeNode) -> Dict[str, str]:
    return {k: v for k, v in node.attrs}


def _collect_inline_text(node: _TreeNode) -> str:
    """
    Collects readable text from a tree node in document order, preserving links
    and line breaks while skipping images/media and other non-textual wrappers.
    """
    parts: List[str] = []
    seen_p = False

    def collect_children(n: _TreeNode) -> None:
        for entry in n.content:
            if isinstance(entry, str):
                parts.append(entry)
            else:
                walk(entry)

    def walk(n: _TreeNode) -> None:
        nonlocal seen_p
        if n.tag == "br":
            parts.append("\n")
            return
        if n.tag in ("p",):
            if seen_p:
                parts.append("\n")
            seen_p = True
        if n.tag == "a":
            href = dict(n.attrs).get("href", "")
            start = len(parts)
            collect_children(n)
            text = "".join(parts[start:]).strip()
            if href.startswith("http") and text:
                parts[start:] = [f"{text} ({href})"]
            return
        if n.tag in IGNORED_CONFLUENCE_TAGS:
            return
        collect_children(n)

    walk(node)
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _code_language(node: _TreeNode) -> str:
    """Infers a code language hint from a <pre class="..."> or <code> node."""
    attrs = _attrs_map(node)
    cls = attrs.get("class", "")
    if "code-" in cls:
        return cls.split("code-", 1)[-1].split(" ")[0] or ""
    if cls:
        return cls.split(" ")[0]
    return ""


def _table_to_blocks(node: _TreeNode) -> List[Dict[str, Any]]:
    """Converts a Storage-Format <table> into a structured DATABASE block."""
    header_cells: List[str] = []
    body_rows: List[List[str]] = []

    for tr in node.children:
        if tr.tag != "tr":
            continue
        cells = [c for c in tr.children if c.tag in ("th", "td", "tc")]
        if not cells:
            continue
        values = [_collect_inline_text(c) for c in cells]
        is_header = any(c.tag in ("th", "tc") for c in cells)
        if is_header and not header_cells:
            header_cells = values
        else:
            body_rows.append(values)

    columns = header_cells or [
        f"Column {i + 1}" for i in range(max((len(r) for r in body_rows), default=0))
    ]
    if not columns:
        return []

    rows = []
    for idx, row in enumerate(body_rows, 1):
        rows.append({
            "id": str(idx),
            "data": {columns[j]: (row[j] if j < len(row) else "") for j in range(len(columns))},
        })

    return [{
        "type": "database",
        "text": "Confluence Table",
        "block_id": "confluence_table",
        "columns": columns,
        "rows": rows,
        "total_rows": len(rows),
    }]


def _node_to_blocks(node: _TreeNode, depth: int = 0) -> List[Dict[str, Any]]:
    """Recursively converts a Storage-Format tree node into block dictionaries."""
    blocks: List[Dict[str, Any]] = []
    tag = (node.tag or "").lower()

    if tag in HEADING_TAG_LEVELS:
        text = _collect_inline_text(node)
        if text:
            blocks.append(build_heading(text, HEADING_TAG_LEVELS[tag]))
    elif tag == "p":
        text = _collect_inline_text(node)
        if text:
            blocks.append(build_paragraph(text))
    elif tag in LIST_TAG_KINDS:
        kind = LIST_TAG_KINDS[tag]
        for child in node.children:
            if child.tag != "li":
                continue
            item_text = ""
            nested_blocks: List[Dict[str, Any]] = []
            for entry in child.content:
                if isinstance(entry, str):
                    item_text += entry
                elif entry.tag in LIST_TAG_KINDS:
                    nested_blocks.extend(_node_to_blocks(entry, depth + 1))
                else:
                    item_text += f"{_collect_inline_text(entry)} "
            item = {
                "type": kind,
                "text": item_text.strip(),
                "block_id": _slug(item_text, "list"),
            }
            if nested_blocks:
                item["children"] = nested_blocks
            blocks.append(item)
    elif tag == "blockquote":
        text = _collect_inline_text(node)
        if text:
            blocks.append({"type": "quote", "text": text, "block_id": _slug(text, "quote")})
    elif tag in ("pre", "code"):
        text = _collect_inline_text(node)
        if text:
            blocks.append({
                "type": "code",
                "text": text,
                "block_id": _slug(text, "code"),
                "language": _code_language(node),
                "properties": {"language": _code_language(node)},
            })
    elif tag == "table":
        blocks.extend(_table_to_blocks(node))
    elif tag == "hr":
        blocks.append({"type": "divider", "text": "", "block_id": "divider"})
    elif tag in ("ac:code",):
        text = _collect_inline_text(node)
        if text:
            language = _code_language(node)
            blocks.append({
                "type": "code",
                "text": text,
                "block_id": _slug(text, "code"),
                "language": language,
                "properties": {"language": language},
            })
    elif tag in IGNORED_CONFLUENCE_TAGS:
        pass  # Media, images, tasks, and metadata carry no retrievable text.
    elif tag in ("ac:structured-macro", "ac:rich-text-body", "div", "body", "root",
                 "section", "article", "tbody", "thead", "table-wrap", "ac:layout-cell",
                 "ac:layout", "ac:parameter"):
        for child in node.children:
            blocks.extend(_node_to_blocks(child, depth))
    else:
        text = _collect_inline_text(node)
        if text:
            blocks.append(build_paragraph(text))

    return blocks


def storage_to_blocks(storage_html: str) -> List[Dict[str, Any]]:
    """
    Converts a Confluence Storage-Format body into normalized block dictionaries.

    Args:
        storage_html: Raw XHTML from 'body.storage.value'.

    Returns:
        List of block dictionaries (headings, paragraphs, lists, code, tables).
    """
    if not storage_html or not storage_html.strip():
        return []
    root = _parse_storage_tree(storage_html)
    blocks: List[Dict[str, Any]] = []
    for child in root.children:
        blocks.extend(_node_to_blocks(child))
    return blocks


def _join_page_url(base: str, webui: Optional[str]) -> Optional[str]:
    """Robustly joins Confluence `_links.base` and `_links.webui` avoiding a doubled /wiki prefix."""
    if not webui:
        return base or None
    if not base:
        return webui
    base = base.rstrip("/")
    if base.endswith("/wiki") and webui.startswith("/wiki"):
        return base + webui[len("/wiki"):]
    return base + webui


def extract_page_metadata(page: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts high-signal page-level metadata into our document format.
    """
    version = page.get("version") or {}
    space = page.get("space") or {}
    history = page.get("history") or {}
    ancestors = page.get("ancestors") or []
    parent = ancestors[-1] if ancestors else None

    links = page.get("_links") or {}
    webui = links.get("webui")
    base = links.get("base") or ""
    url = _join_page_url(base, webui)

    version_by = version.get("by") or {}
    history_created = history.get("createdBy") or {}
    history_last = history.get("lastUpdated") or {}

    return {
        "source": "confluence",
        "source_id": str(page.get("id") or ""),
        "title": page.get("title") or "Untitled",
        "url": url,
        "parent_type": "space" if not parent else "confluence_page",
        "parent_id": (str(parent.get("id")) if parent is not None and parent.get("id") else None)
        or space.get("key"),
        "created_at": history.get("createdDate") or history_created.get("when"),
        "updated_at": version.get("when") or history_last.get("when"),
        "created_by": history_created.get("displayName") or history_created.get("username"),
        "last_edited_by": version_by.get("displayName") or version_by.get("username"),
        "extra": {
            "space_key": space.get("key"),
            "space_name": space.get("name"),
            "type": page.get("type"),
            "status": page.get("status"),
            "version": version.get("number"),
        },
    }


def normalize_page_document(page: Dict[str, Any]) -> Dict[str, Any]:
    """
    Combines page metadata and its Storage-Format body into a normalized Document dict.
    """
    doc = extract_page_metadata(page)
    blocks: List[Dict[str, Any]] = []

    blocks.append(build_heading(page.get("title") or "Untitled", level=1))

    meta_lines = []
    space = page.get("space") or {}
    version = page.get("version") or {}
    if space.get("key"):
        meta_lines.append(f"Space: {space.get('key', '')} ({space.get('name', '')})")
    if page.get("type"):
        meta_lines.append(f"Type: {page.get('type')}")
    if version.get("number"):
        meta_lines.append(f"Version: {version.get('number')}")
    if doc["url"]:
        meta_lines.append(f"URL: {doc['url']}")
    if meta_lines:
        blocks.append({
            "type": "callout",
            "text": "\n".join(meta_lines),
            "block_id": "confluence_meta",
            "properties": {"icon": "📄"},
        })

    storage = (page.get("body") or {}).get("storage") or {}
    storage_value = storage.get("value") or ""
    if storage_value.strip():
        blocks.extend(storage_to_blocks(storage_value))
    else:
        blocks.append(build_paragraph("(No body content available for this page.)"))

    doc["content"] = blocks
    return doc


def normalize_space_document(space: Dict[str, Any]) -> Dict[str, Any]:
    """
    Combines space metadata into a single normalized Document dict.
    """
    links = space.get("_links") or {}
    webui = links.get("webui")
    base = links.get("base") or ""
    url = _join_page_url(base, webui)
    description = (space.get("description") or {}).get("plain", {}).get("value") or ""

    doc = {
        "source": "confluence",
        "source_id": space.get("key") or str(space.get("id") or ""),
        "title": space.get("name") or space.get("key") or "Untitled Space",
        "url": url,
        "parent_type": "confluence_site",
        "parent_id": "confluence",
        "created_at": None,
        "updated_at": None,
        "created_by": None,
        "last_edited_by": None,
        "extra": {
            "space_key": space.get("key"),
            "space_id": space.get("id"),
            "type": "space",
        },
    }

    blocks: List[Dict[str, Any]] = []
    blocks.append(build_heading(space.get("name") or space.get("key") or "Space", level=1))
    if space.get("key"):
        blocks.append(build_paragraph(f"Space Key: {space.get('key')}"))
    if url:
        blocks.append(build_paragraph(f"URL: {url}"))
    if description.strip():
        blocks.append(build_paragraph(description.strip()))

    if space.get("id"):
        blocks.append({
            "type": "database",
            "text": f"Space: {space.get('name')}",
            "block_id": f"space_{space.get('key')}",
            "columns": ["Space Key", "Name", "Space ID", "URL"],
            "rows": [{
                "id": space.get("key") or "",
                "data": {
                    "Space Key": space.get("key") or "",
                    "Name": space.get("name") or "",
                    "Space ID": space.get("id") or "",
                    "URL": url or "",
                },
            }],
            "total_rows": 1,
        })

    doc["content"] = blocks
    return doc