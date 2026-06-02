"""Unit tests for the LLM-based trajectory searcher."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sregym_agents.cli_agent.trajectory import llm_search
from sregym_agents.cli_agent.trajectory.store import (
    TrajectoryMeta,
    TrajectoryRecorder,
    TrajectoryStore,
)

if TYPE_CHECKING:
    from pathlib import Path

APP = "hotelReservation"


def _seed_one(store: TrajectoryStore) -> None:
    w = store.open_run(TrajectoryMeta(app=APP, problem_id="wrong_service_selector_x", ts="20260601_000000"))
    rec = TrajectoryRecorder(w)
    rec.on_thinking("frontend has zero endpoints; service selector has an extra label")
    rec.on_tool_call("bash", "kubectl get endpoints frontend")
    w.close(outcome="completed")


def test_default_searcher_passthrough_without_model_or_provider(monkeypatch) -> None:
    monkeypatch.delenv("SDS_TRAJECTORY_LLM_SEARCH_MODEL", raising=False)
    assert isinstance(llm_search.default_searcher(), llm_search.PassthroughSearcher)


def test_default_searcher_uses_cli_agent_by_default(monkeypatch) -> None:
    """With no explicit litellm model, reuse the cli_agent's provider/model."""
    monkeypatch.delenv("SDS_TRAJECTORY_LLM_SEARCH_MODEL", raising=False)
    s = llm_search.default_searcher(provider="claude", model="claude-sonnet-4-6")
    assert isinstance(s, llm_search.CodingAgentSearcher)
    assert s.provider == "claude"
    assert s.model == "claude-sonnet-4-6"


def test_default_searcher_litellm_model_overrides_cli_agent(monkeypatch) -> None:
    monkeypatch.delenv("SDS_TRAJECTORY_LLM_SEARCH_MODEL", raising=False)
    s = llm_search.default_searcher(
        litellm_model="vertex_ai/gemini-2.5-flash", provider="claude", model="claude-sonnet-4-6"
    )
    assert isinstance(s, llm_search.CallSubagentSearcher)
    assert s.model == "vertex_ai/gemini-2.5-flash"


def test_default_searcher_env_model_when_set(monkeypatch) -> None:
    monkeypatch.setenv("SDS_TRAJECTORY_LLM_SEARCH_MODEL", "vertex_ai/gemini-2.5-flash")
    s = llm_search.default_searcher(provider="claude", model="claude-sonnet-4-6")
    assert isinstance(s, llm_search.CallSubagentSearcher)  # env overrides cli agent


def test_passthrough_returns_candidate_narratives(tmp_path: Path) -> None:
    store = TrajectoryStore(tmp_path)
    _seed_one(store)
    digests = store.digests(APP)
    out = llm_search.PassthroughSearcher()("frontend 503", digests, store)
    assert "zero endpoints" in out  # the recorded reasoning surfaces
    assert digests[0].run_id in out


def test_call_subagent_searcher_invokes_litellm(tmp_path: Path, monkeypatch) -> None:
    """CallSubagentSearcher feeds the query + candidate narratives to call_subagent."""
    store = TrajectoryStore(tmp_path)
    _seed_one(store)
    digests = store.digests(APP)
    captured: dict[str, str] = {}

    def fake_call_subagent(*, model, system_prompt, user_prompt, **kw):
        captured["model"] = model
        captured["user"] = user_prompt
        return "synthesised relevant finding"

    monkeypatch.setattr("agentshim.subagent.call_subagent", fake_call_subagent)
    out = llm_search.CallSubagentSearcher("some-model")("frontend 503s", digests, store)
    assert out == "synthesised relevant finding"
    assert captured["model"] == "some-model"
    assert "frontend 503s" in captured["user"]  # the query
    assert "zero endpoints" in captured["user"]  # candidate narrative content


def test_coding_agent_searcher_reuses_cli_agent(tmp_path: Path, monkeypatch) -> None:
    """CodingAgentSearcher spins up CodingAgent(provider, model) and feeds it the
    query + candidate narratives inline."""
    store = TrajectoryStore(tmp_path)
    _seed_one(store)
    digests = store.digests(APP)
    captured: dict[str, object] = {}

    class FakeCodingAgent:
        def __init__(self, *, provider, model):
            captured["provider"] = provider
            captured["model"] = model

        def generate(self, prompt, timeout=300, silent=False):
            captured["prompt"] = prompt
            return "cli-agent synthesised finding"

    monkeypatch.setattr("agentshim.CodingAgent", FakeCodingAgent)
    out = llm_search.CodingAgentSearcher("claude", "claude-sonnet-4-6")("frontend 503s", digests, store)
    assert out == "cli-agent synthesised finding"
    assert captured["provider"] == "claude"
    assert captured["model"] == "claude-sonnet-4-6"
    assert "frontend 503s" in captured["prompt"]
    assert "zero endpoints" in captured["prompt"]  # candidate narrative inline
