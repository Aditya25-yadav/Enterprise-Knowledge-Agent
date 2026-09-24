"""
Unit Test Suite for RBAC Translators across Vector, BM25, and Graph DBs (Phase 8 - Part 2).

Validates:
  1. QdrantFilterTranslator generating rest.Filter for vector pre-filtering.
  2. BM25FilterTranslator generating boolean predicates for BM25 search.
  3. CypherRBACClauseBuilder generating parameterized WHERE clauses for Neo4j.
  4. GraphNodeFilter evaluating in-memory graph nodes.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Ensure project root is in sys.path
for _parent in Path(__file__).resolve().parents:
    if (_parent / "backend").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from backend.models.graph import GraphNode
from backend.models.security import UserSecurityContext
from backend.security.hierarchy import RoleHierarchy
from backend.security.rbac_resolver import RBACResolver
from backend.security.translators import (
    BM25FilterTranslator,
    CypherRBACClauseBuilder,
    GraphNodeFilter,
    QdrantFilterTranslator,
)


class TestRBACFilterTranslators(unittest.TestCase):

    def setUp(self) -> None:
        self.resolver = RBACResolver()
        self.user_ctx = self.resolver.resolve_context({
            "roles": ["engineer"],
            "groups": ["payments-core"],
            "user_id": "alice@company.com",
        })

    def test_01_qdrant_filter_translation(self) -> None:
        """Verifies QdrantFilterTranslator builds correct rest.Filter structure."""
        q_filter = QdrantFilterTranslator.build_filter(
            context=self.user_ctx,
            source="github",
            resource_type="file",
        )
        self.assertIsNotNone(q_filter)
        must_list = q_filter.must
        self.assertGreaterEqual(len(must_list), 3)

        # Verify source and resource_type filters
        field_keys = [c.key for c in must_list if hasattr(c, "key")]
        self.assertIn("source", field_keys)
        self.assertIn("resource_type", field_keys)

        # Verify RBAC should clauses
        rbac_clause = next(c for c in must_list if hasattr(c, "should"))
        should_keys = [s.key for s in rbac_clause.should]
        self.assertIn("is_public", should_keys)
        self.assertIn("allowed_roles", should_keys)
        self.assertIn("allowed_users", should_keys)
        self.assertIn("allowed_groups", should_keys)

    def test_02_qdrant_superadmin_bypass(self) -> None:
        """Verifies superadmin does not receive RBAC should constraints in Qdrant filter."""
        super_ctx = self.resolver.resolve_context({
            "roles": ["admin"],
            "is_superadmin": True,
        })
        q_filter = QdrantFilterTranslator.build_filter(
            context=super_ctx,
            source="jira",
        )
        self.assertIsNotNone(q_filter)
        # Only source filter, no RBAC should clauses
        must_list = q_filter.must
        self.assertEqual(len(must_list), 1)
        self.assertEqual(must_list[0].key, "source")

    def test_03_bm25_predicate_translator(self) -> None:
        """Verifies BM25FilterTranslator predicate function against candidate dicts."""
        predicate = BM25FilterTranslator.create_predicate(self.user_ctx, self.resolver)

        public_chunk = {"is_public": True, "allowed_roles": []}
        eng_chunk = {"is_public": False, "allowed_roles": ["employee"]}
        ciso_chunk = {"is_public": False, "allowed_roles": ["ciso"]}
        user_chunk = {"is_public": False, "allowed_roles": [], "allowed_users": ["alice@company.com"]}
        group_chunk = {"is_public": False, "allowed_roles": [], "allowed_groups": ["payments-team"]}

        self.assertTrue(predicate(public_chunk))
        self.assertTrue(predicate(eng_chunk))    # engineer inherits employee
        self.assertFalse(predicate(ciso_chunk))   # engineer does not inherit ciso
        self.assertTrue(predicate(user_chunk))   # matches alice@company.com
        self.assertTrue(predicate(group_chunk))  # payments-core is inside payments-team

    def test_04_cypher_clause_builder(self) -> None:
        """Verifies CypherRBACClauseBuilder outputs valid Cypher syntax and parameters."""
        clause, params = CypherRBACClauseBuilder.build_clause(
            context=self.user_ctx,
            node_variable="n",
            param_prefix="p_",
        )

        self.assertIn("n.is_public = true", clause)
        self.assertIn("ANY(r IN n.allowed_roles WHERE r IN $p_roles)", clause)
        self.assertIn("$p_user_id IN n.allowed_users", clause)
        self.assertIn("ANY(g IN n.allowed_groups WHERE g IN $p_groups)", clause)

        self.assertEqual(params["p_user_id"], "alice@company.com")
        self.assertIn("engineer", params["p_roles"])
        self.assertIn("employee", params["p_roles"])
        self.assertIn("payments-core", params["p_groups"])
        self.assertIn("payments-team", params["p_groups"])

    def test_05_cypher_superadmin_bypass(self) -> None:
        """Verifies Cypher builder emits 'true' with empty params for superadmin."""
        super_ctx = self.resolver.resolve_context({"is_superadmin": True})
        clause, params = CypherRBACClauseBuilder.build_clause(super_ctx)
        self.assertEqual(clause, "true")
        self.assertEqual(params, {})

    def test_06_graph_node_filter(self) -> None:
        """Verifies GraphNodeFilter evaluates GraphNode properties."""
        node_allowed = GraphNode(
            node_id="github:pr:142",
            label="PullRequest",
            properties={
                "is_public": False,
                "allowed_roles": ["employee"],
            },
        )
        node_restricted = GraphNode(
            node_id="notion:vault:master",
            label="Secret",
            properties={
                "is_public": False,
                "allowed_roles": ["security-admin"],
            },
        )

        self.assertTrue(GraphNodeFilter.is_authorized(node_allowed, self.user_ctx, self.resolver))
        self.assertFalse(GraphNodeFilter.is_authorized(node_restricted, self.user_ctx, self.resolver))


if __name__ == "__main__":
    unittest.main()
