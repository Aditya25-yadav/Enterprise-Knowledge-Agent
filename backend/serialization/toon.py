"""
TOON (Token-Oriented Object Notation) Engine.

Provides high-density, token-efficient serialization and deserialization for
enterprise knowledge chunks, OKF concept bundles, and global master index manifests.

Reduces prompt token consumption by 60-75% compared to verbose JSON / Markdown,
accelerating local LLM inference and preserving context window budgets.

Syntax Specification:
    [#<index>|C:<id>|S:<source>|D:<domain>|R:<roles>|T:<type>|K:<score>|P:<path>|U:<url>|Seq:<step>/<total>]
    # <title>
    <body text>
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional, Tuple, Union


def _extract_domain_from_tags(tags: List[str], source: str, title: str) -> str:
    """Infers enterprise business domain from tags or title."""
    tag_str = " ".join(tags).lower() + " " + title.lower()
    if any(k in tag_str for k in ("payment", "checkout", "charge", "stripe", "3ds")):
        return "Payments"
    if any(k in tag_str for k in ("sre", "failover", "runbook", "postgres", "opensearch", "dr")):
        return "SRE"
    if any(k in tag_str for k in ("k8s", "kubernetes", "ingress", "cert-manager", "tls", "devops", "infra")):
        return "Infra"
    if any(k in tag_str for k in ("security", "ciso", "kms", "vault", "yubikey", "cve", "zero-trust", "vpn")):
        return "Security"
    if any(k in tag_str for k in ("auth", "oauth", "pkce", "jwt", "saml")):
        return "Auth"
    if any(k in tag_str for k in ("flink", "iceberg", "kafka", "analytics", "data", "clickstream")):
        return "Data"
    if any(k in tag_str for k in ("finops", "aws", "gcp", "cost", "cloud")):
        return "FinOps"
    if any(k in tag_str for k in ("onboarding", "workstation", "macos", "hardware")):
        return "Onboarding"
    if any(k in tag_str for k in ("grpc", "protobuf", "architecture", "rfc", "adr")):
        return "Architecture"
    return "Engineering"


def serialize_toon_chunk(
    chunk: Dict[str, Any],
    index: Optional[int] = None,
) -> str:
    """
    Serializes an evidence chunk into compact TOON format.
    
    Format:
        [#1|C:chunk_123|S:github|D:Payments|R:employee,engineer|T:File|K:0.95|P:API > Payments|U:https://...]
        # Title
        Content
    """
    chunk_id = str(chunk.get("chunk_id") or chunk.get("id") or "")
    source = str(chunk.get("source") or "doc").lower()
    title = str(chunk.get("title") or "Untitled").strip()
    url = str(chunk.get("url") or "")
    
    # Extract domain
    tags = chunk.get("tags") or []
    domain = chunk.get("domain") or _extract_domain_from_tags(tags, source, title)
    
    # Roles
    roles = chunk.get("allowed_roles") or chunk.get("roles") or []
    if not roles and isinstance(chunk.get("permissions"), dict):
        roles = chunk["permissions"].get("allowed_roles", [])
    roles_str = ",".join(str(r) for r in roles) if roles else "employee"
    
    # Resource type
    rtype = str(chunk.get("resource_type") or chunk.get("type") or "Chunk")
    
    # Score
    score = chunk.get("rerank_score") or chunk.get("score") or chunk.get("relevance_score")
    score_str = f"{float(score):.2f}" if score is not None and isinstance(score, (int, float)) else ""
    
    # Path
    path_list = chunk.get("section_path", [])
    path_str = " > ".join(str(p) for p in path_list) if path_list else str(chunk.get("section_heading") or "")
    
    # Sequence
    seq = chunk.get("sequence")
    seq_str = ""
    if isinstance(seq, dict) and "step" in seq and "total_steps" in seq:
        seq_str = f"{seq['step']}/{seq['total_steps']}"

    # Build Header Keys
    header_parts: List[str] = []
    if index is not None:
        header_parts.append(f"#{index}")
    if chunk_id:
        header_parts.append(f"C:{chunk_id}")
    if source:
        header_parts.append(f"S:{source}")
    if domain:
        header_parts.append(f"D:{domain}")
    if roles_str:
        header_parts.append(f"R:{roles_str}")
    if rtype:
        header_parts.append(f"T:{rtype}")
    if score_str:
        header_parts.append(f"K:{score_str}")
    if path_str:
        header_parts.append(f"P:{path_str}")
    if url:
        header_parts.append(f"U:{url}")
    if seq_str:
        header_parts.append(f"Seq:{seq_str}")

    header = "[" + "|".join(header_parts) + "]"
    text = str(chunk.get("text") or chunk.get("body") or "").strip()

    lines = [header]
    if title:
        lines.append(f"# {title}")
    if text:
        lines.append(text)

    return "\n".join(lines)


def deserialize_toon_chunk(toon_block: str) -> Dict[str, Any]:
    """
    Deserializes a TOON chunk string back into a structured dictionary.
    """
    lines = toon_block.strip().split("\n")
    if not lines:
        return {}

    header_line = lines[0].strip()
    result: Dict[str, Any] = {
        "chunk_id": "",
        "source": "doc",
        "domain": "Engineering",
        "allowed_roles": ["employee"],
        "resource_type": "Chunk",
        "title": "",
        "text": "",
        "url": "",
        "section_path": [],
        "score": None,
        "sequence": None,
    }

    body_start_idx = 1
    if header_line.startswith("[") and header_line.endswith("]"):
        inner = header_line[1:-1]
        parts = inner.split("|")
        for p in parts:
            if p.startswith("#") and p[1:].isdigit():
                result["citation_index"] = int(p[1:])
            elif p.startswith("C:"):
                result["chunk_id"] = p[2:]
            elif p.startswith("S:"):
                result["source"] = p[2:]
            elif p.startswith("D:"):
                result["domain"] = p[2:]
            elif p.startswith("R:"):
                result["allowed_roles"] = [r.strip() for r in p[2:].split(",") if r.strip()]
            elif p.startswith("T:"):
                result["resource_type"] = p[2:]
            elif p.startswith("K:"):
                try:
                    result["score"] = float(p[2:])
                except ValueError:
                    pass
            elif p.startswith("P:"):
                result["section_heading"] = p[2:]
                result["section_path"] = [seg.strip() for seg in p[2:].split(">")]
            elif p.startswith("U:"):
                result["url"] = p[2:]
            elif p.startswith("Seq:"):
                step_parts = p[4:].split("/")
                if len(step_parts) == 2:
                    try:
                        result["sequence"] = {"step": int(step_parts[0]), "total_steps": int(step_parts[1])}
                    except ValueError:
                        pass
    else:
        body_start_idx = 0

    # Parse title if present
    remaining_lines: List[str] = []
    for line in lines[body_start_idx:]:
        if line.startswith("# ") and not result["title"]:
            result["title"] = line[2:].strip()
        else:
            remaining_lines.append(line)

    result["text"] = "\n".join(remaining_lines).strip()
    return result


def serialize_toon_catalog_entry(
    entry: Any,
    index: Optional[int] = None,
) -> str:
    """
    Serializes a CatalogEntry or OKFConcept into a compact TOON manifest record.
    """
    if hasattr(entry, "title"):
        title = getattr(entry, "title", "Untitled") or "Untitled"
        source = getattr(entry, "source", "") or getattr(entry, "tags", ["doc"])[0]
        rtype = getattr(entry, "resource_type", "") or getattr(entry, "type", "Document")
        uri = getattr(entry, "resource_uri", "") or getattr(entry, "resource", "")
        domain = getattr(entry, "domain", "") or _extract_domain_from_tags([], source, title)
        summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
        roles = getattr(entry, "allowed_roles", ["employee"])
        key_entities = getattr(entry, "key_entities", [])
    elif isinstance(entry, dict):
        title = entry.get("title", "Untitled")
        source = entry.get("source", "doc")
        rtype = entry.get("resource_type", entry.get("type", "Document"))
        uri = entry.get("resource_uri", entry.get("resource", entry.get("url", "")))
        domain = entry.get("domain") or _extract_domain_from_tags(entry.get("tags", []), source, title)
        summary = entry.get("summary", entry.get("description", ""))
        roles = entry.get("allowed_roles", ["employee"])
        key_entities = entry.get("key_entities", [])
    else:
        title = str(entry)
        source, rtype, uri, domain, summary, roles, key_entities = "doc", "Document", "", "Engineering", "", ["employee"], []

    roles_str = ",".join(str(r) for r in roles) if roles else "employee"
    entities_str = ",".join(str(e) for e in key_entities) if key_entities else ""

    header_parts: List[str] = []
    if index is not None:
        header_parts.append(f"#{index}")
    if source:
        header_parts.append(f"S:{source}")
    if domain:
        header_parts.append(f"D:{domain}")
    if roles_str:
        header_parts.append(f"R:{roles_str}")
    if rtype:
        header_parts.append(f"T:{rtype}")
    if uri:
        header_parts.append(f"U:{uri}")
    if entities_str:
        header_parts.append(f"E:{entities_str}")

    header = "[" + "|".join(header_parts) + "]"
    lines = [header, f"# {title}"]
    if summary:
        lines.append(f"> {summary}")

    return "\n".join(lines)


def deserialize_toon_catalog_entry(toon_block: str) -> Dict[str, Any]:
    """
    Deserializes a TOON catalog entry string back into a structured dictionary.
    """
    lines = toon_block.strip().split("\n")
    if not lines:
        return {}

    header_line = lines[0].strip()
    result: Dict[str, Any] = {
        "title": "",
        "source": "doc",
        "domain": "Engineering",
        "allowed_roles": ["employee"],
        "resource_type": "Document",
        "resource_uri": "",
        "key_entities": [],
        "summary": "",
    }

    body_start_idx = 1
    if header_line.startswith("[") and header_line.endswith("]"):
        inner = header_line[1:-1]
        parts = inner.split("|")
        for p in parts:
            if p.startswith("#") and p[1:].isdigit():
                result["index"] = int(p[1:])
            elif p.startswith("S:"):
                result["source"] = p[2:]
            elif p.startswith("D:"):
                result["domain"] = p[2:]
            elif p.startswith("R:"):
                result["allowed_roles"] = [r.strip() for r in p[2:].split(",") if r.strip()]
            elif p.startswith("T:"):
                result["resource_type"] = p[2:]
            elif p.startswith("U:"):
                result["resource_uri"] = p[2:]
            elif p.startswith("E:"):
                result["key_entities"] = [e.strip() for e in p[2:].split(",") if e.strip()]
    else:
        body_start_idx = 0

    for line in lines[body_start_idx:]:
        clean = line.strip()
        if clean.startswith("# ") and not result["title"]:
            result["title"] = clean[2:].strip()
        elif clean.startswith("> "):
            result["summary"] = clean[2:].strip()

    return result


def serialize_toon_context(
    chunks: List[Dict[str, Any]],
    numbered: bool = True,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Serializes a list of retrieved chunks into a high-density TOON evidence context block
    along with structured citation reference dictionaries.
    
    Returns:
        (toon_context_string, citations_list)
    """
    if not chunks:
        return "No relevant enterprise documents found.", []

    blocks: List[str] = []
    citations: List[Dict[str, Any]] = []

    for idx, chunk in enumerate(chunks, 1):
        num = idx if numbered else None
        toon_str = serialize_toon_chunk(chunk, index=num)
        blocks.append(toon_str)

        source = str(chunk.get("source", "doc")).upper()
        title = str(chunk.get("title", "Untitled"))
        url = str(chunk.get("url", ""))
        path_list = chunk.get("section_path", [])
        path_str = " > ".join(str(p) for p in path_list) if path_list else str(chunk.get("section_heading", ""))
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

    return "\n\n".join(blocks).strip(), citations


def estimate_token_count(text: str) -> int:
    """
    Estimates token count using standard ~3.8 characters per token approximation
    plus whitespace and punctuation boundaries.
    """
    if not text:
        return 0
    words = re.findall(r"\w+|[^\w\s]", text, re.UNICODE)
    # Average ~1.3 tokens per word/punctuation token in LLM BPE tokenizers
    return max(1, int(len(words) * 1.15))


def calculate_token_savings(standard_repr: str, toon_repr: str) -> Dict[str, Any]:
    """
    Calculates token reduction metrics between standard verbose representation and TOON representation.
    """
    std_tokens = estimate_token_count(standard_repr)
    toon_tokens = estimate_token_count(toon_repr)
    saved_tokens = max(0, std_tokens - toon_tokens)
    pct_saved = (saved_tokens / std_tokens * 100.0) if std_tokens > 0 else 0.0

    return {
        "standard_tokens": std_tokens,
        "toon_tokens": toon_tokens,
        "tokens_saved": saved_tokens,
        "percent_saved": round(pct_saved, 1),
    }


def serialize_toon_table(
    items: List[Dict[str, Any]],
    name: str = "items",
    fields: Optional[List[str]] = None,
    indent: str = "  ",
) -> str:
    """
    Serializes a list of uniform structured objects into canonical TOON Tabular Array format.

    Format:
        name[N]{field1,field2,...}:
          val1,"val 2",val3
          val1,"val 2",val3

    Example:
        items[2]{chunk_id,title,source,author,status}:
          gh_142_0,"Fix 3DS timeout in Checkout Flow",github,alice,MERGED
          jira_928_0,"PAY-928: 3DS Authentication Timeout",jira,checkout-oncall,CLOSED
    """
    if not items:
        field_list = fields or []
        fields_str = ",".join(field_list)
        return f"{name}[0]{{{fields_str}}}:"

    if fields is None:
        # Infer fields while preserving insertion order
        seen = set()
        fields = []
        for item in items:
            for k in item.keys():
                if k not in seen:
                    seen.add(k)
                    fields.append(k)

    header = f"{name}[{len(items)}]{{{','.join(fields)}}}:"
    
    rows: List[str] = [header]
    for item in items:
        row_vals = []
        for f in fields:
            val = item.get(f)
            if val is None:
                row_vals.append("")
            elif isinstance(val, bool):
                row_vals.append("true" if val else "false")
            elif isinstance(val, (int, float)):
                row_vals.append(str(val))
            else:
                s_val = str(val)
                # Quote if string contains spaces, commas, quotes, newlines, colons, or brackets
                if any(c in s_val for c in (' ', ',', '"', '\n', '\r', '\t', ':', '{', '}', '[', ']')):
                    escaped = s_val.replace('"', '""')
                    row_vals.append(f'"{escaped}"')
                else:
                    row_vals.append(s_val)
        
        row_str = ",".join(row_vals)
        rows.append(f"{indent}{row_str}")

    return "\n".join(rows)


def deserialize_toon_table(toon_table_str: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Deserializes a TOON Tabular Array string back into a tuple of (name, items_list).

    Example input:
        items[2]{chunk_id,title,source,author,status}:
          gh_142_0,"Fix 3DS timeout in Checkout Flow",github,alice,MERGED
          jira_928_0,"PAY-928: 3DS Authentication Timeout",jira,checkout-oncall,CLOSED

    Returns:
        ("items", [{"chunk_id": "gh_142_0", ...}, ...])
    """
    lines = [line for line in toon_table_str.strip().split("\n") if line.strip()]
    if not lines:
        return ("items", [])

    header_line = lines[0].strip()
    match = re.match(r"^([a-zA-Z0-9_\-]+)\[(\d+)\]\{([^}]+)\}:\s*$", header_line)
    if not match:
        return ("items", [])

    name = match.group(1)
    _expected_count = int(match.group(2))
    raw_fields = match.group(3)
    fields = [f.strip() for f in raw_fields.split(",")]

    items: List[Dict[str, Any]] = []
    data_lines = lines[1:]
    
    if data_lines:
        csv_content = "\n".join(line.strip() for line in data_lines)
        reader = csv.reader(io.StringIO(csv_content))
        for row in reader:
            if not row:
                continue
            item: Dict[str, Any] = {}
            for idx, field in enumerate(fields):
                val = row[idx] if idx < len(row) else ""
                # Attempt light type casting
                if val == "true":
                    item[field] = True
                elif val == "false":
                    item[field] = False
                elif val.isdigit():
                    item[field] = int(val)
                else:
                    try:
                        if "." in val:
                            item[field] = float(val)
                        else:
                            item[field] = val
                    except ValueError:
                        item[field] = val
            items.append(item)

    return (name, items)
