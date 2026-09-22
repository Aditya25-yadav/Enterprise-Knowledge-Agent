"""
Security & Role-Based Access Control (RBAC) Module for Enterprise Knowledge Agent (Phase 8).
"""

from backend.models.security import (
    AccessDecision,
    DecisionReason,
    ResourcePermissions,
    UserSecurityContext,
)
from backend.security.hierarchy import GroupHierarchy, RoleHierarchy
from backend.security.rbac_resolver import (
    RBACResolver,
    get_default_rbac_resolver,
)
from backend.security.translators import (
    BM25FilterTranslator,
    CypherRBACClauseBuilder,
    GraphNodeFilter,
    QdrantFilterTranslator,
)

__all__ = [
    "UserSecurityContext",
    "ResourcePermissions",
    "AccessDecision",
    "DecisionReason",
    "RoleHierarchy",
    "GroupHierarchy",
    "RBACResolver",
    "get_default_rbac_resolver",
    "QdrantFilterTranslator",
    "BM25FilterTranslator",
    "CypherRBACClauseBuilder",
    "GraphNodeFilter",
]
