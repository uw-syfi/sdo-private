"""Unit tests for the read-only recall MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client
from mcp.types import TextContent

from sregym_agents.cli_agent.memory.recall_server import RecallServer, build_recall_mcp
from sregym_agents.cli_agent.memory.store import CONFIRMED_VERDICT, LessonStore

if TYPE_CHECKING:
    from pathlib import Path


def _text(content: object) -> str:
    assert isinstance(content, TextContent)
    return content.text


async def _call_recall(store: LessonStore, app: str, situation: str = "frontend 503s") -> str:
    mcp = build_recall_mcp(store, app)
    async with Client(mcp) as client:
        result = await client.call_tool("recall", {"situation": situation})
    return _text(result.content[0])


async def test_recall_empty_store_returns_no_match(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    text = await _call_recall(store, "hotelReservation")
    assert "No past incidents" in text


async def test_recall_returns_lessons_with_framing(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    store.append(
        "hotelReservation",
        situation="frontend 503s; profile CrashLoopBackOff",
        root_cause="missing DB_HOST env var",
        tell="kubectl describe shows env unset",
        fix="set DB_HOST",
        affected_resource="deployment/profile.env",
        confirmed_by=CONFIRMED_VERDICT,
        obvious_guess="bad image tag",
    )
    text = await _call_recall(store, "hotelReservation")
    assert "hypothesis" in text.lower()  # framing present
    assert "missing DB_HOST env var" in text
    assert "bad image tag" in text  # obvious_guess surfaced


async def test_recall_filters_by_app(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    store.append(
        "socialNetwork",
        situation="s",
        root_cause="other-app cause",
        tell="t",
        fix="f",
        affected_resource="ar",
        confirmed_by=CONFIRMED_VERDICT,
    )
    text = await _call_recall(store, "hotelReservation")
    assert "other-app cause" not in text
    assert "No past incidents" in text


async def test_recall_only_exposes_read_tool(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    mcp = build_recall_mcp(store, "hotelReservation")
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools}
    assert names == {"recall"}  # no store_incident write tool


async def test_recall_server_serves_over_http(tmp_path: Path) -> None:
    """The in-process server the driver starts must answer over HTTP/SSE."""
    store = LessonStore(tmp_path)
    store.append(
        "hotelReservation",
        situation="frontend 503s",
        root_cause="missing DB_HOST env var",
        tell="env unset",
        fix="set DB_HOST",
        affected_resource="deployment/profile.env",
        confirmed_by=CONFIRMED_VERDICT,
    )
    server = RecallServer(store, "hotelReservation")
    server.start()
    try:
        async with Client(server.url) as client:
            result = await client.call_tool("recall", {"situation": "frontend 503s"})
        assert "missing DB_HOST env var" in _text(result.content[0])
    finally:
        server.stop()
