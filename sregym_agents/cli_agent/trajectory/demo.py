"""End-to-end demo: record a real agent run, then retrieve it two ways.

Run:  uv run python -m sregym_agents.cli_agent.trajectory.demo

What it proves, in order:
  1. RECORD — a real ``claude`` agent run is captured by ``TrajectoryRecorder``
     attached to the agentshim ``CodingAgent`` event stream, producing a JSONL
     trajectory on disk.
  2. SEED   — a few synthetic past incidents are written so retrieval has a
     corpus with a clear "right answer".
  3. LLM retrieval  — the ``search_trajectories`` MCP tool (mode="llm") returns
     compact digests for an agent's LLM to rank.
  4. RAG retrieval  — the ``search_trajectories`` MCP tool (mode="rag") embeds the
     query and returns the top-k by cosine similarity, with scores.
  5. DRILL-DOWN — ``read_trajectory`` returns the full transcript of the top hit.

The MCP tools are exercised in-memory via ``fastmcp.Client`` (fast, deterministic);
only step 1 makes a real provider call. If the provider call fails (no auth /
offline), step 1 is reported as skipped and the rest still runs.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastmcp import Client

from sregym_agents.cli_agent.trajectory.retrieval import build_trajectory_mcp
from sregym_agents.cli_agent.trajectory.store import (
    TrajectoryEvent,
    TrajectoryMeta,
    TrajectoryRecorder,
    TrajectoryStore,
)

APP = "hotelReservation"


def _rule(title: str) -> None:
    print("\n" + "=" * 72 + f"\n {title}\n" + "=" * 72)


def record_real_run(store: TrajectoryStore) -> bool:
    """Step 1: drive a real claude agent and record its trajectory."""
    _rule("1. RECORD — real claude agent run, captured via event handler")
    meta = TrajectoryMeta(
        app=APP,
        problem_id="demo_live_probe",
        ts="20260601_000000",
        model="claude-haiku-4-5-20251001",
        provider="claude",
        task="List the files in the current directory and say what kind of project this is.",
    )
    writer = store.open_run(meta)
    recorder = TrajectoryRecorder(writer)
    try:
        from agentshim import CodingAgent

        agent = CodingAgent(provider="claude", model=meta.model, event_handler=recorder)
        reply = agent.generate(meta.task, timeout=120, silent=True)
        writer.close(outcome="completed")
        print(f"agent replied ({len(reply)} chars). recorded file: {writer.path}")
        # Show what was captured.
        digest = store.digest(writer.path)
        kinds: dict[str, int] = {}
        for line in writer.path.read_text().splitlines():
            import json

            rec = json.loads(line)
            if rec.get("type") == "event":
                kinds[rec["kind"]] = kinds.get(rec["kind"], 0) + 1
        print(f"captured event kinds: {kinds}")
        print(f"digest tools: {digest.tool_calls if digest else '-'}")
        return True
    except Exception as exc:  # provider unavailable / offline
        writer.close(outcome="error")
        print(f"[skipped real run: {type(exc).__name__}: {exc}]")
        return False


def seed_corpus(store: TrajectoryStore) -> None:
    """Step 2: write synthetic past incidents to retrieve over."""
    _rule("2. SEED — synthetic past incidents for the corpus")
    incidents = [
        (
            "missing_db_host_env_profile",
            "frontend returns 503; profile pod in CrashLoopBackOff after a rollout",
            [
                ("kubectl get pods", "profile-7d9 CrashLoopBackOff; frontend 503"),
                ("kubectl logs profile-7d9", "panic: dial tcp: missing DB_HOST"),
                ("kubectl describe deploy/profile", "env: DB_HOST not set"),
            ],
            "root cause: DB_HOST env var missing on profile deployment after image bump; fix: set DB_HOST",
        ),
        (
            "cpu_throttle_reservation",
            "reservation latency spikes; requests time out under load",
            [
                ("kubectl top pods", "reservation pod at CPU limit, heavy throttling"),
                ("kubectl describe deploy/reservation", "resources.limits.cpu: 100m"),
            ],
            "root cause: CPU limit too low on reservation deployment; fix: raise cpu limit",
        ),
        (
            "duplicate_pvc_mounts_mongodb",
            "mongodb pod stuck ContainerCreating; volume multi-attach error",
            [
                ("kubectl get pods", "mongodb-0 ContainerCreating"),
                ("kubectl describe pod/mongodb-0", "Multi-Attach error: pvc mounted twice"),
            ],
            "root cause: duplicate PVC mounts on mongodb statefulset; fix: remove duplicate volume mount",
        ),
    ]
    for i, (pid, task, tools, outcome) in enumerate(incidents):
        meta = TrajectoryMeta(app=APP, problem_id=pid, ts=f"20260530_0{i}0000", task=task)
        w = store.open_run(meta)
        w.event(TrajectoryEvent(seq=w.next_seq(), kind="thinking", text=f"Investigating: {task}"))
        for tool, out in tools:
            w.event(TrajectoryEvent(seq=w.next_seq(), kind="tool_call", tool="bash", args=tool))
            w.event(TrajectoryEvent(seq=w.next_seq(), kind="tool_result", tool="bash", stdout=out))
        w.event(TrajectoryEvent(seq=w.next_seq(), kind="thinking", text=outcome))
        w.close(outcome=outcome)
        print(f"seeded: {pid}")


async def _call_tool(server, name: str, args: dict) -> str:
    async with Client(server) as client:
        result = await client.call_tool(name, args)
        return "\n".join(getattr(b, "text", str(b)) for b in result.content)


def demo_llm_retrieval(store: TrajectoryStore) -> None:
    _rule("3. LLM retrieval — search_trajectories (an LLM reads + synthesises)")
    from sregym_agents.cli_agent.trajectory.llm_search import PassthroughSearcher

    # Use the offline passthrough searcher so the demo runs without a model;
    # in production this is an agentshim call_subagent over a litellm model.
    server = build_trajectory_mcp(store, APP, mode="llm", searcher=PassthroughSearcher())
    query = "frontend 503s and a profile pod crash looping right after a deploy"
    print(f"query: {query!r}\n")
    print(asyncio.run(_call_tool(server, "search_trajectories", {"query": query})))


def demo_rag_retrieval(store: TrajectoryStore) -> str:
    _rule("4. RAG retrieval — search_trajectories(query, k) returns top-k by cosine")
    server = build_trajectory_mcp(store, APP)  # offline HashingEmbedder
    query = "frontend 503s and a profile pod crash looping right after a deploy"
    print(f"query: {query!r}\n")
    out = asyncio.run(_call_tool(server, "search_trajectories", {"query": query, "k": 2}))
    print(out)
    # Return the top path for the drill-down step.
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("path:"):
            return line.split("path:", 1)[1].strip()
    return ""


def demo_read_full(store: TrajectoryStore, path: str) -> None:
    _rule("5. DRILL-DOWN — read_trajectory(path) returns the full transcript")
    if not path:
        print("[no top hit path]")
        return
    server = build_trajectory_mcp(store, APP)
    print(asyncio.run(_call_tool(server, "read_trajectory", {"path": path})))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="traj_demo_") as tmp:
        store = TrajectoryStore(Path(tmp) / "trajectories")
        record_real_run(store)
        seed_corpus(store)
        demo_llm_retrieval(store)
        top = demo_rag_retrieval(store)
        demo_read_full(store, top)
        _rule("DONE")
        print(f"store root: {store.store_dir}  (files: {len(list(store.store_dir.rglob('*.jsonl')))})")


if __name__ == "__main__":
    main()
