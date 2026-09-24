"""
Centralized Enterprise RBAC Resolver Engine (Phase 8).

Provides:
  1. RBACResolver: High-performance access control policy engine evaluating
     hierarchical roles, nested groups, direct user whitelists, and parent inheritance.
  2. In-memory and batch candidate filtering for all retrieval modalities.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set, Union

from backend.models.okf import OKFConcept, OKFPermissions
from backend.models.security import (
    AccessDecision,
    DecisionReason,
    ResourcePermissions,
    UserSecurityContext,
)
from backend.security.hierarchy import GroupHierarchy, RoleHierarchy

logger = logging.getLogger(__name__)


class RBACResolver:
    """
    Centralized security and RBAC policy evaluation engine for the Enterprise Knowledge Agent.
    """

    def __init__(
        self,
        role_hierarchy: Optional[RoleHierarchy] = None,
        group_hierarchy: Optional[GroupHierarchy] = None,
        parent_permission_resolver: Optional[Callable[[str], Optional[ResourcePermissions]]] = None,
    ) -> None:
        self.role_hierarchy = role_hierarchy or RoleHierarchy()
        self.group_hierarchy = group_hierarchy or GroupHierarchy()
        self.parent_permission_resolver = parent_permission_resolver

    # ── Context Normalization & Expansion ────────────────────────────────────

    def resolve_context(
        self,
        context: Union[UserSecurityContext, Dict[str, Any], None],
    ) -> UserSecurityContext:
        """
        Normalizes any user context input and expands full transitive roles and groups.
        """
        if isinstance(context, UserSecurityContext):
            sec_ctx = context
        elif isinstance(context, dict):
            sec_ctx = UserSecurityContext.from_dict(context)
        else:
            sec_ctx = UserSecurityContext(roles=["guest"], user_id="anonymous")

        # Expand effective roles and groups via hierarchies
        sec_ctx.effective_roles = self.role_hierarchy.expand_roles(sec_ctx.roles)
        sec_ctx.effective_groups = self.group_hierarchy.expand_groups(sec_ctx.groups)

        return sec_ctx

    # ── Authorization Evaluation ─────────────────────────────────────────────

    def evaluate_access(
        self,
        context: Union[UserSecurityContext, Dict[str, Any], None],
        resource: Union[ResourcePermissions, OKFPermissions, OKFConcept, Dict[str, Any]],
        resource_id: Optional[str] = None,
    ) -> AccessDecision:
        """
        Evaluates whether the security context is authorized to access the given resource.
        """
        sec_ctx = self.resolve_context(context)

        # Normalize target resource permissions
        if isinstance(resource, ResourcePermissions):
            perms = resource
        elif isinstance(resource, OKFPermissions):
            perms = ResourcePermissions(
                is_public=resource.is_public,
                allowed_roles=resource.allowed_roles,
                allowed_users=resource.allowed_users,
                allowed_groups=resource.allowed_groups,
            )
        elif isinstance(resource, OKFConcept):
            p = resource.permissions
            perms = ResourcePermissions(
                is_public=p.is_public,
                allowed_roles=p.allowed_roles,
                allowed_users=p.allowed_users,
                allowed_groups=p.allowed_groups,
                parent_id=resource.extra_metadata.get("parent_id"),
            )
        elif isinstance(resource, dict):
            perms = ResourcePermissions.from_payload(resource)
        else:
            perms = ResourcePermissions(is_public=False, allowed_roles=["admin"])

        target_id = resource_id or getattr(resource, "resource", None) or getattr(resource, "resource_id", None)
        if isinstance(resource, dict) and not target_id:
            target_id = resource.get("resource_id") or resource.get("chunk_id") or resource.get("node_id")

        # 1. Superadmin bypass
        if sec_ctx.is_superadmin:
            return AccessDecision(
                is_allowed=True,
                reason=DecisionReason.SUPERADMIN_BYPASS,
                resource_id=target_id,
                matched_identity="superadmin",
                details="Access granted via superadmin privileges.",
            )

        # 2. Public resource access
        if perms.is_public:
            return AccessDecision(
                is_allowed=True,
                reason=DecisionReason.PUBLIC,
                resource_id=target_id,
                details="Access granted because the resource is marked public.",
            )

        # 3. Direct User Whitelist
        if sec_ctx.user_id and perms.allowed_users:
            normalized_allowed_users = [u.lower() for u in perms.allowed_users]
            if sec_ctx.user_id.lower() in normalized_allowed_users:
                return AccessDecision(
                    is_allowed=True,
                    reason=DecisionReason.USER_WHITELIST,
                    resource_id=target_id,
                    matched_identity=sec_ctx.user_id,
                    details=f"Access granted via direct user whitelist matching '{sec_ctx.user_id}'.",
                )

        # 4. Role Match (with hierarchical expansion)
        if perms.allowed_roles:
            normalized_allowed_roles = set(r.lower() for r in perms.allowed_roles)
            matching_roles = sec_ctx.effective_roles.intersection(normalized_allowed_roles)
            if matching_roles:
                matched_role = sorted(matching_roles)[0]
                return AccessDecision(
                    is_allowed=True,
                    reason=DecisionReason.ROLE_MATCH,
                    resource_id=target_id,
                    matched_identity=matched_role,
                    details=f"Access granted via matching effective role '{matched_role}'.",
                )

        # 5. Group Match (with hierarchical expansion)
        if perms.allowed_groups:
            normalized_allowed_groups = set(g.lower() for g in perms.allowed_groups)
            matching_groups = sec_ctx.effective_groups.intersection(normalized_allowed_groups)
            if matching_groups:
                matched_group = sorted(matching_groups)[0]
                return AccessDecision(
                    is_allowed=True,
                    reason=DecisionReason.GROUP_MATCH,
                    resource_id=target_id,
                    matched_identity=matched_group,
                    details=f"Access granted via matching effective group '{matched_group}'.",
                )

        # 6. Parent Resource Permission Inheritance
        if perms.parent_id and self.parent_permission_resolver:
            parent_perms = self.parent_permission_resolver(perms.parent_id)
            if parent_perms:
                parent_decision = self.evaluate_access(sec_ctx, parent_perms, resource_id=perms.parent_id)
                if parent_decision.is_allowed:
                    return AccessDecision(
                        is_allowed=True,
                        reason=DecisionReason.INHERITED_ALLOW,
                        resource_id=target_id,
                        matched_identity=parent_decision.matched_identity,
                        details=f"Access granted via inherited permissions from parent '{perms.parent_id}'.",
                    )

        # 7. Denied
        return AccessDecision(
            is_allowed=False,
            reason=DecisionReason.DENIED,
            resource_id=target_id,
            details=f"Access denied: User '{sec_ctx.user_id}' with effective roles {sorted(sec_ctx.effective_roles)} and groups {sorted(sec_ctx.effective_groups)} does not satisfy resource requirements.",
        )

    # ── Batch Filtering & Redaction ──────────────────────────────────────────

    def filter_candidates(
        self,
        context: Union[UserSecurityContext, Dict[str, Any], None],
        items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Filters a list of candidate dictionaries, retaining only items authorized for the user.
        """
        sec_ctx = self.resolve_context(context)
        authorized_items: List[Dict[str, Any]] = []

        for item in items:
            decision = self.evaluate_access(sec_ctx, item)
            if decision.is_allowed:
                authorized_items.append(item)

        return authorized_items


# Global singleton instance
_default_rbac_resolver: Optional[RBACResolver] = None


def get_default_rbac_resolver() -> RBACResolver:
    """Returns the shared global RBACResolver instance."""
    global _default_rbac_resolver
    if _default_rbac_resolver is None:
        _default_rbac_resolver = RBACResolver()
    return _default_rbac_resolver
