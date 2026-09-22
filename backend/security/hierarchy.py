"""
Role and Group Hierarchy Expansion Engines for Enterprise RBAC (Phase 8).

Provides:
  1. RoleHierarchy: Directed Acyclic Graph (DAG) for role inheritance.
  2. GroupHierarchy: Nested organizational group and team hierarchy expansion.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set


class RoleHierarchy:
    """
    Manages enterprise role relationships and resolves transitive permissions.
    If Role A inherits Role B, a user with Role A automatically possesses Role B.
    """

    DEFAULT_ROLE_GRAPH: Dict[str, List[str]] = {
        "superadmin": ["admin", "security-admin", "ciso", "developer", "engineer", "employee", "guest"],
        "admin": ["security-admin", "developer", "engineer", "employee", "guest"],
        "security-admin": ["engineer", "employee", "guest"],
        "ciso": ["security-admin", "employee", "guest"],
        "lead": ["developer", "engineer", "employee", "guest"],
        "developer": ["engineer", "employee", "guest"],
        "engineer": ["employee", "guest"],
        "devops": ["engineer", "employee", "guest"],
        "secops": ["security-admin", "engineer", "employee", "guest"],
        "employee": ["guest"],
        "contractor": ["guest"],
        "guest": [],
    }

    def __init__(self, custom_graph: Optional[Dict[str, List[str]]] = None) -> None:
        self._graph: Dict[str, Set[str]] = {}
        base_graph = custom_graph or self.DEFAULT_ROLE_GRAPH
        for parent, children in base_graph.items():
            self._graph[parent.lower()] = set(c.lower() for c in children)

    def add_inheritance(self, parent_role: str, child_role: str) -> None:
        """Declares that parent_role inherits all permissions of child_role."""
        p = parent_role.lower()
        c = child_role.lower()
        if p not in self._graph:
            self._graph[p] = set()
        self._graph[p].add(c)

    def expand_roles(self, roles: Iterable[str]) -> Set[str]:
        """
        Calculates the complete transitive closure of effective roles.
        """
        expanded: Set[str] = set()
        queue: List[str] = [r.lower() for r in roles]

        while queue:
            role = queue.pop(0)
            if role not in expanded:
                expanded.add(role)
                children = self._graph.get(role, set())
                for child in children:
                    if child not in expanded:
                        queue.append(child)

        return expanded


class GroupHierarchy:
    """
    Manages nested team, group, and organizational department memberships.
    If Group X is a sub-group of Group Y, membership in Group X implies membership in Group Y.
    """

    DEFAULT_GROUP_GRAPH: Dict[str, List[str]] = {
        "payments-core": ["payments-team", "engineering", "all-company"],
        "payments-infra": ["payments-team", "infrastructure", "engineering", "all-company"],
        "payments-team": ["engineering", "all-company"],
        "checkout-team": ["engineering", "all-company"],
        "secops-team": ["security", "engineering", "all-company"],
        "security": ["all-company"],
        "infrastructure": ["engineering", "all-company"],
        "engineering": ["all-company"],
        "all-company": [],
    }

    def __init__(self, custom_graph: Optional[Dict[str, List[str]]] = None) -> None:
        self._graph: Dict[str, Set[str]] = {}
        base_graph = custom_graph or self.DEFAULT_GROUP_GRAPH
        for child, parents in base_graph.items():
            self._graph[child.lower()] = set(p.lower() for p in parents)

    def add_parent_group(self, child_group: str, parent_group: str) -> None:
        """Declares that child_group is a member / subgroup of parent_group."""
        c = child_group.lower()
        p = parent_group.lower()
        if c not in self._graph:
            self._graph[c] = set()
        self._graph[c].add(p)

    def expand_groups(self, groups: Iterable[str]) -> Set[str]:
        """
        Calculates the complete set of effective parent groups.
        """
        expanded: Set[str] = set()
        queue: List[str] = [g.lower() for g in groups]

        while queue:
            group = queue.pop(0)
            if group not in expanded:
                expanded.add(group)
                parents = self._graph.get(group, set())
                for parent in parents:
                    if parent not in expanded:
                        queue.append(parent)

        return expanded
