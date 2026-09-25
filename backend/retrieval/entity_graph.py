"""
Generalized Entity Knowledge Graph Retrieval Layer for Enterprise Knowledge Agent.

Provides a generalized property-graph engine for developer entities, code intelligence,
and cross-system relationship traversal across GitHub, Jira, Notion, and Slack:

Generalized Graph Operations:
  1. `get_entity`: Direct lookup of any entity node by ID or alias.
  2. `get_neighbors`: Multi-hop relationship expansion in any direction (IN/OUT/BOTH) filtered by rel_types and labels.
  3. `search_nodes`: Search entities by label, properties, or text keywords.
  4. `find_path`: Shortest path discovery between any two graph entities.
  5. `raw_cypher`: Parameterized read-only Cypher query execution against Neo4j.

Domain Intelligence Shortcuts (GitHub & Beyond):
  - `get_pr_details`: Full PR metadata, author, reviewers, assignees, modified files, closed issues.
  - `get_user_activity`: Developer 360 view (PRs authored, commits, reviews, assigned issues).
  - `get_file_contributors`: Code ownership, commit history, and PRs touching a file.
  - `get_commit_details`: Commit author, message, touched files, parent PR merge links.
  - `get_issue_details`: Issue state, reporter, assignees, labels, and fixing PRs.
  - `get_labeled_items`: Issues and PRs tagged with specific labels/topics.
  - `get_team_overview`: Team members, repository access, and ownership.
  - `get_repo_overview`: Maintainers, open PRs, issues, topics, and repository health.

Dual Mode Execution:
  - Live Neo4j instance when configured (`NEO4J_URI`, `NEO4J_PASSWORD`).
  - In-Memory Generalized Graph fallback (`InMemoryEntityGraph`) for zero-dependency offline execution.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.graph.neo4j_client import Neo4jClient
from backend.models.graph import (
    GitHubGraphBundle,
    GraphNode,
    GraphRelationship,
    NodeLabel,
    RelType,
)

logger = logging.getLogger(__name__)


class InMemoryEntityGraph:
    """
    Generalized, high-performance in-memory property graph store.
    Supports node indexing, directional edge indexing, multi-hop BFS traversals,
    path finding, and specialized domain filters.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, GraphNode] = {}
        self.relationships: List[GraphRelationship] = []
        # Adjacency indexes for O(1) edge lookups
        self._out_edges: Dict[str, List[GraphRelationship]] = defaultdict(list)
        self._in_edges: Dict[str, List[GraphRelationship]] = defaultdict(list)

    def clear(self) -> None:
        self.nodes.clear()
        self.relationships.clear()
        self._out_edges.clear()
        self._in_edges.clear()

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.node_id] = node

    def add_relationship(self, rel: GraphRelationship) -> None:
        self.relationships.append(rel)
        self._out_edges[rel.from_id].append(rel)
        self._in_edges[rel.to_id].append(rel)

    def load_bundle(self, bundle: GitHubGraphBundle) -> None:
        """Loads all nodes and relationships from a GitHubGraphBundle."""
        for node in bundle.all_nodes():
            self.add_node(node)
        for rel in bundle.all_relationships():
            self.add_relationship(rel)

    # ── Generalized Graph Primitives ──────────────────────────────────────────

    def get_entity(self, node_id_or_alias: str) -> Optional[Dict[str, Any]]:
        """Fetches any entity node by exact node_id or matching unique suffix/key."""
        if node_id_or_alias in self.nodes:
            n = self.nodes[node_id_or_alias]
            return {"label": n.label, "node_id": n.node_id, **n.properties}

        # Search by suffix/key matching (e.g. "PAY-928" or "142" or "pr#142" or "alice")
        clean = node_id_or_alias.strip()
        clean_num = re.sub(r"^(pr|pull|issue|#|\s)+", "", clean, flags=re.IGNORECASE).strip().lstrip("#")
        for n in self.nodes.values():
            n_num = str(n.properties.get("number", ""))
            n_login = str(n.properties.get("login", ""))
            n_sha = str(n.properties.get("sha", ""))
            if (
                n.node_id == clean
                or n.node_id.endswith(f":{clean}")
                or (clean_num and n.node_id.endswith(f":{clean_num}"))
                or (clean_num and n_num == clean_num)
                or n_login.lower() == clean.lower()
                or n_sha == clean
            ):
                return {"label": n.label, "node_id": n.node_id, **n.properties}
        return None

    def search_nodes(
        self,
        label: Optional[str] = None,
        property_filters: Optional[Dict[str, Any]] = None,
        text_query: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Generalized search across entity nodes by label, properties, or text keywords."""
        results: List[Dict[str, Any]] = []
        q_lower = text_query.lower() if text_query else None

        for n in self.nodes.values():
            if label and n.label.lower() != label.lower():
                continue

            # Property filter matches
            if property_filters:
                match = True
                for k, v in property_filters.items():
                    if n.properties.get(k) != v:
                        match = False
                        break
                if not match:
                    continue

            # Text keyword match across node_id and string properties
            if q_lower:
                text_match = (
                    q_lower in n.node_id.lower()
                    or any(isinstance(val, str) and q_lower in val.lower() for val in n.properties.values())
                )
                if not text_match:
                    continue

            results.append({"label": n.label, "node_id": n.node_id, **n.properties})
            if len(results) >= limit:
                break

        return results

    def get_neighbors(
        self,
        node_id: str,
        direction: str = "both",
        rel_types: Optional[List[str]] = None,
        target_label: Optional[str] = None,
        max_depth: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Generalized multi-hop BFS neighbor expansion in any direction with edge and label filters.
        """
        center = self.get_entity(node_id)
        if not center:
            return []

        start_id = center["node_id"]
        rel_types_set = set(r.upper() for r in rel_types) if rel_types else None
        target_label_lower = target_label.lower() if target_label else None

        visited: Set[str] = {start_id}
        queue: deque[Tuple[str, int]] = deque([(start_id, 0)])
        neighbors: List[Dict[str, Any]] = []

        while queue:
            curr_id, depth = queue.popleft()
            if depth >= max_depth:
                continue

            candidate_edges: List[Tuple[GraphRelationship, str]] = []
            if direction in ("out", "both"):
                for rel in self._out_edges.get(curr_id, []):
                    candidate_edges.append((rel, rel.to_id))
            if direction in ("in", "both"):
                for rel in self._in_edges.get(curr_id, []):
                    candidate_edges.append((rel, rel.from_id))

            for rel, next_id in candidate_edges:
                rel_type_str = rel.rel_type if isinstance(rel.rel_type, str) else rel.rel_type.value
                if rel_types_set and rel_type_str.upper() not in rel_types_set:
                    continue

                if next_id not in visited and next_id in self.nodes:
                    visited.add(next_id)
                    neighbor_node = self.nodes[next_id]
                    if not target_label_lower or neighbor_node.label.lower() == target_label_lower:
                        neighbors.append({
                            "relationship": rel_type_str,
                            "direction": "OUT" if rel.from_id == curr_id else "IN",
                            "hop_depth": depth + 1,
                            "label": neighbor_node.label,
                            "node_id": neighbor_node.node_id,
                            **neighbor_node.properties,
                        })
                    queue.append((next_id, depth + 1))

        return neighbors

    def find_path(
        self,
        start_id: str,
        end_id: str,
        max_depth: int = 3,
    ) -> Optional[List[Dict[str, Any]]]:
        """Finds shortest relationship path between two entities using BFS."""
        start_entity = self.get_entity(start_id)
        end_entity = self.get_entity(end_id)
        if not start_entity or not end_entity:
            return None

        src = start_entity["node_id"]
        dst = end_entity["node_id"]
        if src == dst:
            return [{"node_id": src, "label": start_entity["label"]}]

        visited: Set[str] = {src}
        queue: deque[Tuple[str, List[Dict[str, Any]]]] = deque([(src, [{"node_id": src, "label": start_entity["label"]}])])

        while queue:
            curr_id, path = queue.popleft()
            if len(path) > max_depth + 1:
                continue

            for rel in self._out_edges.get(curr_id, []):
                next_id = rel.to_id
                if next_id == dst:
                    next_node = self.nodes[next_id]
                    return path + [{"rel": rel.rel_type, "node_id": next_id, "label": next_node.label}]
                if next_id not in visited and next_id in self.nodes:
                    visited.add(next_id)
                    next_node = self.nodes[next_id]
                    queue.append((next_id, path + [{"rel": rel.rel_type, "node_id": next_id, "label": next_node.label}]))

            for rel in self._in_edges.get(curr_id, []):
                next_id = rel.from_id
                if next_id == dst:
                    next_node = self.nodes[next_id]
                    return path + [{"rel": f"INVERSE_{rel.rel_type}", "node_id": next_id, "label": next_node.label}]
                if next_id not in visited and next_id in self.nodes:
                    visited.add(next_id)
                    next_node = self.nodes[next_id]
                    queue.append((next_id, path + [{"rel": f"INVERSE_{rel.rel_type}", "node_id": next_id, "label": next_node.label}]))

        return None

    # ── Domain-Specific Shortcuts ─────────────────────────────────────────────

    def get_pr_details(self, pr_identifier: str | int) -> Optional[Dict[str, Any]]:
        """Finds PR and resolves author, reviewers, assignees, modified files, and closed issues."""
        pr = self.get_entity(str(pr_identifier))
        if not pr or pr.get("label") not in ("PullRequest", NodeLabel.PULL_REQUEST.value):
            return None

        pr_node_id = pr["node_id"]
        author = pr.get("author_login")
        reviewers = set(pr.get("reviewer_logins") or [])
        assignees = set(pr.get("assignee_logins") or [])
        modified_files = []
        closed_issues = []

        # Outgoing edges from PR
        for rel in self._out_edges.get(pr_node_id, []):
            if rel.rel_type in ("MODIFIES", RelType.MODIFIES.value):
                f = self.nodes.get(rel.to_id)
                if f:
                    modified_files.append(f.properties.get("path") or f.node_id)
            elif rel.rel_type in ("CLOSES", RelType.CLOSES.value):
                i = self.nodes.get(rel.to_id)
                if i:
                    closed_issues.append({
                        "number": i.properties.get("number"),
                        "title": i.properties.get("title"),
                        "state": i.properties.get("state"),
                    })

        # Incoming edges to PR
        for rel in self._in_edges.get(pr_node_id, []):
            u = self.nodes.get(rel.from_id)
            if u:
                u_login = u.properties.get("login")
                if rel.rel_type in ("CREATED", RelType.CREATED.value):
                    author = u_login
                elif rel.rel_type in ("REVIEWED", RelType.REVIEWED.value):
                    reviewers.add(u_login)
                elif rel.rel_type in ("ASSIGNED_TO", RelType.ASSIGNED_TO.value):
                    assignees.add(u_login)

        res = dict(pr)
        res["author"] = author
        res["reviewers"] = sorted(list(reviewers))
        res["assignees"] = sorted(list(assignees))
        res["modified_files"] = modified_files
        res["closed_issues"] = closed_issues
        return res

    def get_user_activity(self, username: str) -> Dict[str, Any]:
        """Resolves 360-degree activity for a developer: authored PRs, commits, reviews, issues."""
        user = self.get_entity(username)
        u_login = user.get("login", username) if user else username
        u_id = user["node_id"] if user else f"github:user:{u_login}"

        authored_prs = []
        reviewed_prs = []
        authored_commits = []
        assigned_issues = []

        for rel in self._out_edges.get(u_id, []):
            target = self.nodes.get(rel.to_id)
            if not target:
                continue
            if rel.rel_type in ("CREATED", RelType.CREATED.value) and target.label in ("PullRequest", NodeLabel.PULL_REQUEST.value):
                authored_prs.append({"number": target.properties.get("number"), "title": target.properties.get("title"), "state": target.properties.get("state")})
            elif rel.rel_type in ("REVIEWED", RelType.REVIEWED.value):
                reviewed_prs.append({"number": target.properties.get("number"), "title": target.properties.get("title")})
            elif rel.rel_type in ("AUTHORED", RelType.AUTHORED.value):
                authored_commits.append({"sha": target.properties.get("sha"), "message": target.properties.get("message")})
            elif rel.rel_type in ("ASSIGNED_TO", RelType.ASSIGNED_TO.value):
                assigned_issues.append({"number": target.properties.get("number"), "title": target.properties.get("title")})

        return {
            "username": u_login,
            "authored_prs_count": len(authored_prs),
            "authored_prs": authored_prs,
            "reviewed_prs_count": len(reviewed_prs),
            "reviewed_prs": reviewed_prs,
            "authored_commits_count": len(authored_commits),
            "authored_commits": authored_commits,
            "assigned_issues_count": len(assigned_issues),
            "assigned_issues": assigned_issues,
        }

    def get_file_contributors(self, file_path: str) -> Dict[str, Any]:
        """Finds authors, commits, and PRs that modified a specific file."""
        target_path = file_path.strip().lstrip("/")
        matching_file_ids: List[str] = []

        for n in self.nodes.values():
            if n.label in ("File", NodeLabel.FILE.value):
                path = n.properties.get("path", "")
                if target_path in path or n.node_id.endswith(f":{target_path}"):
                    matching_file_ids.append(n.node_id)

        commits = []
        authors = set()
        pull_requests = []

        for f_id in matching_file_ids:
            for rel in self._in_edges.get(f_id, []):
                source_node = self.nodes.get(rel.from_id)
                if not source_node:
                    continue

                # Commits modifying file
                if source_node.label in ("Commit", NodeLabel.COMMIT.value):
                    c_props = dict(source_node.properties)
                    for c_rel in self._in_edges.get(source_node.node_id, []):
                        if c_rel.rel_type in ("AUTHORED", RelType.AUTHORED.value):
                            u = self.nodes.get(c_rel.from_id)
                            if u:
                                c_props["author"] = u.properties.get("login")
                                authors.add(u.properties.get("login"))
                    commits.append(c_props)

                # PRs modifying file
                elif source_node.label in ("PullRequest", NodeLabel.PULL_REQUEST.value):
                    pull_requests.append({
                        "number": source_node.properties.get("number"),
                        "title": source_node.properties.get("title"),
                        "state": source_node.properties.get("state"),
                        "author": source_node.properties.get("author_login"),
                    })

        return {
            "file_path": file_path,
            "matched_files_count": len(matching_file_ids),
            "authors": sorted(list(authors)),
            "commits": commits,
            "pull_requests": pull_requests,
        }

    def get_commit_details(self, commit_sha_or_id: str) -> Optional[Dict[str, Any]]:
        """Resolves commit author, message, modified files, and associated PR."""
        commit = self.get_entity(commit_sha_or_id)
        if not commit or commit.get("label") not in ("Commit", NodeLabel.COMMIT.value):
            return None

        c_id = commit["node_id"]
        author = None
        modified_files = []
        parent_pr = None

        for rel in self._in_edges.get(c_id, []):
            if rel.rel_type in ("AUTHORED", RelType.AUTHORED.value):
                u = self.nodes.get(rel.from_id)
                if u:
                    author = u.properties.get("login")

        for rel in self._out_edges.get(c_id, []):
            if rel.rel_type in ("MODIFIES", RelType.MODIFIES.value):
                f = self.nodes.get(rel.to_id)
                if f:
                    modified_files.append(f.properties.get("path") or f.node_id)
            elif rel.rel_type in ("PART_OF", RelType.PART_OF.value):
                pr = self.nodes.get(rel.to_id)
                if pr:
                    parent_pr = {"number": pr.properties.get("number"), "title": pr.properties.get("title")}

        res = dict(commit)
        res["author"] = author
        res["modified_files"] = modified_files
        res["parent_pr"] = parent_pr
        return res

    def get_issue_details(self, issue_identifier: str | int) -> Optional[Dict[str, Any]]:
        """Resolves issue metadata, reporter, assignees, labels, and closing PRs."""
        issue = self.get_entity(str(issue_identifier))
        if not issue or issue.get("label") not in ("Issue", NodeLabel.ISSUE.value):
            return None

        issue_id = issue["node_id"]
        author = issue.get("author_login")
        assignees = set(issue.get("assignee_logins") or [])
        labels = set(issue.get("label_names") or [])
        closing_prs = []

        # Incoming edges (Authors, Assignees, Closing PRs)
        for rel in self._in_edges.get(issue_id, []):
            source = self.nodes.get(rel.from_id)
            if not source:
                continue
            if rel.rel_type in ("CREATED", RelType.CREATED.value):
                author = source.properties.get("login")
            elif rel.rel_type in ("ASSIGNED_TO", RelType.ASSIGNED_TO.value):
                assignees.add(source.properties.get("login"))
            elif rel.rel_type in ("CLOSES", RelType.CLOSES.value):
                closing_prs.append({
                    "number": source.properties.get("number"),
                    "title": source.properties.get("title"),
                    "state": source.properties.get("state"),
                    "author": source.properties.get("author_login"),
                })

        # Outgoing edges (Labels)
        for rel in self._out_edges.get(issue_id, []):
            if rel.rel_type in ("TAGGED_WITH", RelType.TAGGED_WITH.value):
                lbl = self.nodes.get(rel.to_id)
                if lbl:
                    labels.add(lbl.properties.get("name") or rel.to_id.split(":")[-1])

        res = dict(issue)
        res["author"] = author
        res["assignees"] = sorted(list(assignees))
        res["labels"] = sorted(list(labels))
        res["closing_prs"] = closing_prs
        return res

    def get_labeled_items(self, label_name: str) -> Dict[str, Any]:
        """Finds all PRs and issues tagged with a specific label."""
        lbl_clean = label_name.strip().lower()
        tagged_issues = []
        tagged_prs = []

        for rel in self.relationships:
            if rel.rel_type in ("TAGGED_WITH", RelType.TAGGED_WITH.value):
                if lbl_clean in rel.to_id.lower() or (rel.to_id in self.nodes and self.nodes[rel.to_id].properties.get("name", "").lower() == lbl_clean):
                    item = self.nodes.get(rel.from_id)
                    if item:
                        if item.label in ("Issue", NodeLabel.ISSUE.value):
                            tagged_issues.append({"number": item.properties.get("number"), "title": item.properties.get("title"), "state": item.properties.get("state")})
                        elif item.label in ("PullRequest", NodeLabel.PULL_REQUEST.value):
                            tagged_prs.append({"number": item.properties.get("number"), "title": item.properties.get("title"), "state": item.properties.get("state")})

        return {
            "label": label_name,
            "issues": tagged_issues,
            "pull_requests": tagged_prs,
            "total_count": len(tagged_issues) + len(tagged_prs),
        }

    def get_team_overview(self, team_slug_or_id: str) -> Dict[str, Any]:
        """Finds team members and accessible repositories."""
        team = self.get_entity(team_slug_or_id)
        t_id = team["node_id"] if team else team_slug_or_id

        members = []
        accessible_repos = []

        for rel in self._in_edges.get(t_id, []):
            if rel.rel_type in ("MEMBER_OF", RelType.MEMBER_OF.value):
                u = self.nodes.get(rel.from_id)
                if u:
                    members.append(u.properties.get("login") or rel.from_id)

        for rel in self._out_edges.get(t_id, []):
            if rel.rel_type in ("HAS_ACCESS_TO", RelType.HAS_ACCESS_TO.value):
                r = self.nodes.get(rel.to_id)
                if r:
                    accessible_repos.append(r.properties.get("full_name") or rel.to_id)

        return {
            "team": team.get("slug") if team else team_slug_or_id,
            "members": sorted(members),
            "accessible_repos": sorted(accessible_repos),
        }

    def get_repo_overview(self, repo_name: str) -> Dict[str, Any]:
        """Returns summary of a repository (files count, open issues, open PRs, maintainers)."""
        repo = self.get_entity(repo_name)
        r_id = repo["node_id"] if repo else f"github:repo:{repo_name}"

        files = []
        teams = []
        for rel in self._out_edges.get(r_id, []):
            if rel.rel_type in ("CONTAINS", RelType.CONTAINS.value):
                f = self.nodes.get(rel.to_id)
                if f:
                    files.append(f.properties.get("path"))

        for rel in self._in_edges.get(r_id, []):
            if rel.rel_type in ("HAS_ACCESS_TO", RelType.HAS_ACCESS_TO.value):
                t = self.nodes.get(rel.from_id)
                if t:
                    teams.append(t.properties.get("slug") or rel.from_id)

        return {
            "repository": repo.get("full_name") if repo else repo_name,
            "files_count": len(files),
            "teams_with_access": teams,
            "open_issues_count": repo.get("open_issues_count", 0) if repo else 0,
            "stargazers_count": repo.get("stargazers_count", 0) if repo else 0,
            "default_branch": repo.get("default_branch", "main") if repo else "main",
        }


class EntityGraphRetriever:
    """
    Unified Entity Knowledge Graph Retriever.
    Routes queries to live Neo4j Cypher or high-speed InMemoryEntityGraph.
    """

    def __init__(
        self,
        neo4j_client: Optional[Neo4jClient] = None,
        memory_graph: Optional[InMemoryEntityGraph] = None,
    ) -> None:
        self.memory_graph = memory_graph or InMemoryEntityGraph()
        self.neo4j_client: Optional[Neo4jClient] = neo4j_client
        self.mode: str = "memory"

        # Check existing client or attempt connection only if unconfigured
        if self.neo4j_client is not None:
            if self.neo4j_client.test_connection():
                self.mode = "neo4j"
            else:
                self.mode = "memory"
        elif memory_graph is not None and len(memory_graph.nodes) > 0:
            self.mode = "memory"
        else:
            pwd = os.getenv("NEO4J_PASSWORD")
            if pwd:
                try:
                    client = Neo4jClient()
                    if client.test_connection():
                        self.neo4j_client = client
                        self.mode = "neo4j"
                except Exception as e:
                    logger.warning(f"Neo4j connection failed, using in-memory graph: {e}")
                    self.mode = "memory"

    # ── Generalized Query Dispatcher ──────────────────────────────────────────

    def search(
        self,
        operation: str,
        target: str,
        parameters: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Universal dispatch entrypoint for Agent Planner and LangChain tools.
        Dispatches to high-performance native Neo4j Cypher when connected,
        or InMemoryEntityGraph when offline / in-memory.
        """
        op = operation.strip().lower()
        params = parameters or {}
        use_neo4j = (self.mode == "neo4j" and self.neo4j_client is not None)

        # ── 1. Generalized Operations ──
        if op == "get_entity":
            return self._neo4j_get_entity(target) if use_neo4j else self.memory_graph.get_entity(target)
        elif op == "get_neighbors":
            return (
                self._neo4j_get_neighbors(
                    node_id=target,
                    direction=params.get("direction", "both"),
                    rel_types=params.get("rel_types"),
                    target_label=params.get("target_label"),
                    max_depth=params.get("max_depth", 1),
                )
                if use_neo4j
                else self.memory_graph.get_neighbors(
                    node_id=target,
                    direction=params.get("direction", "both"),
                    rel_types=params.get("rel_types"),
                    target_label=params.get("target_label"),
                    max_depth=params.get("max_depth", 1),
                )
            )
        elif op == "search_nodes":
            return (
                self._neo4j_search_nodes(
                    label=params.get("label"),
                    property_filters=params.get("property_filters"),
                    text_query=target,
                    limit=params.get("limit", 20),
                )
                if use_neo4j
                else self.memory_graph.search_nodes(
                    label=params.get("label"),
                    property_filters=params.get("property_filters"),
                    text_query=target,
                    limit=params.get("limit", 20),
                )
            )
        elif op == "find_path":
            end_id = params.get("end_id") or target
            start_id = params.get("start_id", target)
            max_depth = params.get("max_depth", 3)
            return (
                self._neo4j_find_path(start_id=start_id, end_id=end_id, max_depth=max_depth)
                if use_neo4j
                else self.memory_graph.find_path(start_id=start_id, end_id=end_id, max_depth=max_depth)
            )

        # ── 2. Domain-Specific Shortcuts ──
        elif op == "get_pr_details":
            return self._neo4j_get_pr_details(pr_identifier=target) if use_neo4j else self.memory_graph.get_pr_details(pr_identifier=target)
        elif op == "get_user_activity":
            return self._neo4j_get_user_activity(username=target) if use_neo4j else self.memory_graph.get_user_activity(username=target)
        elif op == "get_file_contributors":
            return self._neo4j_get_file_contributors(file_path=target) if use_neo4j else self.memory_graph.get_file_contributors(file_path=target)
        elif op == "get_commit_details":
            return self._neo4j_get_commit_details(commit_sha_or_id=target) if use_neo4j else self.memory_graph.get_commit_details(commit_sha_or_id=target)
        elif op == "get_issue_details":
            return self._neo4j_get_issue_details(issue_identifier=target) if use_neo4j else self.memory_graph.get_issue_details(issue_identifier=target)
        elif op == "get_labeled_items":
            return self._neo4j_get_labeled_items(label_name=target) if use_neo4j else self.memory_graph.get_labeled_items(label_name=target)
        elif op == "get_team_overview":
            return self._neo4j_get_team_overview(team_slug_or_id=target) if use_neo4j else self.memory_graph.get_team_overview(team_slug_or_id=target)
        elif op == "get_repo_overview":
            return self._neo4j_get_repo_overview(repo_name=target) if use_neo4j else self.memory_graph.get_repo_overview(repo_name=target)

        # ── 3. Raw Cypher Execution (Neo4j mode) ──
        elif op == "raw_cypher":
            if self.mode == "neo4j" and self.neo4j_client:
                upper = target.upper()
                if any(kw in upper for kw in ["CREATE", "DELETE", "SET", "REMOVE", "MERGE", "DROP", "DETACH"]):
                    return {"error": "Write and destructive Cypher queries are prohibited via agent retrieval tool."}
                return self.neo4j_client.run_query(target, params)
            return {"error": "raw_cypher requires active Neo4j connection. Use structured operations for in-memory mode."}

        return {"error": f"Unsupported entity graph operation '{operation}'"}

    # ── Native Parameterized Cypher Implementations for Neo4j Mode ────────────

    def _neo4j_get_entity(self, target: str) -> Optional[Dict[str, Any]]:
        clean = target.strip()
        clean_num = re.sub(r"^(pr|pull|issue|#|\s)+", "", clean, flags=re.IGNORECASE).strip().lstrip("#")
        target_int = int(clean_num) if clean_num.isdigit() else None
        cypher = """
        MATCH (n)
        WHERE n.node_id = $target
           OR n.node_id ENDS WITH (':' + $clean)
           OR ($clean_num <> '' AND n.node_id ENDS WITH (':' + $clean_num))
           OR ($target_int IS NOT NULL AND n.number = $target_int)
           OR n.login = $clean
           OR n.sha = $clean
           OR n.path = $target
        RETURN labels(n)[0] AS label, n.node_id AS node_id, properties(n) AS properties
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"target": target, "clean": clean, "clean_num": clean_num, "target_int": target_int})
        if not rows:
            return None
        r = rows[0]
        props = r.get("properties") or {}
        return {"label": r.get("label"), "node_id": r.get("node_id"), **props}

    def _neo4j_get_neighbors(
        self,
        node_id: str,
        direction: str = "both",
        rel_types: Optional[List[str]] = None,
        target_label: Optional[str] = None,
        max_depth: int = 1,
    ) -> List[Dict[str, Any]]:
        clean = node_id.strip().lstrip("#")
        rel_filter = ":" + "|".join(rel_types) if rel_types else ""
        label_filter = f":{target_label}" if target_label else ""

        if direction == "out":
            pattern = f"-[r{rel_filter}*1..{max_depth}]->(m{label_filter})"
        elif direction == "in":
            pattern = f"<-[r{rel_filter}*1..{max_depth}]-(m{label_filter})"
        else:
            pattern = f"-[r{rel_filter}*1..{max_depth}]-(m{label_filter})"

        cypher = f"""
        MATCH (n)
        WHERE n.node_id = $node_id OR n.node_id ENDS WITH (':' + $clean)
        MATCH (n){pattern}
        RETURN DISTINCT labels(m)[0] AS label, m.node_id AS node_id, properties(m) AS properties
        LIMIT 50
        """
        rows = self.neo4j_client.run_query(cypher, {"node_id": node_id, "clean": clean})
        results = []
        for r in rows:
            props = r.get("properties") or {}
            results.append({"label": r.get("label"), "node_id": r.get("node_id"), **props})
        return results

    def _neo4j_search_nodes(
        self,
        label: Optional[str] = None,
        property_filters: Optional[Dict[str, Any]] = None,
        text_query: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        label_str = f":{label}" if label else ""
        cypher = f"""
        MATCH (n{label_str})
        WHERE ($text_query IS NULL
           OR toLower(n.node_id) CONTAINS toLower($text_query)
           OR toLower(coalesce(n.title, '')) CONTAINS toLower($text_query)
           OR toLower(coalesce(n.name, '')) CONTAINS toLower($text_query)
           OR toLower(coalesce(n.login, '')) CONTAINS toLower($text_query)
           OR toLower(coalesce(n.path, '')) CONTAINS toLower($text_query))
        RETURN labels(n)[0] AS label, n.node_id AS node_id, properties(n) AS properties
        LIMIT $limit
        """
        rows = self.neo4j_client.run_query(cypher, {"text_query": text_query, "limit": limit})
        results = []
        for r in rows:
            props = r.get("properties") or {}
            if property_filters:
                if not all(props.get(k) == v for k, v in property_filters.items()):
                    continue
            results.append({"label": r.get("label"), "node_id": r.get("node_id"), **props})
        return results

    def _neo4j_find_path(self, start_id: str, end_id: str, max_depth: int = 3) -> Optional[List[Dict[str, Any]]]:
        clean_s = start_id.strip().lstrip("#")
        clean_e = end_id.strip().lstrip("#")
        cypher = f"""
        MATCH (a), (b)
        WHERE (a.node_id = $start_id OR a.node_id ENDS WITH (':' + $clean_s) OR a.login = $clean_s)
          AND (b.node_id = $end_id OR b.node_id ENDS WITH (':' + $clean_e) OR b.path = $end_id)
        MATCH p = shortestPath((a)-[*..{max_depth}]-(b))
        RETURN [n IN nodes(p) | {{node_id: n.node_id, label: labels(n)[0]}}] AS nodes,
               [r IN relationships(p) | type(r)] AS rels
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {
            "start_id": start_id, "clean_s": clean_s,
            "end_id": end_id, "clean_e": clean_e,
        })
        if not rows:
            return None
        r = rows[0]
        nodes = r.get("nodes", [])
        rels = r.get("rels", [])
        if not nodes:
            return None
        path = [{"node_id": nodes[0]["node_id"], "label": nodes[0]["label"]}]
        for idx, (node, rel) in enumerate(zip(nodes[1:], rels)):
            path.append({"rel": rel, "node_id": node["node_id"], "label": node["label"]})
        return path

    def _neo4j_get_pr_details(self, pr_identifier: str) -> Optional[Dict[str, Any]]:
        clean = pr_identifier.strip().lstrip("#")
        target_int = int(clean) if clean.isdigit() else None
        cypher = """
        MATCH (pr:PullRequest)
        WHERE pr.node_id = $target OR pr.node_id ENDS WITH (':' + $clean) OR ($target_int IS NOT NULL AND pr.number = $target_int)
        OPTIONAL MATCH (author:User)-[:CREATED]->(pr)
        OPTIONAL MATCH (reviewer:User)-[:REVIEWED]->(pr)
        OPTIONAL MATCH (assignee:User)-[:ASSIGNED_TO]->(pr)
        OPTIONAL MATCH (pr)-[:MODIFIES]->(f:File)
        OPTIONAL MATCH (pr)-[:CLOSES]->(i:Issue)
        RETURN properties(pr) AS pr,
               author.login AS author,
               collect(DISTINCT reviewer.login) AS reviewers,
               collect(DISTINCT assignee.login) AS assignees,
               collect(DISTINCT f.path) AS modified_files,
               collect(DISTINCT {number: i.number, title: i.title, state: i.state}) AS closed_issues
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"target": pr_identifier, "clean": clean, "target_int": target_int})
        if not rows:
            return None
        r = rows[0]
        res = dict(r.get("pr") or {})
        res["author"] = r.get("author") or res.get("author_login")
        res["reviewers"] = [x for x in r.get("reviewers", []) if x]
        res["assignees"] = [x for x in r.get("assignees", []) if x]
        res["modified_files"] = [x for x in r.get("modified_files", []) if x]
        res["closed_issues"] = [x for x in r.get("closed_issues", []) if x and x.get("number")]
        return res

    def _neo4j_get_user_activity(self, username: str) -> Dict[str, Any]:
        cypher = """
        MATCH (u:User)
        WHERE u.login = $username OR u.node_id ENDS WITH (':' + $username)
        OPTIONAL MATCH (u)-[:CREATED]->(pr:PullRequest)
        OPTIONAL MATCH (u)-[:AUTHORED]->(c:Commit)
        OPTIONAL MATCH (u)-[:REVIEWED]->(rpr:PullRequest)
        OPTIONAL MATCH (u)-[:ASSIGNED_TO]->(i:Issue)
        RETURN properties(u) AS user,
               collect(DISTINCT {number: pr.number, title: pr.title, state: pr.state}) AS authored_prs,
               collect(DISTINCT {sha: c.sha, message: c.message}) AS authored_commits,
               collect(DISTINCT {number: rpr.number, title: rpr.title}) AS reviewed_prs,
               collect(DISTINCT {number: i.number, title: i.title, state: i.state}) AS assigned_issues
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"username": username})
        if not rows:
            return {"user": username, "authored_prs_count": 0, "authored_commits_count": 0}
        r = rows[0]
        u_props = r.get("user") or {}
        a_prs = [x for x in r.get("authored_prs", []) if x and x.get("number")]
        commits = [x for x in r.get("authored_commits", []) if x and x.get("sha")]
        r_prs = [x for x in r.get("reviewed_prs", []) if x and x.get("number")]
        issues = [x for x in r.get("assigned_issues", []) if x and x.get("number")]
        return {
            "user": u_props.get("login", username),
            "name": u_props.get("name"),
            "email": u_props.get("email"),
            "authored_prs_count": len(a_prs),
            "authored_prs": a_prs,
            "authored_commits_count": len(commits),
            "commits": commits,
            "reviewed_prs_count": len(r_prs),
            "reviewed_prs": r_prs,
            "assigned_issues": issues,
        }

    def _neo4j_get_file_contributors(self, file_path: str) -> Dict[str, Any]:
        cypher = """
        MATCH (f:File)
        WHERE f.path = $file_path OR f.node_id ENDS WITH $file_path
        OPTIONAL MATCH (pr:PullRequest)-[:MODIFIES]->(f)
        OPTIONAL MATCH (author:User)-[:CREATED]->(pr)
        OPTIONAL MATCH (c:Commit)-[:MODIFIES]->(f)
        OPTIONAL MATCH (c_author:User)-[:AUTHORED]->(c)
        RETURN f.path AS file,
               collect(DISTINCT coalesce(author.login, c_author.login)) AS authors,
               collect(DISTINCT {sha: c.sha, message: c.message, author: c_author.login}) AS commits,
               collect(DISTINCT {number: pr.number, title: pr.title, state: pr.state, author: author.login}) AS pull_requests
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"file_path": file_path})
        if not rows:
            return {"file": file_path, "authors": [], "commits": [], "pull_requests": []}
        r = rows[0]
        return {
            "file": r.get("file") or file_path,
            "authors": [x for x in r.get("authors", []) if x],
            "commits": [x for x in r.get("commits", []) if x and x.get("sha")],
            "pull_requests": [x for x in r.get("pull_requests", []) if x and x.get("number")],
        }

    def _neo4j_get_commit_details(self, commit_sha_or_id: str) -> Optional[Dict[str, Any]]:
        clean = commit_sha_or_id.strip()
        cypher = """
        MATCH (c:Commit)
        WHERE c.sha STARTS WITH $clean OR c.node_id ENDS WITH $clean
        OPTIONAL MATCH (author:User)-[:AUTHORED]->(c)
        OPTIONAL MATCH (c)-[:MODIFIES]->(f:File)
        RETURN properties(c) AS commit,
               author.login AS author,
               collect(DISTINCT f.path) AS modified_files
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"clean": clean})
        if not rows:
            return None
        r = rows[0]
        res = dict(r.get("commit") or {})
        res["author"] = r.get("author") or res.get("author_login")
        res["modified_files"] = [x for x in r.get("modified_files", []) if x]
        return res

    def _neo4j_get_issue_details(self, issue_identifier: str) -> Optional[Dict[str, Any]]:
        clean = issue_identifier.strip().lstrip("#")
        target_int = int(clean) if clean.isdigit() else None
        cypher = """
        MATCH (i:Issue)
        WHERE i.node_id = $target OR i.node_id ENDS WITH (':' + $clean) OR ($target_int IS NOT NULL AND i.number = $target_int)
        OPTIONAL MATCH (author:User)-[:CREATED]->(i)
        OPTIONAL MATCH (assignee:User)-[:ASSIGNED_TO]->(i)
        OPTIONAL MATCH (pr:PullRequest)-[:CLOSES]->(i)
        OPTIONAL MATCH (i)-[:TAGGED_WITH]->(lbl:Label)
        RETURN properties(i) AS issue,
               author.login AS author,
               collect(DISTINCT assignee.login) AS assignees,
               collect(DISTINCT lbl.name) AS labels,
               collect(DISTINCT {number: pr.number, title: pr.title, state: pr.state, author: pr.author_login}) AS closing_prs
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"target": issue_identifier, "clean": clean, "target_int": target_int})
        if not rows:
            return None
        r = rows[0]
        res = dict(r.get("issue") or {})
        res["author"] = r.get("author") or res.get("author_login")
        res["assignees"] = [x for x in r.get("assignees", []) if x]
        res["labels"] = [x for x in r.get("labels", []) if x]
        res["closing_prs"] = [x for x in r.get("closing_prs", []) if x and x.get("number")]
        return res

    def _neo4j_get_labeled_items(self, label_name: str) -> Dict[str, Any]:
        cypher = """
        MATCH (lbl:Label)
        WHERE toLower(lbl.name) = toLower($label) OR lbl.node_id ENDS WITH (':' + $label)
        OPTIONAL MATCH (i:Issue)-[:TAGGED_WITH]->(lbl)
        OPTIONAL MATCH (pr:PullRequest)-[:TAGGED_WITH]->(lbl)
        RETURN lbl.name AS label,
               collect(DISTINCT {number: i.number, title: i.title, state: i.state}) AS issues,
               collect(DISTINCT {number: pr.number, title: pr.title, state: pr.state}) AS pull_requests
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"label": label_name.strip()})
        if not rows:
            return {"label": label_name, "issues": [], "pull_requests": [], "total_count": 0}
        r = rows[0]
        issues = [x for x in r.get("issues", []) if x and x.get("number")]
        prs = [x for x in r.get("pull_requests", []) if x and x.get("number")]
        return {
            "label": r.get("label") or label_name,
            "issues": issues,
            "pull_requests": prs,
            "total_count": len(issues) + len(prs),
        }

    def _neo4j_get_team_overview(self, team_slug_or_id: str) -> Dict[str, Any]:
        cypher = """
        MATCH (t:Team)
        WHERE t.slug = $slug OR t.node_id ENDS WITH $slug
        OPTIONAL MATCH (u:User)-[:MEMBER_OF]->(t)
        OPTIONAL MATCH (t)-[:HAS_ACCESS_TO]->(r:Repository)
        RETURN t.slug AS team,
               collect(DISTINCT u.login) AS members,
               collect(DISTINCT r.full_name) AS accessible_repos
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"slug": team_slug_or_id.strip()})
        if not rows:
            return {"team": team_slug_or_id, "members": [], "accessible_repos": []}
        r = rows[0]
        return {
            "team": r.get("team") or team_slug_or_id,
            "members": [x for x in r.get("members", []) if x],
            "accessible_repos": [x for x in r.get("accessible_repos", []) if x],
        }

    def _neo4j_get_repo_overview(self, repo_name: str) -> Dict[str, Any]:
        cypher = """
        MATCH (r:Repository)
        WHERE r.full_name = $repo_name OR r.name = $repo_name OR r.node_id ENDS WITH $repo_name
        OPTIONAL MATCH (r)-[:CONTAINS]->(f:File)
        OPTIONAL MATCH (t:Team)-[:HAS_ACCESS_TO]->(r)
        RETURN r.full_name AS repository,
               count(DISTINCT f) AS files_count,
               collect(DISTINCT t.slug) AS teams_with_access,
               r.open_issues_count AS open_issues_count,
               r.stargazers_count AS stargazers_count,
               r.default_branch AS default_branch
        LIMIT 1
        """
        rows = self.neo4j_client.run_query(cypher, {"repo_name": repo_name.strip()})
        if not rows:
            return {"repository": repo_name, "files_count": 0, "teams_with_access": []}
        r = rows[0]
        return {
            "repository": r.get("repository") or repo_name,
            "files_count": r.get("files_count", 0),
            "teams_with_access": [x for x in r.get("teams_with_access", []) if x],
            "open_issues_count": r.get("open_issues_count", 0),
            "stargazers_count": r.get("stargazers_count", 0),
            "default_branch": r.get("default_branch", "main"),
        }


    def format_as_chunk(self, data: Any, operation: str, target: str) -> Optional[Dict[str, Any]]:
        """Converts raw graph query output into a standard evidence chunk for LLM synthesis."""
        if not data or not isinstance(data, dict) or data.get("error"):
            return None

        op = operation.strip().lower()
        clean_target = target.strip().lstrip("#")
        node_id = data.get("node_id") or f"github:{op}:{clean_target}"
        url = data.get("html_url") or data.get("url") or ""

        title = data.get("title")
        if not title:
            if "number" in data:
                title = f"Pull Request #{data['number']}: {data.get('title', '')}" if "pr" in op else f"Issue #{data['number']}: {data.get('title', '')}"
            elif "login" in data:
                title = f"User Profile: {data.get('login')} ({data.get('name', '')})"
            elif "repository" in data:
                title = f"Repository: {data.get('repository')}"
            elif "team" in data:
                title = f"Team: {data.get('team')}"
            elif "file" in data:
                title = f"File: {data.get('file')}"
            elif "sha" in data:
                title = f"Commit {data.get('sha')[:7]}: {data.get('message', '')}"
            else:
                title = f"GitHub Entity: {target}"

        return {
            "chunk_id": node_id,
            "source": "github",
            "title": title.strip(),
            "url": url,
            "text": json.dumps(data, indent=2, ensure_ascii=False),
            "score": 1.0,
            "extra_metadata": {
                "operation": operation,
                "target": target,
            },
        }

    def retrieve(
        self,
        operation: str,
        target: str,
        parameters: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieval interface that executes the graph operation and formats results
        as standard evidence chunks ready for ContextBuilder and LLM grounding.
        """
        raw = self.search(operation, target, parameters, user_context)
        if isinstance(raw, list):
            chunks = []
            for item in raw:
                if isinstance(item, dict):
                    chunk = self.format_as_chunk(item, operation, target)
                    if chunk:
                        chunks.append(chunk)
            return chunks
        elif isinstance(raw, dict) and not raw.get("error"):
            chunk = self.format_as_chunk(raw, operation, target)
            return [chunk] if chunk else []
        return []

