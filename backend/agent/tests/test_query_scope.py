"""Unit tests for Query Scope Modifier parser."""

import pytest
from backend.agent.query_scope import parse_query_scope


def test_parse_query_scope_general():
    cleaned, scope, connector = parse_query_scope("@general what is the meaning of authentication?")
    assert cleaned == "what is the meaning of authentication?"
    assert scope == "general"
    assert connector is None

    cleaned, scope, connector = parse_query_scope("/llm explain quicksort")
    assert cleaned == "explain quicksort"
    assert scope == "general"
    assert connector is None


def test_parse_query_scope_enterprise():
    cleaned, scope, connector = parse_query_scope("@enterprise how do I failover postgres?")
    assert cleaned == "how do I failover postgres?"
    assert scope == "enterprise"
    assert connector is None

    cleaned, scope, connector = parse_query_scope("@docs SRE disaster recovery runbook")
    assert cleaned == "SRE disaster recovery runbook"
    assert scope == "enterprise"
    assert connector is None


def test_parse_query_scope_connectors():
    for conn in ["github", "jira", "notion", "dropbox", "gmail", "confluence"]:
        cleaned, scope, connector = parse_query_scope(f"@{conn} find related tickets")
        assert cleaned == "find related tickets"
        assert scope == "connector"
        assert connector == conn


def test_parse_query_scope_web():
    cleaned, scope, connector = parse_query_scope("@web what is the latest version of LangGraph?")
    assert cleaned == "what is the latest version of LangGraph?"
    assert scope == "web"
    assert connector is None


def test_parse_query_scope_no_modifier():
    cleaned, scope, connector = parse_query_scope("What caused bug PAY-928?")
    assert cleaned == "What caused bug PAY-928?"
    assert scope is None
    assert connector is None


def test_parse_query_scope_empty():
    assert parse_query_scope("") == ("", None, None)
    assert parse_query_scope("   ") == ("", None, None)
