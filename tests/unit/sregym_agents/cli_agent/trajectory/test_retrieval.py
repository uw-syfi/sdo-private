"""Unit tests for the trajectory retrieval MCP tools (LLM + RAG modes)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import Client
from mcp.types import TextContent

from sregym_agents.cli_agent.trajectory.retrieval import build_trajectory_mcp
from sregym_agents.cli_agent.trajectory.store import (
    TrajectoryMeta,
    TrajectoryRecorder,
    TrajectoryStore,
)

if TYPE_CHECKING:
    from pathlib import Path

APP = "hotelReservation"


def _text(content: object) -> str:
    assert isinstance(content, TextContent)
    return content.text


def _seed(store: TrajectoryStore) -> None:
    incidents = [
        ("missing_db_host", "frontend 503 profile pod crashloopbackoff after rollout", "set DB_HOST env"),
        ("cpu_throttle", "reservation latency cpu throttling under load", "raise cpu limit"),
        ("dup_pvc", "mongodb containercreating duplicate pvc multi-attach", "remove duplicate mount"),
    ]
    for i, (pid, task, outcome) in enumerate(incidents):
        w = store.open_run(TrajectoryMeta(app=APP, problem_id=pid, ts=f"2026053{i}_000000", task=task))
        rec = TrajectoryRecorder(w)
        rec.on_tool_call("bash", "kubectl get pods")
        rec.on_thinking(task)
        w.close(outcome=outcome)


async def _call(server, name: str, args: dict) -> str:
    async with Client(server) as client:
        result = await client.call_tool(name, args)
    return _text(result.content[0])


def _find(store: TrajectoryStore, needle: str):
    """A trajectory is identified by its recorded content, not the hidden problem_id."""
    return next(d for d in store.digests(APP) if needle in d.text)


async def test_rag_ranks_relevant_incident_first(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    _seed(store)
    server = build_trajectory_mcp(store, APP)  # offline HashingEmbedder
    out = await _call(
        server,
        "search_trajectories",
        {"query": "frontend 503s and a profile pod crash looping after a deploy", "k": 1},
    )
    assert "DB_HOST" in out  # the db_host run's outcome (only top-1 returned)
    assert "cpu limit" not in out
    assert "similarity=" in out
    assert "wrong_service" not in out  # no problem_id leak in the surfaced text


async def test_empty_store_returns_no_match(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    server = build_trajectory_mcp(store, APP)
    out = await _call(server, "search_trajectories", {"query": "anything", "k": 3})
    assert "No past trajectories" in out


async def test_llm_mode_invokes_searcher_with_candidates(tmp_path: Path) -> None:
    """LLM mode routes the query + candidate digests through the injected searcher."""
    store = TrajectoryStore(tmp_path)
    _seed(store)
    seen: dict[str, object] = {}

    def fake_searcher(query, digests, st):
        seen["query"] = query
        seen["n"] = len(digests)
        return f"LLM picked run {digests[0].run_id}: relevant to {query}"

    server = build_trajectory_mcp(store, APP, mode="llm", searcher=fake_searcher)
    out = await _call(server, "search_trajectories", {"query": "frontend 503 crashloop"})
    assert seen["query"] == "frontend 503 crashloop"
    assert seen["n"] == 3  # all candidates handed to the LLM searcher
    assert "LLM picked run" in out


async def test_llm_mode_empty_store_returns_no_match(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    called = {"n": 0}

    def fake_searcher(query, digests, st):
        called["n"] += 1
        return "should not be called"

    server = build_trajectory_mcp(store, APP, mode="llm", searcher=fake_searcher)
    out = await _call(server, "search_trajectories", {"query": "anything"})
    assert "No past trajectories" in out
    assert called["n"] == 0  # searcher skipped when there is nothing to search


async def test_rag_excludes_in_flight_run(tmp_path: Path) -> None:
    """A run still being recorded (its file given as exclude_path) is hidden."""
    store = TrajectoryStore(tmp_path)
    _seed(store)
    target = _find(store, "crashloopbackoff")
    server = build_trajectory_mcp(store, APP, exclude_path=target.path)
    out = await _call(
        server,
        "search_trajectories",
        {"query": "frontend 503 profile crashloop after deploy", "k": 5},
    )
    assert "DB_HOST" not in out


async def test_read_trajectory_drilldown(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    _seed(store)
    target = _find(store, "crashloopbackoff")
    server = build_trajectory_mcp(store, APP)
    out = await _call(server, "read_trajectory", {"path": target.path})
    assert "kubectl get pods" in out
    assert "set DB_HOST env" in out


async def test_read_trajectory_missing_path(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    server = build_trajectory_mcp(store, APP)
    out = await _call(server, "read_trajectory", {"path": str(tmp_path / "nope.jsonl")})
    assert "No trajectory found" in out
