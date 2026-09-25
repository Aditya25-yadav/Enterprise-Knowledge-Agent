"""
Neo4j Graph Client for Enterprise Knowledge Agent.

Writes a GitHubGraphBundle into a Neo4j database using idempotent MERGE
queries. Handles connection, constraint creation, batched node writes,
and batched relationship writes.

Pipeline position:
    GitHubGraphBundle  (from github_extractor.py)
           ↓
    Neo4jClient        ← THIS FILE
           ↓
    Neo4j database

Design rules:
  1. MERGE on node_id — every node has a stable namespaced ID.
     Re-running the extractor updates existing nodes (SET n += props)
     instead of creating duplicates.
  2. Batch writes via UNWIND — sends one Cypher query per node-label type
     with a list of records, instead of one query per node.
  3. Group by type — since Cypher does not support dynamic labels or
     relationship types, nodes are grouped by NodeLabel and relationships
     are grouped by RelType. One UNWIND query is executed per group.
  4. MATCH for relationships — relationship MERGE uses MATCH (not MERGE)
     for both endpoints. If either endpoint does not exist (e.g. a CLOSES
     edge pointing to an issue not in the bundle), the row is silently
     skipped by Neo4j.
  5. Neo4j-safe properties — list[str] is supported natively. Nested
     dicts are not; they are JSON-stringified before writing.

Environment variables required (.env):
    NEO4J_URI       bolt://localhost:7687   (or neo4j://, neo4j+s://)
    NEO4J_USERNAME  neo4j
    NEO4J_PASSWORD  your_password
    NEO4J_DATABASE  neo4j                  (optional, default "neo4j")
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── sys.path bootstrap ────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# ─────────────────────────────────────────────────────────────────────────────

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ServiceUnavailable, AuthError
except ImportError:
    GraphDatabase = None
    ServiceUnavailable = Exception
    AuthError = Exception

from backend.models.graph import (
    GitHubGraphBundle,
    GraphNode,
    GraphRelationship,
    NodeLabel,
    RelType,
)

# Batch size for UNWIND queries — tune for performance vs memory
_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Cypher templates — one per NodeLabel (dynamic labels need separate queries)
# ---------------------------------------------------------------------------

# MERGE on node_id, then SET all properties.
# Using SET n += props (map merge) so only provided keys are updated;
# existing keys not in the new payload are preserved.
_NODE_MERGE = {
    label: f"""
        UNWIND $batch AS row
        MERGE (n:{label} {{node_id: row.node_id}})
        SET n += row.properties
    """
    for label in [
        NodeLabel.REPOSITORY,
        NodeLabel.FILE,
        NodeLabel.USER,
        NodeLabel.TEAM,
        NodeLabel.ISSUE,
        NodeLabel.PULL_REQUEST,
        NodeLabel.COMMIT,
        NodeLabel.LABEL,
    ]
}

# MERGE the relationship after MATCHing both endpoints.
# If either endpoint is missing, the row is silently skipped.
_REL_MERGE = {
    rel_type: f"""
        UNWIND $batch AS row
        MATCH (from {{node_id: row.from_id}})
        MATCH (to   {{node_id: row.to_id}})
        MERGE (from)-[r:{rel_type}]->(to)
        SET r += row.properties
    """
    for rel_type in [
        RelType.CONTAINS,
        RelType.OWNED_BY,
        RelType.AUTHORED,
        RelType.CREATED,
        RelType.REVIEWED,
        RelType.ASSIGNED_TO,
        RelType.MEMBER_OF,
        RelType.HAS_ACCESS_TO,
        RelType.MODIFIES,
        RelType.CLOSES,
        RelType.PART_OF,
        RelType.TAGGED_WITH,
    ]
}

# Uniqueness constraints — one per label on node_id
_CONSTRAINTS = [
    (label, f"github_{label.lower()}_node_id_unique")
    for label in [
        "Repository", "File", "User", "Team",
        "Issue", "PullRequest", "Commit", "Label",
    ]
]


# ---------------------------------------------------------------------------
# Neo4j Client
# ---------------------------------------------------------------------------

class Neo4jClient:
    """
    Wraps the Neo4j Python driver with graph write operations for
    the GitHub knowledge graph.

    Usage:
        client = Neo4jClient()          # reads from env
        client.test_connection()        # True / False
        client.create_constraints()     # run once on first setup
        client.write_bundle(bundle)     # write all nodes + edges
        client.close()
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ) -> None:
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "")
        self.database = database or os.getenv("NEO4J_DATABASE", "neo4j")
        self.last_error: Optional[str] = None

        if GraphDatabase is None:
            raise ImportError(
                "The 'neo4j' package is required to connect to Neo4j. Install it via 'pip install neo4j'."
            )

        if not self.password:
            raise ValueError(
                "Neo4j password must be provided or set in NEO4J_PASSWORD env var."
            )

        self.driver = GraphDatabase.driver(
            self.uri,
            auth=(self.user, self.password),
        )

    def close(self) -> None:
        """Close the driver connection pool."""
        self.driver.close()

    def __enter__(self) -> "Neo4jClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── Connection ────────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """
        Checks that the driver can reach Neo4j and authenticate.
        Returns True on success, False on any failure.
        """
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run("RETURN 1 AS ok")
                return result.single()["ok"] == 1
        except Exception as e:
            self.last_error = str(e)
            return False

    def get_server_info(self) -> Dict[str, Any]:
        """Returns Neo4j server version and edition info."""
        try:
            with self.driver.session(database=self.database) as session:
                result = session.run(
                    "CALL dbms.components() YIELD name, versions, edition "
                    "RETURN name, versions, edition LIMIT 1"
                )
                row = result.single()
                if row:
                    return {
                        "name": row["name"],
                        "version": row["versions"][0] if row["versions"] else "unknown",
                        "edition": row["edition"],
                    }
        except Exception:
            pass
        return {}

    # ── Schema Setup ─────────────────────────────────────────────────────────

    def create_constraints(self) -> None:
        """
        Creates uniqueness constraints on node_id for every NodeLabel.
        Safe to run multiple times — uses IF NOT EXISTS.
        Run this once when setting up the database.
        """
        with self.driver.session(database=self.database) as session:
            for label, name in _CONSTRAINTS:
                cypher = (
                    f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.node_id IS UNIQUE"
                )
                session.run(cypher)
                print(f"    ✅ Constraint: {label}.node_id IS UNIQUE")

    def create_indexes(self) -> None:
        """
        Creates additional lookup indexes beyond the node_id constraint.
        Useful for queries that filter by login, path, sha, state, etc.
        """
        indexes = [
            ("User",        "login",       "github_user_login_idx"),
            ("File",        "path",        "github_file_path_idx"),
            ("Commit",      "sha",         "github_commit_sha_idx"),
            ("Issue",       "number",      "github_issue_number_idx"),
            ("PullRequest", "number",      "github_pr_number_idx"),
            ("Repository",  "full_name",   "github_repo_fullname_idx"),
        ]
        with self.driver.session(database=self.database) as session:
            for label, prop, idx_name in indexes:
                cypher = (
                    f"CREATE INDEX {idx_name} IF NOT EXISTS "
                    f"FOR (n:{label}) ON (n.{prop})"
                )
                session.run(cypher)
                print(f"    ✅ Index: {label}.{prop}")

    # ── Write Bundle ─────────────────────────────────────────────────────────

    def write_bundle(self, bundle: GitHubGraphBundle) -> Dict[str, int]:
        """
        Writes an entire GitHubGraphBundle into Neo4j.
        Uses batched MERGE queries — idempotent, safe to re-run.

        Returns a summary dict with counts of nodes and relationships written.
        """
        summary = bundle.summary()
        print(f"\n  📥 Writing bundle to Neo4j ({self.uri} / {self.database})")

        all_nodes = bundle.all_nodes()
        all_rels = bundle.relationships

        with self.driver.session(database=self.database) as session:
            nodes_written = self._write_nodes(session, all_nodes)
            rels_written = self._write_relationships(session, all_rels)

        print(f"  ✅ Nodes written:         {nodes_written}")
        print(f"  ✅ Relationships written: {rels_written}")
        return {"nodes": nodes_written, "relationships": rels_written}

    # ── Internal: Node writes ─────────────────────────────────────────────────

    def _write_nodes(self, session: Any, nodes: List[GraphNode]) -> int:
        """
        Groups nodes by label, then sends one batched UNWIND query per label.
        """
        # Group nodes by label
        by_label: Dict[str, List[Dict]] = defaultdict(list)
        for node in nodes:
            label = node.label if isinstance(node.label, str) else node.label.value
            safe_props = _sanitize_properties(node.properties)
            by_label[label].append({
                "node_id": node.node_id,
                "properties": safe_props,
            })

        total = 0
        for label, rows in by_label.items():
            query = _NODE_MERGE.get(label)
            if not query:
                print(f"    ⚠️  No MERGE template for label '{label}' — skipping.")
                continue
            # Send in batches of _BATCH_SIZE
            for batch in _chunks(rows, _BATCH_SIZE):
                session.run(query, batch=batch)
                total += len(batch)
            print(f"    → {label:<15} {len(rows):>5} node(s) merged")

        return total

    # ── Internal: Relationship writes ─────────────────────────────────────────

    def _write_relationships(self, session: Any, rels: List[GraphRelationship]) -> int:
        """
        Groups relationships by type, then sends one batched UNWIND query per type.
        Rows whose endpoints don't exist are silently skipped by Neo4j MATCH.
        """
        by_type: Dict[str, List[Dict]] = defaultdict(list)
        for rel in rels:
            rel_type = rel.rel_type if isinstance(rel.rel_type, str) else rel.rel_type.value
            safe_props = _sanitize_properties(rel.properties)
            by_type[rel_type].append({
                "from_id": rel.from_id,
                "to_id": rel.to_id,
                "properties": safe_props,
            })

        total = 0
        for rel_type, rows in by_type.items():
            query = _REL_MERGE.get(rel_type)
            if not query:
                print(f"    ⚠️  No MERGE template for rel type '{rel_type}' — skipping.")
                continue
            for batch in _chunks(rows, _BATCH_SIZE):
                session.run(query, batch=batch)
                total += len(batch)
            print(f"    → {rel_type:<25} {len(rows):>5} edge(s) merged")

        return total

    # ── Query helpers (for testing / Graph RAG) ───────────────────────────────

    def run_query(self, cypher: str, params: Optional[Dict] = None) -> List[Dict]:
        """
        Runs a raw Cypher query and returns results as a list of dicts.
        Use for Graph RAG retrieval or debugging.
        """
        with self.driver.session(database=self.database) as session:
            result = session.run(cypher, params or {})
            return [dict(record) for record in result]

    def get_node_count(self) -> int:
        """Returns total node count in the database."""
        rows = self.run_query("MATCH (n) RETURN count(n) AS total")
        return rows[0]["total"] if rows else 0

    def get_rel_count(self) -> int:
        """Returns total relationship count in the database."""
        rows = self.run_query("MATCH ()-[r]->() RETURN count(r) AS total")
        return rows[0]["total"] if rows else 0

    def get_schema_summary(self) -> Dict[str, Any]:
        """Returns node label counts and relationship type counts."""
        label_counts = self.run_query(
            "MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count "
            "ORDER BY count DESC"
        )
        rel_counts = self.run_query(
            "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(r) AS count "
            "ORDER BY count DESC"
        )
        return {
            "nodes": {row["label"]: row["count"] for row in label_counts},
            "relationships": {row["rel_type"]: row["count"] for row in rel_counts},
        }

    def clear_github_data(self, confirm: bool = False) -> None:
        """
        Deletes ALL nodes and relationships in the database.
        Requires confirm=True as a safety guard.
        """
        if not confirm:
            raise ValueError(
                "Pass confirm=True to clear_github_data(). "
                "This deletes everything in the database."
            )
        with self.driver.session(database=self.database) as session:
            session.run("MATCH (n) DETACH DELETE n")
        print("🗑️  All nodes and relationships deleted.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_properties(props: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converts a properties dict to Neo4j-safe types.

    Neo4j supports: str, int, float, bool, List[str|int|float|bool].
    It does NOT support: nested dicts, None values (they are dropped),
    mixed-type lists.

    Rules applied:
      - None values are dropped.
      - List[primitive] stays as-is.
      - Nested dicts are JSON-stringified.
      - Anything else is converted to str.
    """
    clean: Dict[str, Any] = {}
    for key, val in props.items():
        if val is None:
            continue
        if isinstance(val, (str, int, float, bool)):
            clean[key] = val
        elif isinstance(val, list):
            # Keep as list only if all elements are primitive
            if all(isinstance(v, (str, int, float, bool)) for v in val):
                clean[key] = val
            else:
                clean[key] = json.dumps(val, ensure_ascii=False)
        elif isinstance(val, dict):
            clean[key] = json.dumps(val, ensure_ascii=False)
        else:
            clean[key] = str(val)
    return clean


def _chunks(lst: list, size: int):
    """Yields successive sub-lists of length `size`."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ---------------------------------------------------------------------------
# Entry point — run directly:
#   python3 backend/graph/neo4j_client.py --repo owner/name
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv())

    from backend.connectors.github.client import GitHubClient
    from backend.graph.github_extractor import GitHubGraphExtractor

    parser = argparse.ArgumentParser(
        description="Extract GitHub graph and write it to Neo4j."
    )
    parser.add_argument("--repo", required=False, default=None,
                        help="'owner/repo' to extract. Auto-discovers if omitted.")
    parser.add_argument("--max-files",   type=int, default=500)
    parser.add_argument("--max-commits", type=int, default=50)
    parser.add_argument("--setup-only",  action="store_true",
                        help="Only create constraints/indexes, don't ingest.")
    parser.add_argument("--clear",       action="store_true",
                        help="Clear all data before ingesting (⚠️  destructive).")
    parser.add_argument("--query",       action="store_true",
                        help="After ingestion, print schema summary.")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("🚀 ENTERPRISE KNOWLEDGE AGENT — NEO4J GRAPH WRITER")
    print("="*60)

    # ── Connect ────────────────────────────────────────────────────────────
    try:
        neo = Neo4jClient()
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)

    print(f"📡 Connecting to Neo4j at {neo.uri} ...")
    if not neo.test_connection():
        print("❌ Connection failed. Check NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD in .env")
        neo.close()
        sys.exit(1)

    info = neo.get_server_info()
    print(f"✅ Connected! Neo4j {info.get('version')} {info.get('edition')} edition")

    # ── Schema setup ───────────────────────────────────────────────────────
    print("\n📐 Creating constraints and indexes...")
    neo.create_constraints()
    neo.create_indexes()

    if args.setup_only:
        print("\n✅ Schema setup complete (--setup-only flag, stopping here).")
        neo.close()
        sys.exit(0)

    # ── Optionally clear ───────────────────────────────────────────────────
    if args.clear:
        print("\n⚠️  Clearing all existing data...")
        neo.clear_github_data(confirm=True)

    # ── Extract bundle ─────────────────────────────────────────────────────
    gh_token = os.getenv("GITHUB_TOKEN")
    if not gh_token:
        print("❌ GITHUB_TOKEN not set in .env")
        neo.close()
        sys.exit(1)

    gh_client = GitHubClient(token=gh_token)

    target = args.repo
    if not target:
        print("\nNo --repo provided. Auto-discovering first accessible repo...")
        repos = gh_client.list_repos()
        if not repos:
            print("❌ No accessible repositories found.")
            neo.close()
            sys.exit(1)
        target = repos[0].get("full_name")
        print(f"Auto-selected: {target}")

    owner_str, _, repo_str = target.partition("/")
    extractor = GitHubGraphExtractor(gh_client)
    bundle = extractor.extract(
        owner=owner_str,
        repo=repo_str,
        max_files=args.max_files,
        max_commits=args.max_commits,
    )

    # ── Write to Neo4j ─────────────────────────────────────────────────────
    print()
    neo.write_bundle(bundle)

    # ── Schema summary ─────────────────────────────────────────────────────
    if args.query:
        print("\n📊 Neo4j Schema Summary After Ingestion:")
        schema = neo.get_schema_summary()
        print("  NODES:")
        for label, count in schema["nodes"].items():
            print(f"    {label:<20} {count:>6}")
        print("  RELATIONSHIPS:")
        for rel_type, count in schema["relationships"].items():
            print(f"    {rel_type:<25} {count:>6}")

    print("\n" + "="*60)
    print(f"🎉 Done! Total in DB: {neo.get_node_count()} nodes, {neo.get_rel_count()} relationships")
    print("="*60 + "\n")

    neo.close()
