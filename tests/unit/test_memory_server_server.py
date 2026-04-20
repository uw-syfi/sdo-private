"""Unit tests for sregym_agents.cli_agent.memory_server.server."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sregym_agents.cli_agent.memory_server.server import MemoryMCPServer, _format_incident
from sregym_agents.cli_agent.memory_server.store import IncidentCase, IncidentStore


@pytest.fixture
def store(tmp_path: Path) -> IncidentStore:
    return IncidentStore(tmp_path / "test.db")


@pytest.fixture
def server(store: IncidentStore) -> MemoryMCPServer:
    return MemoryMCPServer(store)


# --- _format_incident ---


def test_format_incident():
    case = IncidentCase(
        id=1,
        app="astronomy-shop",
        symptoms="cart 503",
        key_checks="kubectl get pods → crashloop",
        root_causes="missing REDIS_PASSWORD",
        fix="added env var",
        lesson="check valkey auth first",
        created_at="2026-01-01 00:00:00",
    )
    text = _format_incident(case)
    assert "astronomy-shop" in text
    assert "cart 503" in text
    assert "missing REDIS_PASSWORD" in text
    assert "check valkey auth first" in text
    assert "for reference only" in text


# --- _dispatch ---


def test_initialize(server: MemoryMCPServer):
    resp = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp is not None
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "incident_memory"
    assert "tools" in result["capabilities"]


def test_initialized_notification_no_response(server: MemoryMCPServer):
    resp = server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None


def test_tools_list(server: MemoryMCPServer):
    resp = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert resp is not None
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "recall_incident" in names
    assert "store_incident" in names


def test_unknown_method(server: MemoryMCPServer):
    resp = server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "unknown/method"})
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32601


# --- tools/call: recall_incident ---


def test_recall_no_incidents(server: MemoryMCPServer):
    resp = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "recall_incident", "arguments": {"query": "cart 503 valkey"}},
        }
    )
    assert resp is not None
    text = resp["result"]["content"][0]["text"]
    assert "No similar past incident" in text


def test_recall_returns_incident(server: MemoryMCPServer, store: IncidentStore):
    store.store(
        app="astronomy-shop",
        symptoms="cart 503",
        key_checks="kubectl → crashloop",
        root_causes="missing REDIS_PASSWORD",
        fix="added REDIS_PASSWORD",
        lesson="check valkey auth first",
    )
    resp = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "recall_incident", "arguments": {"query": "cart 503 valkey auth"}},
        }
    )
    assert resp is not None
    text = resp["result"]["content"][0]["text"]
    assert "astronomy-shop" in text
    assert "check valkey auth first" in text


# --- tools/call: store_incident ---


def test_store_incident_tool(server: MemoryMCPServer, store: IncidentStore):
    resp = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "store_incident",
                "arguments": {
                    "app": "astronomy-shop",
                    "symptoms": "cart 503",
                    "key_checks": "kubectl get pods",
                    "root_causes": "valkey auth",
                    "fix": "added REDIS_PASSWORD",
                    "lesson": "check valkey auth first",
                },
            },
        }
    )
    assert resp is not None
    text = resp["result"]["content"][0]["text"]
    assert "stored" in text.lower()
    # Verify it's actually in the store
    case = store.retrieve("cart 503 valkey")
    assert case is not None
    assert case.app == "astronomy-shop"


def test_store_incident_deduplicates_without_merge_model(store: IncidentStore):
    # Pre-populate with a very similar incident so find_duplicate triggers
    store.store(
        app="astronomy-shop",
        symptoms="cart 503",
        key_checks="kubectl get pods → crashloop",
        root_causes="missing REDIS_PASSWORD",
        fix="added REDIS_PASSWORD",
        lesson="check valkey auth first",
    )
    server = MemoryMCPServer(store, merge_model=None)
    resp = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "store_incident",
                "arguments": {
                    "app": "astronomy-shop",
                    "symptoms": "cart 503",
                    "key_checks": "kubectl get pods → crashloop",
                    "root_causes": "missing REDIS_PASSWORD",
                    "fix": "added REDIS_PASSWORD",
                    "lesson": "refined: check valkey requirepass before env vars",
                },
            },
        }
    )
    assert resp is not None
    text = resp["result"]["content"][0]["text"]
    assert "already in memory" in text or "skipped" in text
    # Row count must still be 1 — no duplicate inserted
    import sqlite3

    with sqlite3.connect(store.db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    assert count == 1


def test_store_incident_merges_with_llm(store: IncidentStore):
    store.store(
        app="astronomy-shop",
        symptoms="cart 503",
        key_checks="kubectl get pods → crashloop",
        root_causes="missing REDIS_PASSWORD",
        fix="added REDIS_PASSWORD",
        lesson="check valkey auth first",
    )
    merged_fields = {
        "key_checks": "merged key checks",
        "root_causes": "merged root causes",
        "fix": "merged fix",
        "lesson": "merged lesson",
    }
    server = MemoryMCPServer(store, merge_model="claude-haiku-4-5")
    with patch(
        "sregym_agents.cli_agent.memory_server.server._llm_merge",
        return_value=merged_fields,
    ):
        resp = server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "store_incident",
                    "arguments": {
                        "app": "astronomy-shop",
                        "symptoms": "cart 503",
                        "key_checks": "kubectl get pods → crashloop",
                        "root_causes": "missing REDIS_PASSWORD",
                        "fix": "added REDIS_PASSWORD",
                        "lesson": "refined lesson",
                    },
                },
            }
        )
    assert resp is not None
    text = resp["result"]["content"][0]["text"]
    assert "merged" in text.lower()
    case = store.retrieve("cart 503 valkey")
    assert case is not None
    assert case.lesson == "merged lesson"
    assert case.key_checks == "merged key checks"


def test_store_incident_llm_failure_skips(store: IncidentStore):
    store.store(
        app="astronomy-shop",
        symptoms="cart 503",
        key_checks="kubectl get pods → crashloop",
        root_causes="missing REDIS_PASSWORD",
        fix="added REDIS_PASSWORD",
        lesson="original lesson",
    )
    server = MemoryMCPServer(store, merge_model="some-model")
    with patch(
        "sregym_agents.cli_agent.memory_server.server._llm_merge",
        return_value=None,  # simulate failure
    ):
        resp = server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "store_incident",
                    "arguments": {
                        "app": "astronomy-shop",
                        "symptoms": "cart 503",
                        "key_checks": "kubectl get pods → crashloop",
                        "root_causes": "missing REDIS_PASSWORD",
                        "fix": "added REDIS_PASSWORD",
                        "lesson": "new lesson",
                    },
                },
            }
        )
    assert resp is not None
    # Existing record should be unchanged
    case = store.retrieve("cart 503")
    assert case is not None
    assert case.lesson == "original lesson"


def test_unknown_tool(server: MemoryMCPServer):
    resp = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
    )
    assert resp is not None
    assert "error" in resp
    assert resp["error"]["code"] == -32601


# --- run() loop ---


def test_run_handles_invalid_json(server: MemoryMCPServer, capsys):
    lines = "not-json\n"
    with patch("sys.stdin", iter(lines.splitlines(keepends=True))):
        server.run()
    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert parsed["error"]["code"] == -32700


def test_run_handles_valid_request(server: MemoryMCPServer, capsys):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}) + "\n"
    with patch("sys.stdin", iter([req])):
        server.run()
    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert "tools" in parsed["result"]
