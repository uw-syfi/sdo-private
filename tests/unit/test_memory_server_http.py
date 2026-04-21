"""Integration tests for the memory server HTTP/SSE daemon."""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING

import pytest
import requests

if TYPE_CHECKING:
    from pathlib import Path

from sregym_agents.cli_agent.memory_server.http_server import MemoryHTTPDaemon
from sregym_agents.cli_agent.memory_server.server import MemoryMCPServer
from sregym_agents.cli_agent.memory_server.store import IncidentStore


@pytest.fixture
def daemon(tmp_path: Path):
    store = IncidentStore(tmp_path / "test.db")
    mcp = MemoryMCPServer(store)
    d = MemoryHTTPDaemon(mcp, host="127.0.0.1", port=0)
    t = threading.Thread(target=d.run, daemon=True)
    t.start()
    time.sleep(0.05)
    yield d
    d.stop()
    t.join(timeout=2)


@pytest.fixture
def base_url(daemon: MemoryHTTPDaemon) -> str:
    return f"http://127.0.0.1:{daemon.port}"


# ---------------------------------------------------------------------------
# Health and routing
# ---------------------------------------------------------------------------


def test_health(base_url: str):
    resp = requests.get(f"{base_url}/health", timeout=2)
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_unknown_route_404(base_url: str):
    resp = requests.get(f"{base_url}/unknown", timeout=2)
    assert resp.status_code == 404


def test_post_unknown_path_404(base_url: str):
    resp = requests.post(f"{base_url}/unknown", json={}, timeout=2)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SSE endpoint handshake
# ---------------------------------------------------------------------------


def test_sse_returns_event_stream(base_url: str):
    import socket as _socket

    port = int(base_url.rsplit(":", 1)[1])
    raw = b""
    try:
        with _socket.create_connection(("127.0.0.1", port), timeout=2) as s:
            s.sendall(b"GET /sse HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            s.settimeout(2)
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                raw += chunk
                if b"\r\n\r\n" in raw:
                    body_start = raw.index(b"\r\n\r\n") + 4
                    if b"\n\n" in raw[body_start:]:
                        break  # got HTTP headers + at least one SSE event
    except OSError:
        pass  # expected socket timeout after reading what we need

    assert b"200" in raw
    assert b"text/event-stream" in raw
    # Body uses chunked encoding; search for the SSE field strings directly.
    assert b"event: endpoint" in raw
    assert b"data: /messages?sessionId=" in raw


# ---------------------------------------------------------------------------
# Full JSON-RPC roundtrip
# ---------------------------------------------------------------------------


def _sse_rpc(base_url: str, request: dict) -> dict:
    """Send one JSON-RPC request via MCP SSE transport; return the response dict."""
    endpoint_ready = threading.Event()
    response_ready = threading.Event()
    endpoint_path: list[str] = []
    response_data: list[dict] = []

    def sse_reader():
        with requests.get(f"{base_url}/sse", stream=True, timeout=5) as resp:
            event_type = "message"
            for raw in resp.iter_lines(decode_unicode=True):
                if raw.startswith("event: "):
                    event_type = raw[7:]
                elif raw.startswith("data: "):
                    data = raw[6:]
                    if event_type == "endpoint":
                        endpoint_path.append(data)
                        endpoint_ready.set()
                        event_type = "message"
                    elif event_type == "message":
                        response_data.append(json.loads(data))
                        response_ready.set()
                        return

    t = threading.Thread(target=sse_reader, daemon=True)
    t.start()
    assert endpoint_ready.wait(timeout=2), "SSE endpoint event not received"

    resp = requests.post(
        f"{base_url}{endpoint_path[0]}",
        json=request,
        timeout=2,
    )
    assert resp.status_code == 202
    assert response_ready.wait(timeout=2), "SSE response not received"
    return response_data[0]


def test_initialize(base_url: str):
    resp = _sse_rpc(base_url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "incident_memory"


def test_tools_list(base_url: str):
    resp = _sse_rpc(base_url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "recall_incident" in names
    assert "store_incident" in names


def test_recall_empty(base_url: str):
    resp = _sse_rpc(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "recall_incident", "arguments": {"query": "cart 503 valkey"}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "No similar past incident" in text


def test_store_then_recall(base_url: str):
    _sse_rpc(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "store_incident",
                "arguments": {
                    "app": "astronomy-shop",
                    "symptoms": "cart returning 503",
                    "key_checks": "kubectl get pods → crashloop",
                    "root_causes": "valkey requirepass missing",
                    "fix": "added REDIS_PASSWORD",
                    "lesson": "check valkey auth first",
                },
            },
        },
    )
    resp = _sse_rpc(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "recall_incident", "arguments": {"query": "cart 503 valkey auth"}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "astronomy-shop" in text
    assert "check valkey auth first" in text


def test_post_without_session_returns_202(base_url: str):
    resp = requests.post(
        f"{base_url}/messages?sessionId=nonexistent",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        timeout=2,
    )
    assert resp.status_code == 202


def test_post_invalid_json_returns_400(base_url: str):
    resp = requests.post(
        f"{base_url}/messages",
        data=b"not-json",
        headers={"Content-Type": "application/json"},
        timeout=2,
    )
    assert resp.status_code == 400


def test_concurrent_sessions(base_url: str):
    """Two independent sessions can exchange messages without cross-talk."""
    results: list[dict] = [None, None]  # type: ignore[list-item]

    def session(idx: int, query: str):
        results[idx] = _sse_rpc(
            base_url,
            {"jsonrpc": "2.0", "id": idx, "method": "tools/list", "params": {}},
        )

    t0 = threading.Thread(target=session, args=(0, "q0"))
    t1 = threading.Thread(target=session, args=(1, "q1"))
    t0.start()
    t1.start()
    t0.join(timeout=5)
    t1.join(timeout=5)

    assert results[0] is not None
    assert "result" in results[0]
    assert results[1] is not None
    assert "result" in results[1]


# ---------------------------------------------------------------------------
# store-only mode (?store_only=1)
# ---------------------------------------------------------------------------


def _sse_rpc_store_only(base_url: str, request: dict) -> dict:
    """Like _sse_rpc but connects to /sse?store_only=1."""
    endpoint_ready = threading.Event()
    response_ready = threading.Event()
    endpoint_path: list[str] = []
    response_data: list[dict] = []

    def sse_reader():
        with requests.get(f"{base_url}/sse?store_only=1", stream=True, timeout=5) as resp:
            event_type = "message"
            for raw in resp.iter_lines(decode_unicode=True):
                if raw.startswith("event: "):
                    event_type = raw[7:]
                elif raw.startswith("data: "):
                    data = raw[6:]
                    if event_type == "endpoint":
                        endpoint_path.append(data)
                        endpoint_ready.set()
                        event_type = "message"
                    elif event_type == "message":
                        response_data.append(json.loads(data))
                        response_ready.set()
                        return

    t = threading.Thread(target=sse_reader, daemon=True)
    t.start()
    assert endpoint_ready.wait(timeout=2), "SSE endpoint event not received"

    resp = requests.post(
        f"{base_url}{endpoint_path[0]}",
        json=request,
        timeout=2,
    )
    assert resp.status_code == 202
    assert response_ready.wait(timeout=2), "SSE response not received"
    return response_data[0]


def test_store_only_tools_list(base_url: str):
    resp = _sse_rpc_store_only(base_url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "store_incident" in names
    assert "recall_incident" not in names


def test_store_only_recall_blocked(base_url: str):
    resp = _sse_rpc_store_only(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "recall_incident", "arguments": {"query": "cart 503"}},
        },
    )
    assert "error" in resp
    assert "store-only" in resp["error"]["message"]


def test_store_only_store_works(base_url: str):
    resp = _sse_rpc_store_only(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "store_incident",
                "arguments": {
                    "app": "test-app",
                    "symptoms": "pod crashloop",
                    "key_checks": "kubectl describe pod",
                    "root_causes": "missing config",
                    "fix": "added env var",
                    "lesson": "check env vars first",
                },
            },
        },
    )
    assert "result" in resp
    assert "Incident stored" in resp["result"]["content"][0]["text"]


def test_store_only_does_not_affect_full_session(base_url: str):
    """A store-only session and a full session on the same daemon are independent."""
    _sse_rpc_store_only(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "store_incident",
                "arguments": {
                    "app": "astronomy-shop",
                    "symptoms": "cart 503",
                    "key_checks": "kubectl get pods",
                    "root_causes": "valkey auth missing",
                    "fix": "added password",
                    "lesson": "check valkey auth",
                },
            },
        },
    )
    # Full-mode session can recall the incident stored above.
    resp = _sse_rpc(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "recall_incident", "arguments": {"query": "cart 503 valkey"}},
        },
    )
    assert "astronomy-shop" in resp["result"]["content"][0]["text"]
    # Full-mode session also lists both tools.
    list_resp = _sse_rpc(base_url, {"jsonrpc": "2.0", "id": 12, "method": "tools/list", "params": {}})
    names = {t["name"] for t in list_resp["result"]["tools"]}
    assert "recall_incident" in names
    assert "store_incident" in names
