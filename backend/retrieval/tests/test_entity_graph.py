"""
Unit Test Suite for Generalized Entity Graph Retrieval Layer (Step 67).

Validates:
  1. Generalized entity lookup (`get_entity`).
  2. Generalized node search by label, property filter, and text keywords (`search_nodes`).
  3. Directional multi-hop BFS relationship expansion (`get_neighbors`).
  4. Shortest relationship path discovery (`find_path`).
  5. Pull Request intelligence (`get_pr_details`).
  6. Developer 360 activity lookup (`get_user_activity`).
  7. Code file contributors & commit history (`get_file_contributors`).
  8. Commit details and PR merge links (`get_commit_details`).
  9. Issue intelligence and closing PR cross-links (`get_issue_details`).
  10. Label/topic querying (`get_labeled_items`).
  11. Team members and repo access overview (`get_team_overview`).
  12. Native LangChain `github_entity_search` StructuredTool invocation.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from backend.agent.langchain_tools import create_langchain_tools
from backend.models.graph import (
    CommitNode,
    FileNode,
    GitHubGraphBundle,
    GraphNode,
    GraphRelationship,
    IssueNode,
    LabelNode,
    NodeLabel,
    PullRequestNode,
    RelType,
    RepositoryNode,
    TeamNode,
    UserNode,
)
from backend.retrieval.entity_graph import EntityGraphRetriever, InMemoryEntityGraph


class TestEntityGraphRetriever(unittest.TestCase):

    def setUp(self) -> None:
        self.memory_graph = InMemoryEntityGraph()
        self.retriever = EntityGraphRetriever(memory_graph=self.memory_graph)

        # ── Construct a rich GitHub entity graph fixture ──
        # 1. Repository
        self.repo = RepositoryNode(
            full_name="company/payments",
            name="payments",
            owner_login="company",
            html_url="https://github.com/company/payments",
            description="Core payment processing gateway and checkout services.",
        )
        self.memory_graph.add_node(self.repo.to_graph_node())

        # 2. Users
        self.alice = UserNode(login="alice", name="Alice Engineer", email="alice@company.com")
        self.bob = UserNode(login="bob", name="Bob Reviewer", email="bob@company.com")
        self.charlie = UserNode(login="charlie", name="Charlie Lead", email="charlie@company.com")
        self.memory_graph.add_node(self.alice.to_graph_node())
        self.memory_graph.add_node(self.bob.to_graph_node())
        self.memory_graph.add_node(self.charlie.to_graph_node())

        # 3. Team
        self.team = TeamNode(org_login="company", slug="core-infra", name="Core Infrastructure")
        self.memory_graph.add_node(self.team.to_graph_node())
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.MEMBER_OF.value,
            from_id=self.alice.node_id,
            to_id=self.team.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.HAS_ACCESS_TO.value,
            from_id=self.team.node_id,
            to_id=self.repo.node_id,
        ))

        # 4. Files
        self.file_engine = FileNode(repo_full_name="company/payments", path="src/engine.py", language="Python")
        self.file_api = FileNode(repo_full_name="company/payments", path="docs/api.md", language="Markdown")
        self.memory_graph.add_node(self.file_engine.to_graph_node())
        self.memory_graph.add_node(self.file_api.to_graph_node())
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.CONTAINS.value,
            from_id=self.repo.node_id,
            to_id=self.file_engine.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.CONTAINS.value,
            from_id=self.repo.node_id,
            to_id=self.file_api.node_id,
        ))

        # 5. Label
        self.bug_label = LabelNode(repo_full_name="company/payments", name="bug", color="d73a4a")
        self.memory_graph.add_node(self.bug_label.to_graph_node())

        # 6. Issue #98
        self.issue98 = IssueNode(
            repo_full_name="company/payments",
            number=98,
            title="3DS Challenge timeout failure in Checkout",
            state="closed",
            author_login="charlie",
            body="Users experience 401 error when 3DS verification takes >30s.",
        )
        self.memory_graph.add_node(self.issue98.to_graph_node())
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.CREATED.value,
            from_id=self.charlie.node_id,
            to_id=self.issue98.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.TAGGED_WITH.value,
            from_id=self.issue98.node_id,
            to_id=self.bug_label.node_id,
        ))

        # 7. Pull Request #142 (Authored by Alice, Reviewed by Bob, Modifies engine.py, Closes Issue #98)
        self.pr142 = PullRequestNode(
            repo_full_name="company/payments",
            number=142,
            title="Fix 3DS timeout in payment gateway transaction initiation",
            state="merged",
            author_login="alice",
            body="Extends 3DS verification timeout and handles HTTP 401 retry token.",
        )
        self.memory_graph.add_node(self.pr142.to_graph_node())
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.CREATED.value,
            from_id=self.alice.node_id,
            to_id=self.pr142.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.REVIEWED.value,
            from_id=self.bob.node_id,
            to_id=self.pr142.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.MODIFIES.value,
            from_id=self.pr142.node_id,
            to_id=self.file_engine.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.CLOSES.value,
            from_id=self.pr142.node_id,
            to_id=self.issue98.node_id,
        ))

        # 8. Commit
        self.commit1 = CommitNode(
            repo_full_name="company/payments",
            sha="a1b2c3d4e5",
            message="Increase 3DS challenge timeout to 60s",
            author_login="alice",
        )
        self.memory_graph.add_node(self.commit1.to_graph_node())
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.AUTHORED.value,
            from_id=self.alice.node_id,
            to_id=self.commit1.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.MODIFIES.value,
            from_id=self.commit1.node_id,
            to_id=self.file_engine.node_id,
        ))
        self.memory_graph.add_relationship(GraphRelationship(
            rel_type=RelType.PART_OF.value,
            from_id=self.commit1.node_id,
            to_id=self.pr142.node_id,
        ))

    def tearDown(self) -> None:
        self.memory_graph.clear()

    # ── Test Cases ────────────────────────────────────────────────────────────

    def test_01_get_entity(self) -> None:
        """Verifies direct entity lookup by node_id and alias."""
        # By full node_id
        user = self.retriever.search("get_entity", self.alice.node_id)
        self.assertIsNotNone(user)
        self.assertEqual(user["login"], "alice")

        # By alias (PR number)
        pr = self.retriever.search("get_entity", "142")
        self.assertIsNotNone(pr)
        self.assertEqual(pr["number"], 142)

    def test_02_search_nodes(self) -> None:
        """Verifies searching entities by label and keyword matching."""
        results = self.retriever.search(
            "search_nodes",
            target="timeout",
            parameters={"label": "PullRequest"},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["number"], 142)

    def test_03_generalized_get_neighbors(self) -> None:
        """Verifies multi-hop BFS neighbor expansion in both directions."""
        neighbors = self.retriever.search(
            "get_neighbors",
            target=self.pr142.node_id,
            parameters={"direction": "both", "max_depth": 1},
        )
        neighbor_nodes = [n["node_id"] for n in neighbors]
        self.assertIn(self.alice.node_id, neighbor_nodes)
        self.assertIn(self.bob.node_id, neighbor_nodes)
        self.assertIn(self.file_engine.node_id, neighbor_nodes)
        self.assertIn(self.issue98.node_id, neighbor_nodes)

    def test_04_find_path(self) -> None:
        """Verifies shortest relationship path discovery."""
        path = self.retriever.search(
            "find_path",
            target=self.bob.node_id,
            parameters={"end_id": self.file_engine.node_id},
        )
        self.assertIsNotNone(path)
        # Path: Bob -> (REVIEWED) -> PR #142 -> (MODIFIES) -> src/engine.py
        node_ids = [step["node_id"] for step in path]
        self.assertEqual(node_ids[0], self.bob.node_id)
        self.assertIn(self.pr142.node_id, node_ids)
        self.assertEqual(node_ids[-1], self.file_engine.node_id)

    def test_05_get_pr_details(self) -> None:
        """Verifies complete PR resolution (author, reviewers, modified files, closed issues)."""
        pr_details = self.retriever.search("get_pr_details", "142")
        self.assertIsNotNone(pr_details)
        self.assertEqual(pr_details["author"], "alice")
        self.assertIn("bob", pr_details["reviewers"])
        self.assertIn("src/engine.py", pr_details["modified_files"])
        self.assertEqual(len(pr_details["closed_issues"]), 1)
        self.assertEqual(pr_details["closed_issues"][0]["number"], 98)

    def test_06_get_user_activity(self) -> None:
        """Verifies developer 360-degree activity view."""
        alice_act = self.retriever.search("get_user_activity", "alice")
        self.assertEqual(alice_act["authored_prs_count"], 1)
        self.assertEqual(alice_act["authored_commits_count"], 1)

        bob_act = self.retriever.search("get_user_activity", "bob")
        self.assertEqual(bob_act["reviewed_prs_count"], 1)

    def test_07_get_file_contributors(self) -> None:
        """Verifies contributors and commits modifying a file."""
        contribs = self.retriever.search("get_file_contributors", "src/engine.py")
        self.assertIn("alice", contribs["authors"])
        self.assertEqual(len(contribs["commits"]), 1)
        self.assertEqual(len(contribs["pull_requests"]), 1)
        self.assertEqual(contribs["pull_requests"][0]["number"], 142)

    def test_08_get_commit_details(self) -> None:
        """Verifies commit author, modified files, and parent PR merge link."""
        commit = self.retriever.search("get_commit_details", "a1b2c3d4e5")
        self.assertIsNotNone(commit)
        self.assertEqual(commit["author"], "alice")
        self.assertIn("src/engine.py", commit["modified_files"])
        self.assertEqual(commit["parent_pr"]["number"], 142)

    def test_09_get_issue_details(self) -> None:
        """Verifies issue reporter, labels, and closing PR link."""
        issue = self.retriever.search("get_issue_details", "98")
        self.assertIsNotNone(issue)
        self.assertEqual(issue["author"], "charlie")
        self.assertIn("bug", issue["labels"])
        self.assertEqual(len(issue["closing_prs"]), 1)
        self.assertEqual(issue["closing_prs"][0]["number"], 142)

    def test_10_get_labeled_items(self) -> None:
        """Verifies querying issues/PRs by label."""
        labeled = self.retriever.search("get_labeled_items", "bug")
        self.assertEqual(labeled["total_count"], 1)
        self.assertEqual(labeled["issues"][0]["number"], 98)

    def test_11_get_team_overview(self) -> None:
        """Verifies team members and accessible repositories."""
        team = self.retriever.search("get_team_overview", "core-infra")
        self.assertIn("alice", team["members"])
        self.assertIn("company/payments", team["accessible_repos"])

    def test_12_native_langchain_entity_tool_invoke(self) -> None:
        """Verifies native LangChain github_entity_search tool invocation."""
        lc_tools = create_langchain_tools(
            entity_graph_retriever=self.retriever,
            user_context={"roles": ["engineer"], "user_id": "eng@company.com"},
        )
        self.assertEqual(len(lc_tools), 5)
        entity_tool = next(t for t in lc_tools if t.name == "github_entity_search")
        res_json = entity_tool.invoke({"operation": "get_pr_details", "target": "142"})
        self.assertIn("alice", res_json)
        self.assertIn("src/engine.py", res_json)
        self.assertIn("bob", res_json)

    def test_13_native_neo4j_mode_cypher_dispatch(self) -> None:
        """Verifies that in Neo4j mode, EntityGraphRetriever dispatches parameterized Cypher to neo4j_client."""
        class MockNeo4jClient:
            def __init__(self) -> None:
                self.queries_run: List[Tuple[str, Dict[str, Any]]] = []

            def test_connection(self) -> bool:
                return True

            def run_query(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
                self.queries_run.append((cypher, params or {}))
                # Return mock PR row
                if "MATCH (pr:PullRequest)" in cypher:
                    return [{
                        "pr": {"node_id": "github:pr:company/payments:142", "number": 142, "title": "Fix 3DS timeout", "state": "MERGED"},
                        "author": "alice",
                        "reviewers": ["bob"],
                        "assignees": [],
                        "modified_files": ["backend/services/checkout.py"],
                        "closed_issues": [{"number": 928, "title": "3DS timeout", "state": "closed"}],
                    }]
                elif "shortestPath" in cypher:
                    return [{
                        "nodes": [{"node_id": "github:user:alice", "label": "User"}, {"node_id": "github:file:company/payments:checkout.py", "label": "File"}],
                        "rels": ["MODIFIES"],
                    }]
                return []

        mock_neo4j = MockNeo4jClient()
        neo4j_retriever = EntityGraphRetriever(neo4j_client=mock_neo4j)
        self.assertEqual(neo4j_retriever.mode, "neo4j")

        # 1. Test get_pr_details via native Cypher
        pr_details = neo4j_retriever.search("get_pr_details", "142")
        self.assertIsNotNone(pr_details)
        self.assertEqual(pr_details["author"], "alice")
        self.assertEqual(pr_details["reviewers"], ["bob"])
        self.assertGreater(len(mock_neo4j.queries_run), 0)
        self.assertIn("MATCH (pr:PullRequest)", mock_neo4j.queries_run[-1][0])
        self.assertEqual(mock_neo4j.queries_run[-1][1]["target_int"], 142)

        # 2. Test find_path via native Cypher
        path = neo4j_retriever.search("find_path", "github:user:alice", {"end_id": "github:file:company/payments:checkout.py"})
        self.assertIsNotNone(path)
        self.assertEqual(len(path), 2)
        self.assertIn("shortestPath", mock_neo4j.queries_run[-1][0])


if __name__ == "__main__":
    unittest.main()
