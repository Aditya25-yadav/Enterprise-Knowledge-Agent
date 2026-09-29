"""
Query Scope Modifier Parser & Normalizer.

Parses explicit command directives (e.g. @enterprise, @general, @docs, @llm, @web, @jira, @github)
to provide deterministic control over agent reasoning and tool routing.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

SCOPE_PREFIX_PATTERN = re.compile(
    r"^(?:@|/)(enterprise|docs|general|llm|web|github|jira|notion|dropbox|gmail|confluence)\b[\s:]*",
    re.IGNORECASE,
)

ALL_CONNECTORS = frozenset({"github", "jira", "notion", "dropbox", "gmail", "confluence"})


def parse_query_scope(raw_query: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Extracts explicit scope modifier from user query.

    Returns:
        (cleaned_query, scope_directive, target_connector)
        e.g.
            ("@general what is authentication?") -> ("what is authentication?", "general", None)
            ("@jira status of PAY-928") -> ("status of PAY-928", "connector", "jira")
            ("@enterprise how do I failover?") -> ("how do I failover?", "enterprise", None)
            ("regular query") -> ("regular query", None, None)
    """
    if not raw_query or not raw_query.strip():
        return (raw_query or "").strip(), None, None

    match = SCOPE_PREFIX_PATTERN.match(raw_query.strip())
    if not match:
        return raw_query.strip(), None, None

    prefix = match.group(1).lower()
    cleaned_query = raw_query.strip()[match.end() :].strip()

    if prefix in ("general", "llm"):
        return cleaned_query, "general", None
    elif prefix in ("enterprise", "docs"):
        return cleaned_query, "enterprise", None
    elif prefix == "web":
        return cleaned_query, "web", None
    elif prefix in ALL_CONNECTORS:
        return cleaned_query, "connector", prefix

    return raw_query.strip(), None, None
