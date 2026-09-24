"""
Unit Test Suite for RBAC Resolver & Security Hierarchy (Phase 8 - Part 1).

Validates:
  1. RoleHierarchy expansion and transitive role resolution.
  2. GroupHierarchy expansion for nested organizational teams.
  3. Public resource access.
  4. Direct user whitelist access.
  5. Direct role and hierarchical role access (e.g. admin -> engineer -> employee).
  6. Direct group and nested group access (e.g. payments-core -> payments-team -> engineering).
  7. Parent-child permission inheritance.
  8. Superadmin bypass privileges.
  9. Guest/unauthorized access denial.
  10. Candidate batch filtering and raw context normalization.
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

from backend.models.okf import OKFConcept, OKFPermissions
from backend.models.security import (
    AccessDecision,
    DecisionReason,
    ResourcePermissions,
    UserSecurityContext,
)
from backend.security.hierarchy import GroupHierarchy, RoleHierarchy
from backend.security.rbac_resolver import RBACResolver


class TestRBACResolver(unittest.TestCase):

    def setUp(self) -> None:
        self.role_hierarchy = RoleHierarchy()
        self.group_hierarchy = GroupHierarchy()
        self.resolver = RBACResolver(
            role_hierarchy=self.role_hierarchy,
            group_hierarchy=self.group_hierarchy,
        )

    def test_01_role_hierarchy_expansion(self) -> None:
        """Verifies transitive role inheritance."""
        admin_roles = self.role_hierarchy.expand_roles(["admin"])
        self.assertIn("admin", admin_roles)
        self.assertIn("security-admin", admin_roles)
        self.assertIn("engineer", admin_roles)
        self.assertIn("employee", admin_roles)
        self.assertIn("guest", admin_roles)

        eng_roles = self.role_hierarchy.expand_roles(["engineer"])
        self.assertIn("engineer", eng_roles)
        self.assertIn("employee", eng_roles)
        self.assertIn("guest", eng_roles)
        self.assertNotIn("admin", eng_roles)
        self.assertNotIn("security-admin", eng_roles)

    def test_02_group_hierarchy_expansion(self) -> None:
        """Verifies nested team/group membership expansion."""
        core_groups = self.group_hierarchy.expand_groups(["payments-core"])
        self.assertIn("payments-core", core_groups)
        self.assertIn("payments-team", core_groups)
        self.assertIn("engineering", core_groups)
        self.assertIn("all-company", core_groups)

        sec_groups = self.group_hierarchy.expand_groups(["secops-team"])
        self.assertIn("secops-team", sec_groups)
        self.assertIn("security", sec_groups)
        self.assertIn("engineering", sec_groups)
        self.assertNotIn("payments-core", sec_groups)

    def test_03_public_resource_access(self) -> None:
        """Verifies that public resources are accessible by all users, including guests."""
        guest_ctx = UserSecurityContext(roles=["guest"], user_id="anon@public.com")
        public_res = ResourcePermissions(is_public=True, allowed_roles=["security-admin"])

        decision = self.resolver.evaluate_access(guest_ctx, public_res)
        self.assertTrue(decision.is_allowed)
        self.assertEqual(decision.reason, DecisionReason.PUBLIC)

    def test_04_direct_user_whitelist(self) -> None:
        """Verifies direct user whitelist authorization."""
        alice_ctx = UserSecurityContext(roles=["employee"], user_id="alice@company.com")
        bob_ctx = UserSecurityContext(roles=["employee"], user_id="bob@company.com")

        res = ResourcePermissions(
            is_public=False,
            allowed_roles=["ciso"],
            allowed_users=["alice@company.com"],
        )

        alice_dec = self.resolver.evaluate_access(alice_ctx, res)
        self.assertTrue(alice_dec.is_allowed)
        self.assertEqual(alice_dec.reason, DecisionReason.USER_WHITELIST)
        self.assertEqual(alice_dec.matched_identity, "alice@company.com")

        bob_dec = self.resolver.evaluate_access(bob_ctx, res)
        self.assertFalse(bob_dec.is_allowed)
        self.assertEqual(bob_dec.reason, DecisionReason.DENIED)

    def test_05_hierarchical_role_match(self) -> None:
        """Verifies that parent roles automatically gain access to child role resources."""
        eng_res = ResourcePermissions(
            is_public=False,
            allowed_roles=["engineer"],
        )

        admin_ctx = UserSecurityContext(roles=["admin"], user_id="admin@company.com")
        eng_ctx = UserSecurityContext(roles=["engineer"], user_id="eng@company.com")
        guest_ctx = UserSecurityContext(roles=["guest"], user_id="guest@external.com")

        # Admin possesses engineer role transitively -> Allowed
        admin_dec = self.resolver.evaluate_access(admin_ctx, eng_res)
        self.assertTrue(admin_dec.is_allowed)
        self.assertEqual(admin_dec.reason, DecisionReason.ROLE_MATCH)

        # Engineer possesses engineer role directly -> Allowed
        eng_dec = self.resolver.evaluate_access(eng_ctx, eng_res)
        self.assertTrue(eng_dec.is_allowed)
        self.assertEqual(eng_dec.reason, DecisionReason.ROLE_MATCH)

        # Guest does not possess engineer role -> Denied
        guest_dec = self.resolver.evaluate_access(guest_ctx, eng_res)
        self.assertFalse(guest_dec.is_allowed)
        self.assertEqual(guest_dec.reason, DecisionReason.DENIED)

    def test_06_nested_group_match(self) -> None:
        """Verifies that membership in a sub-group grants access to parent group resources."""
        team_res = ResourcePermissions(
            is_public=False,
            allowed_roles=[],
            allowed_groups=["payments-team"],
        )

        core_member_ctx = UserSecurityContext(
            roles=["employee"],
            groups=["payments-core"],
            user_id="core_dev@company.com",
        )
        secops_member_ctx = UserSecurityContext(
            roles=["employee"],
            groups=["secops-team"],
            user_id="secops_dev@company.com",
        )

        # payments-core is a sub-group of payments-team -> Allowed
        core_dec = self.resolver.evaluate_access(core_member_ctx, team_res)
        self.assertTrue(core_dec.is_allowed)
        self.assertEqual(core_dec.reason, DecisionReason.GROUP_MATCH)
        self.assertEqual(core_dec.matched_identity, "payments-team")

        # secops-team is not part of payments-team -> Denied
        sec_dec = self.resolver.evaluate_access(secops_member_ctx, team_res)
        self.assertFalse(sec_dec.is_allowed)
        self.assertEqual(sec_dec.reason, DecisionReason.DENIED)

    def test_07_parent_permission_inheritance(self) -> None:
        """Verifies inheritance from parent resource when parent_permission_resolver is configured."""
        parent_store = {
            "vault/master": ResourcePermissions(
                is_public=False,
                allowed_roles=["security-admin"],
            )
        }

        resolver_with_parent = RBACResolver(
            role_hierarchy=self.role_hierarchy,
            group_hierarchy=self.group_hierarchy,
            parent_permission_resolver=lambda pid: parent_store.get(pid),
        )

        # Child chunk with no roles but pointing to parent_id
        child_chunk = {
            "chunk_id": "vault/master#chunk_1",
            "is_public": False,
            "allowed_roles": [],
            "parent_id": "vault/master",
        }

        sec_admin_ctx = UserSecurityContext(roles=["secops"], user_id="sec@company.com")
        eng_ctx = UserSecurityContext(roles=["engineer"], user_id="eng@company.com")

        # Secops has security-admin role -> Allowed via parent inheritance
        sec_dec = resolver_with_parent.evaluate_access(sec_admin_ctx, child_chunk)
        self.assertTrue(sec_dec.is_allowed)
        self.assertEqual(sec_dec.reason, DecisionReason.INHERITED_ALLOW)

        # Engineer does not have security-admin -> Denied
        eng_dec = resolver_with_parent.evaluate_access(eng_ctx, child_chunk)
        self.assertFalse(eng_dec.is_allowed)
        self.assertEqual(eng_dec.reason, DecisionReason.DENIED)

    def test_08_superadmin_bypass(self) -> None:
        """Verifies that is_superadmin bypasses all role and user restrictions."""
        super_ctx = UserSecurityContext(
            roles=["employee"],
            user_id="root@company.com",
            is_superadmin=True,
        )
        ultra_secret_res = ResourcePermissions(
            is_public=False,
            allowed_roles=["board-member"],
            allowed_users=["ceo@company.com"],
        )

        decision = self.resolver.evaluate_access(super_ctx, ultra_secret_res)
        self.assertTrue(decision.is_allowed)
        self.assertEqual(decision.reason, DecisionReason.SUPERADMIN_BYPASS)

    def test_09_candidate_batch_filtering(self) -> None:
        """Verifies batch filtering of candidate dictionaries."""
        items = [
            {"chunk_id": "public_doc", "is_public": True, "allowed_roles": []},
            {"chunk_id": "eng_doc", "is_public": False, "allowed_roles": ["engineer"]},
            {"chunk_id": "admin_doc", "is_public": False, "allowed_roles": ["admin"]},
            {"chunk_id": "alice_doc", "is_public": False, "allowed_roles": [], "allowed_users": ["alice@company.com"]},
        ]

        eng_ctx = {"roles": ["engineer"], "user_id": "bob@company.com"}
        filtered = self.resolver.filter_candidates(eng_ctx, items)

        retained_ids = [c["chunk_id"] for c in filtered]
        self.assertIn("public_doc", retained_ids)
        self.assertIn("eng_doc", retained_ids)
        self.assertNotIn("admin_doc", retained_ids)
        self.assertNotIn("alice_doc", retained_ids)

    def test_10_okf_concept_evaluation(self) -> None:
        """Verifies direct evaluation of OKFConcept instance."""
        concept = OKFConcept(
            type="Architecture",
            title="Core Banking Engine",
            permissions=OKFPermissions(
                is_public=False,
                allowed_roles=["engineer"],
                allowed_groups=["payments-team"],
            ),
        )

        dev_ctx = {"roles": ["developer"], "user_id": "dev@company.com"}
        decision = self.resolver.evaluate_access(dev_ctx, concept)
        self.assertTrue(decision.is_allowed)
        self.assertEqual(decision.reason, DecisionReason.ROLE_MATCH)


if __name__ == "__main__":
    unittest.main()
