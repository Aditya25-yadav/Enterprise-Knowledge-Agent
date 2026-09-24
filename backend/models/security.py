"""
Security and RBAC Data Models for Enterprise Knowledge Agent (Phase 8).

Defines:
  1. UserSecurityContext: Represents an authenticated user's identity, roles, groups, and attributes.
  2. AccessDecision: Result of an access authorization evaluation with diagnostic reasoning.
  3. ResourcePermissions: Unified permission descriptor for OKF concepts, chunks, and graph nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class DecisionReason(str, Enum):
    """Reason code for an RBAC authorization evaluation decision."""
    PUBLIC = "PUBLIC"
    SUPERADMIN_BYPASS = "SUPERADMIN_BYPASS"
    USER_WHITELIST = "USER_WHITELIST"
    ROLE_MATCH = "ROLE_MATCH"
    GROUP_MATCH = "GROUP_MATCH"
    TENANT_MATCH = "TENANT_MATCH"
    INHERITED_ALLOW = "INHERITED_ALLOW"
    DENIED = "DENIED"


@dataclass
class UserSecurityContext:
    """
    Encapsulates the authenticated caller's identity and security attributes.
    """
    user_id: Optional[str] = None
    roles: List[str] = field(default_factory=lambda: ["employee"])
    groups: List[str] = field(default_factory=list)
    tenants: List[str] = field(default_factory=lambda: ["default"])
    attributes: Dict[str, Any] = field(default_factory=dict)
    is_superadmin: bool = False

    # Resolved effective roles and groups (expanded via hierarchies)
    effective_roles: Set[str] = field(default_factory=set)
    effective_groups: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.effective_roles:
            self.effective_roles = set(r.lower() for r in self.roles)
        else:
            self.effective_roles = set(r.lower() for r in self.effective_roles)

        if not self.effective_groups:
            self.effective_groups = set(g.lower() for g in self.groups)
        else:
            self.effective_groups = set(g.lower() for g in self.effective_groups)

    def has_role(self, role: str) -> bool:
        """Checks whether the user possesses the specified role directly or via inheritance."""
        return self.is_superadmin or role.lower() in self.effective_roles

    def in_group(self, group: str) -> bool:
        """Checks whether the user belongs to the specified group directly or via hierarchy."""
        return self.is_superadmin or group.lower() in self.effective_groups

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "roles": list(self.roles),
            "groups": list(self.groups),
            "tenants": list(self.tenants),
            "attributes": self.attributes,
            "is_superadmin": self.is_superadmin,
            "effective_roles": list(self.effective_roles),
            "effective_groups": list(self.effective_groups),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> UserSecurityContext:
        """Constructs UserSecurityContext from arbitrary dictionary payload or agent context."""
        if not data:
            return cls(roles=["guest"], user_id="anonymous")

        roles = data.get("roles") or ["employee"]
        if isinstance(roles, str):
            roles = [roles]

        groups = data.get("groups") or []
        if isinstance(groups, str):
            groups = [groups]

        tenants = data.get("tenants") or ["default"]
        if isinstance(tenants, str):
            tenants = [tenants]

        user_id = data.get("user_id") or data.get("email") or data.get("username")
        is_superadmin = bool(data.get("is_superadmin") or data.get("is_admin", False) and "admin" in roles)

        return cls(
            user_id=user_id,
            roles=list(roles),
            groups=list(groups),
            tenants=list(tenants),
            attributes=data.get("attributes", {}),
            is_superadmin=is_superadmin,
        )


@dataclass
class ResourcePermissions:
    """
    Unified access control descriptor for documents, chunks, and graph entities.
    """
    is_public: bool = True
    allowed_roles: List[str] = field(default_factory=lambda: ["employee"])
    allowed_users: List[str] = field(default_factory=list)
    allowed_groups: List[str] = field(default_factory=list)
    tenant_id: Optional[str] = "default"
    parent_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_public": self.is_public,
            "allowed_roles": list(self.allowed_roles),
            "allowed_users": list(self.allowed_users),
            "allowed_groups": list(self.allowed_groups),
            "tenant_id": self.tenant_id,
            "parent_id": self.parent_id,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> ResourcePermissions:
        """Extracts ResourcePermissions from OKFConcept dict, SmartChunk payload, or graph node."""
        perms = payload.get("permissions")
        if isinstance(perms, dict):
            return cls(
                is_public=bool(perms.get("is_public", False)),
                allowed_roles=list(perms.get("allowed_roles")) if perms.get("allowed_roles") is not None else ["employee"],
                allowed_users=list(perms.get("allowed_users") or []),
                allowed_groups=list(perms.get("allowed_groups") or []),
                tenant_id=payload.get("tenant_id") or "default",
                parent_id=payload.get("parent_id") or payload.get("extra_metadata", {}).get("parent_id"),
            )

        # Fallback to top-level fields
        has_explicit_roles = "allowed_roles" in payload and payload["allowed_roles"] is not None
        allowed_roles = list(payload["allowed_roles"]) if has_explicit_roles else ["employee"]

        return cls(
            is_public=bool(payload.get("is_public", True)),
            allowed_roles=allowed_roles,
            allowed_users=list(payload.get("allowed_users") or []),
            allowed_groups=list(payload.get("allowed_groups") or []),
            tenant_id=payload.get("tenant_id") or "default",
            parent_id=payload.get("parent_id"),
        )


@dataclass
class AccessDecision:
    """
    Result of an authorization check against a resource.
    """
    is_allowed: bool
    reason: DecisionReason
    resource_id: Optional[str] = None
    matched_identity: Optional[str] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_allowed": self.is_allowed,
            "reason": self.reason.value,
            "resource_id": self.resource_id,
            "matched_identity": self.matched_identity,
            "details": self.details,
        }
