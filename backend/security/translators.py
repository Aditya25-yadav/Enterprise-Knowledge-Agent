"""
Database-Native Pre-Filter Translators for Enterprise RBAC (Phase 8).

Translates UserSecurityContext into database-native pre-filtering conditions across:
  1. Qdrant Vector Store (`rest.Filter`)
  2. BM25 / SQLite Index (Boolean predicate)
  3. Neo4j Property Graph (Parameterized Cypher WHERE clauses)
  4. In-Memory Graph Nodes (Predicate evaluator)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.models.graph import GraphNode
from backend.models.security import UserSecurityContext
from backend.security.rbac_resolver import RBACResolver, get_default_rbac_resolver


class QdrantFilterTranslator:
    """
    Translates UserSecurityContext and search parameters into a native Qdrant Filter.
    """

    @staticmethod
    def build_filter(
        context: UserSecurityContext,
        source: Optional[str] = None,
        resource_type: Optional[str] = None,
        tenant_id: Optional[str] = None,
        extra_filters: Optional[List[Any]] = None,
    ) -> Any:
        """
        Builds a qdrant_client.http.models.Filter with strict RBAC pre-filtering clauses.
        """
        from qdrant_client.http import models as rest

        must_conditions: List[rest.Condition] = []

        # 1. Source platform filter
        if source:
            must_conditions.append(
                rest.FieldCondition(
                    key="source",
                    match=rest.MatchValue(value=source.lower()),
                )
            )

        # 2. Resource type filter
        if resource_type:
            must_conditions.append(
                rest.FieldCondition(
                    key="resource_type",
                    match=rest.MatchValue(value=resource_type.lower()),
                )
            )

        # 3. Tenant filter
        if tenant_id and tenant_id != "all":
            must_conditions.append(
                rest.FieldCondition(
                    key="tenant_id",
                    match=rest.MatchValue(value=tenant_id),
                )
            )

        # 4. Extra custom filters
        if extra_filters:
            must_conditions.extend(extra_filters)

        # 5. RBAC Pre-Filter clauses (unless superadmin)
        if not context.is_superadmin:
            rbac_should_clauses: List[rest.Condition] = [
                # Public content accessible to all
                rest.FieldCondition(key="is_public", match=rest.MatchValue(value=True)),
            ]

            if context.effective_roles:
                rbac_should_clauses.append(
                    rest.FieldCondition(
                        key="allowed_roles",
                        match=rest.MatchAny(any=list(context.effective_roles)),
                    )
                )

            if context.user_id:
                rbac_should_clauses.append(
                    rest.FieldCondition(
                        key="allowed_users",
                        match=rest.MatchValue(value=context.user_id),
                    )
                )

            if context.effective_groups:
                rbac_should_clauses.append(
                    rest.FieldCondition(
                        key="allowed_groups",
                        match=rest.MatchAny(any=list(context.effective_groups)),
                    )
                )

            must_conditions.append(rest.Filter(should=rbac_should_clauses))

        return rest.Filter(must=must_conditions) if must_conditions else None


class BM25FilterTranslator:
    """
    Fast boolean predicate evaluator for BM25 and sparse keyword search candidates.
    """

    @staticmethod
    def create_predicate(
        context: UserSecurityContext,
        resolver: Optional[RBACResolver] = None,
    ) -> Callable[[Dict[str, Any]], bool]:
        """
        Returns a high-speed predicate function testing if a candidate chunk is authorized.
        """
        r = resolver or get_default_rbac_resolver()
        sec_ctx = r.resolve_context(context)

        def predicate(payload: Dict[str, Any]) -> bool:
            return r.evaluate_access(sec_ctx, payload).is_allowed

        return predicate


class CypherRBACClauseBuilder:
    """
    Builds parameterized Cypher WHERE clauses and parameter maps for Neo4j queries.
    """

    @staticmethod
    def build_clause(
        context: UserSecurityContext,
        node_variable: str = "n",
        param_prefix: str = "rbac_",
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Generates Cypher WHERE fragment and bound parameters.

        Returns:
          (where_clause, params) e.g.
          ("(n.is_public = true OR ANY(r IN n.allowed_roles WHERE r IN $rbac_roles) ...)", {...})
        """
        if context.is_superadmin:
            return "true", {}

        roles_key = f"{param_prefix}roles"
        user_key = f"{param_prefix}user_id"
        groups_key = f"{param_prefix}groups"

        params: Dict[str, Any] = {
            roles_key: list(context.effective_roles),
            user_key: context.user_id or "",
            groups_key: list(context.effective_groups),
        }

        clauses = [f"{node_variable}.is_public = true"]

        if context.effective_roles:
            clauses.append(f"ANY(r IN {node_variable}.allowed_roles WHERE r IN ${roles_key})")

        if context.user_id:
            clauses.append(f"${user_key} IN {node_variable}.allowed_users")

        if context.effective_groups:
            clauses.append(f"ANY(g IN {node_variable}.allowed_groups WHERE g IN ${groups_key})")

        combined = " OR ".join(clauses)
        return f"({combined})", params


class GraphNodeFilter:
    """
    In-memory GraphNode predicate evaluator for graph traversal and BFS operations.
    """

    @staticmethod
    def is_authorized(
        node: GraphNode,
        context: UserSecurityContext,
        resolver: Optional[RBACResolver] = None,
    ) -> bool:
        """
        Evaluates whether a GraphNode is accessible by the user security context.
        """
        r = resolver or get_default_rbac_resolver()
        payload = {
            "node_id": node.node_id,
            "is_public": node.properties.get("is_public", True),
            "allowed_roles": node.properties.get("allowed_roles") or [],
            "allowed_users": node.properties.get("allowed_users") or [],
            "allowed_groups": node.properties.get("allowed_groups") or [],
            "parent_id": node.properties.get("parent_id"),
        }
        return r.evaluate_access(context, payload).is_allowed
