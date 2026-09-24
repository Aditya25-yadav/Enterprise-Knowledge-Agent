"""
Open Knowledge Format (OKF) v0.2 Specification Implementation.

OKF is an open, human- and agent-friendly format for representing knowledge:
metadata, context, and curated insights surrounding enterprise data and systems.

Specification Reference: OKF v0.2
Structure:
- Bundle of Markdown files with YAML frontmatter
- First-class Provenance (sources, credibility signals, footnote attribution)
- First-class Trust (generated, verified, trust tiers: unverified, machine-confirmed, human-reviewed)
- First-class Lifecycle (status: draft|stable|deprecated, stale_after, timestamps for change tracking)
- Index and Log file support (index.md, log.md)
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional, Union
from backend.models.document import BlockType, ContentBlock, Document, DocumentMetadata


@dataclass
class OKFActor:
    """
    OKF v0.2 Actor convention:
    - '<producer>/<version>' for agents (e.g. 'notion_connector/v1.0', 'gemini-2.5-pro')
    - 'human:<id>' for people (e.g. 'human:ompatil')
    - 'process:<id>' for automated jobs (e.g. 'process:nightly-sync')
    """
    by: str
    at: str                                      # ISO 8601 UTC timestamp

    def to_dict(self) -> Dict[str, str]:
        return {"by": self.by, "at": self.at}


@dataclass
class OKFSource:
    """
    OKF v0.2 Provenance Source entry.
    """
    resource: str                                # REQUIRED: URL, bundle path, or scope descriptor
    id: Optional[str] = None                     # Join key matching body footnotes [^id]
    title: Optional[str] = None                  # Human-readable label
    author: Optional[str] = None                 # Authority signal (Actor string)
    usage_count: Optional[int] = None            # Liveness/adoption exercise count
    last_modified: Optional[str] = None          # When the source itself changed (ISO 8601)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"resource": self.resource}
        if self.id:
            d["id"] = self.id
        if self.title:
            d["title"] = self.title
        if self.author:
            d["author"] = self.author
        if self.usage_count is not None:
            d["usage_count"] = self.usage_count
        if self.last_modified:
            d["last_modified"] = self.last_modified
        return d


@dataclass
class OKFPermissions:
    """
    Enterprise RBAC Extension for OKF.
    Enables pre-filter access control during Vector/Graph retrieval.
    """
    allowed_roles: List[str] = field(default_factory=lambda: ["employee"])
    allowed_users: List[str] = field(default_factory=list)
    allowed_groups: List[str] = field(default_factory=list)
    is_public: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OKFConcept:
    """
    An OKF v0.2 Concept Document (representing a single knowledge document/unit).
    """
    # Core Frontmatter (§4.1)
    type: str                                    # REQUIRED: e.g. 'Notion Page', 'Architecture', 'Playbook'
    title: Optional[str] = None                  # Display name
    description: Optional[str] = None            # One-line summary
    resource: Optional[str] = None               # Canonical URI of the underlying asset (e.g. Notion page URL)
    tags: List[str] = field(default_factory=list)# Category tags

    # Timestamps & Change Tracking
    created_at: Optional[str] = None             # ISO 8601 creation timestamp
    updated_at: Optional[str] = None             # ISO 8601 last modified timestamp

    # Trust Family (§5.2)
    generated: Optional[OKFActor] = None         # { by: actor, at: timestamp }
    verified: List[OKFActor] = field(default_factory=list) # List of verification events

    # Lifecycle Family (§5.4, §5.5)
    status: str = "stable"                       # draft | stable | deprecated
    stale_after: Optional[str] = None            # ISO 8601 timestamp when content becomes stale

    # Provenance Family (§5.1)
    sources: List[OKFSource] = field(default_factory=list) # Provenance sources
    usage_window: Optional[Dict[str, str]] = None          # { from, to } for usage_count framing

    # Enterprise Extensions
    permissions: OKFPermissions = field(default_factory=OKFPermissions)
    content_hash: str = ""                       # SHA-256 hash of content
    extra_metadata: Dict[str, Any] = field(default_factory=dict) # Platform-specific tags/details

    # Markdown Body (§4.2)
    body: str = ""                               # Structural Markdown content

    # Structured Data Records (for databases/charts)
    structured_data: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def trust_tier(self) -> str:
        """
        Derives trust tier per §5.3:
        - No verified key => unverified
        - verified by non-human: actors => machine-confirmed
        - verified by human: actor => human-reviewed
        """
        if not self.verified:
            return "unverified"
        for v in self.verified:
            if v.by.startswith("human:"):
                return "human-reviewed"
        return "machine-confirmed"

    @property
    def is_stale(self) -> bool:
        """Checks if current time >= stale_after."""
        if not self.stale_after:
            return False
        try:
            stale_dt = datetime.datetime.fromisoformat(self.stale_after.replace("Z", "+00:00"))
            now_dt = datetime.datetime.now(datetime.timezone.utc)
            return now_dt >= stale_dt
        except Exception:
            return False

    def to_okf_markdown(self) -> str:
        """
        Serializes this concept into a strict OKF v0.2 Markdown file with YAML Frontmatter.
        Uses standard JSON/YAML escaping for string values to prevent broken frontmatter syntax.
        """
        lines = ["---"]
        lines.append(f"type: {self.type}")
        if self.title:
            lines.append(f"title: {json.dumps(self.title, ensure_ascii=False)}")
        if self.description:
            lines.append(f"description: {json.dumps(self.description, ensure_ascii=False)}")
        if self.resource:
            lines.append(f"resource: {self.resource}")
        if self.tags:
            lines.append(f"tags: [{', '.join(self.tags)}]")
        if self.created_at:
            lines.append(f"created_at: {self.created_at}")
        if self.updated_at:
            lines.append(f"updated_at: {self.updated_at}")
        if self.status and self.status != "stable":
            lines.append(f"status: {self.status}")
        if self.stale_after:
            lines.append(f"stale_after: {self.stale_after}")

        # Generated
        if self.generated:
            lines.append(f"generated: {{ by: {self.generated.by}, at: {self.generated.at} }}")

        # Verified
        if len(self.verified) == 1:
            lines.append(f"verified: {{ by: {self.verified[0].by}, at: {self.verified[0].at} }}")
        elif len(self.verified) > 1:
            lines.append("verified:")
            for v in self.verified:
                lines.append(f"  - {{ by: {v.by}, at: {v.at} }}")

        # Sources (Provenance)
        if self.sources:
            lines.append("sources:")
            for s in self.sources:
                lines.append(f"  - resource: {s.resource}")
                if s.id:
                    lines.append(f"    id: {s.id}")
                if s.title:
                    lines.append(f"    title: {json.dumps(s.title, ensure_ascii=False)}")
                if s.author:
                    lines.append(f"    author: {s.author}")
                if s.last_modified:
                    lines.append(f"    last_modified: {s.last_modified}")
                if s.usage_count is not None:
                    lines.append(f"    usage_count: {s.usage_count}")

        if self.usage_window:
            lines.append(f"usage_window: {{ from: {self.usage_window.get('from', '')}, to: {self.usage_window.get('to', '')} }}")

        # Permissions Extension
        if self.permissions:
            lines.append("permissions:")
            lines.append(f"  is_public: {str(self.permissions.is_public).lower()}")
            lines.append(f"  allowed_roles: {json.dumps(self.permissions.allowed_roles)}")
            if self.permissions.allowed_users:
                lines.append(f"  allowed_users: {json.dumps(self.permissions.allowed_users)}")

        if self.extra_metadata:
            lines.append("extra_metadata:")
            for k, v in sorted(self.extra_metadata.items()):
                lines.append(f"  {k}: {json.dumps(v, ensure_ascii=False)}")

        if self.content_hash:
            lines.append(f"content_hash: {self.content_hash}")

        lines.append("---")
        lines.append("")
        lines.append(self.body.strip())
        lines.append("")

        # Append Footnote Citations for sources
        if self.sources:
            for s in self.sources:
                if s.id and s.title:
                    lines.append(f"[^{s.id}]: {s.title} ({s.resource})")
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serializes concept to JSON dictionary for Vector DB & APIs."""
        return {
            "type": self.type,
            "title": self.title,
            "description": self.description,
            "resource": self.resource,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "trust_tier": self.trust_tier,
            "is_stale": self.is_stale,
            "generated": self.generated.to_dict() if self.generated else None,
            "verified": [v.to_dict() for v in self.verified],
            "sources": [s.to_dict() for s in self.sources],
            "permissions": self.permissions.to_dict(),
            "extra_metadata": self.extra_metadata,
            "content_hash": self.content_hash,
            "body": self.body,
            "structured_data": self.structured_data,
        }

    @classmethod
    def from_intermediate_document(
        cls,
        doc: Document,
        concept_type: str = "Document",
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        author: str = "notion_connector/v1.0",
        permissions: Optional[OKFPermissions] = None,
    ) -> OKFConcept:
        """
        Converts our intermediate Document into a standardized OKF v0.2 Concept.
        """
        body_markdown = doc.to_markdown().strip()
        content_hash = hashlib.sha256(body_markdown.encode("utf-8")).hexdigest()

        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        created_at = doc.metadata.created_time or now_iso
        updated_at = doc.metadata.last_edited_time or now_iso

        # Build Provenance Source entry
        platform = doc.metadata.source_platform or "unknown"
        source_id = f"{platform}-{doc.metadata.id[:8]}" if doc.metadata.id else f"{platform}-source"
        source_entry = OKFSource(
            id=source_id,
            resource=doc.metadata.url or f"{platform}://resources/{doc.metadata.id}",
            title=f"{platform.capitalize()}: {doc.metadata.title}",
            author=f"process:{platform}",
            last_modified=updated_at,
        )

        # Automatic summary description if none provided
        auto_desc = description
        if not auto_desc:
            first_text = doc.to_plain_text()
            if first_text:
                first_line = first_text.splitlines()[0].strip()
                # Clean up quotes if string starts and ends with them
                if (first_line.startswith('"') and first_line.endswith('"')) or \
                   (first_line.startswith("'") and first_line.endswith("'")):
                    first_line = first_line[1:-1].strip()
                auto_desc = first_line[:120] + ("..." if len(first_line) > 120 else "")
            else:
                auto_desc = f"{doc.metadata.title} knowledge document from {platform.capitalize()}."

        # Collect structured tabular data (charts/databases)
        structured_data = []
        for block in doc.blocks:
            if block.type in (BlockType.DATABASE, BlockType.CHILD_DATABASE, BlockType.TABLE):
                structured_data.append({
                    "block_id": block.id,
                    "title": block.text.splitlines()[0] if block.text else "Database",
                    "columns": block.columns,
                    "rows": block.rows,
                    "total_rows": len(block.rows),
                })

        # Infer default tags
        inferred_tags = list(tags) if tags else []
        if doc.metadata.source_platform and doc.metadata.source_platform not in inferred_tags:
            inferred_tags.append(doc.metadata.source_platform)

        # Carry forward connector-specific metadata
        extra_metadata = dict(doc.metadata.extra) if doc.metadata.extra else {}

        return cls(
            type=concept_type,
            title=doc.metadata.title or "Untitled",
            description=auto_desc,
            resource=doc.metadata.url or f"{platform}://resources/{doc.metadata.id}",
            tags=inferred_tags,
            created_at=created_at,
            updated_at=updated_at,
            generated=OKFActor(by=author, at=updated_at),
            verified=[OKFActor(by=f"process:{platform}-sync", at=now_iso)],
            status="stable",
            sources=[source_entry],
            permissions=permissions or OKFPermissions(),
            extra_metadata=extra_metadata,
            content_hash=content_hash,
            body=body_markdown,
            structured_data=structured_data,
        )


@dataclass
class OKFBundle:
    """
    An OKF v0.2 Knowledge Bundle (a collection of concepts with index.md and log.md).
    """
    name: str
    concepts: Dict[str, OKFConcept] = field(default_factory=dict) # path -> OKFConcept
    okf_version: str = "0.2"

    def add_concept(self, relative_path: str, concept: OKFConcept) -> None:
        """Adds a concept to the bundle at relative_path (e.g. 'architecture/overview.md')."""
        clean_path = relative_path.lstrip("/")
        if not clean_path.endswith(".md"):
            clean_path += ".md"
        self.concepts[clean_path] = concept

    def generate_index_markdown(self) -> str:
        """Generates bundle-root index.md per §8 and §12."""
        lines = [
            "---",
            f"okf_version: \"{self.okf_version}\"",
            "---",
            "",
            f"# {self.name} - Knowledge Index",
            "",
            "## Concepts",
            "",
        ]
        for path, concept in sorted(self.concepts.items()):
            title = concept.title or path.replace(".md", "")
            desc = f" - {concept.description}" if concept.description else ""
            lines.append(f"* [{title}]({path}){desc}")

        lines.append("")
        return "\n".join(lines)

    def generate_log_markdown(self) -> str:
        """Generates log.md update history per §9."""
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        lines = [
            "# Knowledge Bundle Update Log",
            "",
            f"## {today}",
        ]
        for path, concept in sorted(self.concepts.items()):
            lines.append(f"* **Ingestion**: Ingested [{concept.title or path}]({path}) from `{concept.resource}`.")

        lines.append("")
        return "\n".join(lines)
