"""Unit tests for sregym_agents.cli_agent.driver.

The CLI agent itself is never instantiated here — tests inject stubs via
the ``agent_factory`` seam on ``_run``. The driver spawns **one** CLI
session per problem that handles all planned stages (diagnosis +
mitigation); the conductor routes each ``submit`` tool call to whichever
stage is currently active. Tests verify:

- the CLI is constructed with the ``submit_mcp_url`` wired through,
- the CLI is invoked exactly once per problem,
- the prompt is appropriate for the planned-stage list, and
- the driver waits for the conductor to reach a terminal stage before
  declaring the session complete.
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from libs.agent_cli.base import CodingAgent
from sregym_agents.cli_agent import driver

if TYPE_CHECKING:
    from pathlib import Path


# --- Prompt ----------------------------------------------------------------


def test_build_prompt_diagnosis_only_includes_context() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis"],
        app_info={"app_name": "social_network", "namespace": "social-net"},
    )
    assert "social_network" in prompt
    assert "social-net" in prompt
    assert "diagnosis" in prompt.lower()
    assert "submit" in prompt
    assert "kubectl" in prompt.lower()
    # Legacy <answer>-tag contract must be gone.
    assert "<answer>" not in prompt


def test_build_prompt_omits_problem_id() -> None:
    """SREGym problem IDs are descriptive (e.g. 'incorrect_image') and would
    leak the answer to the agent — they must not appear in the prompt.
    """
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
    )
    assert "Problem ID" not in prompt
    assert "problem_id" not in prompt


def test_build_prompt_two_stages_describes_both() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
    )
    assert "diagnosis" in prompt.lower()
    assert "mitigation" in prompt.lower()
    # Should tell the agent to call submit twice.
    assert "2" in prompt or "twice" in prompt.lower()


def test_build_prompt_names_the_mcp_server() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis"],
        app_info={"app_name": "a", "namespace": "n"},
    )
    assert driver._SUBMIT_MCP_SERVER_NAME in prompt


# --- Agent factory ---------------------------------------------------------


def test_default_agent_factory_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown cli_agent provider"):
        driver._default_agent_factory("nope_not_a_provider", "m", "http://x/submit/sse")


def test_default_agent_factory_passes_mcp_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory must inject a HttpMcpServer for sregym submission."""
    from libs.agent_cli.base import AGENT_REGISTRY
    from libs.agent_cli.mcp_config import HttpMcpServer

    captured: dict[str, Any] = {}

    class _FakeCls:
        def __init__(self, *, model: str, mcp_servers: list[Any]) -> None:
            captured["model"] = model
            captured["mcp_servers"] = mcp_servers

    monkeypatch.setitem(AGENT_REGISTRY, "fake_mcp_provider", _FakeCls)
    driver._default_agent_factory("fake_mcp_provider", "m-1", "http://h:1234/submit/sse")

    assert captured["model"] == "m-1"
    assert len(captured["mcp_servers"]) == 1
    server = captured["mcp_servers"][0]
    assert isinstance(server, HttpMcpServer)
    assert server.name == driver._SUBMIT_MCP_SERVER_NAME
    assert server.url == "http://h:1234/submit/sse"


# --- _run orchestration ----------------------------------------------------


class _StubAgent(CodingAgent):
    """Minimal CodingAgent stand-in."""

    def __init__(self, response: str = "ok") -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> str:
        self.calls.append({"prompt": prompt, "cwd": cwd, "timeout": timeout})
        return self.response


def _mock_conductor(
    *,
    stages: list[str] | None = None,
    problem_id: str = "p-1",
    app: dict[str, Any] | None = None,
    status_sequence: list[str] | None = None,
):
    """Build a requests.get replacement that serves the conductor endpoints.

    ``status_sequence`` is the sequence of values returned by successive
    ``/status`` calls. The first entry satisfies the startup
    ``_wait_for_stage``; subsequent entries feed the post-session
    ``_wait_for_post_stage`` poll.
    """
    stages = stages or ["diagnosis"]
    app = app or {"app_name": "myapp", "namespace": "myns"}
    seq = list(status_sequence) if status_sequence else [stages[0], "done"]
    idx = [0]

    def _get(url: str, timeout: float = 5, **_: Any) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if url.endswith("/status"):
            i = min(idx[0], len(seq) - 1)
            resp.json.return_value = {"stage": seq[i]}
            idx[0] += 1
        elif url.endswith("/get_problem"):
            resp.json.return_value = {"problem_id": problem_id}
        elif url.endswith("/get_app"):
            resp.json.return_value = app
        elif url.endswith("/stages"):
            resp.json.return_value = {"stages": stages}
        else:
            raise AssertionError(f"unexpected URL: {url}")
        return resp

    return _get


def _args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "provider": "claude",
        "model": "m",
        "logs_dir": None,
        "timeout_sec": 30,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_run_happy_path_single_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """One CLI session per problem, with MCP submit URL wired through."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("MCP_SERVER_PORT", "9954")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    stub = _StubAgent()
    factory_calls: list[tuple[str, str, str]] = []

    def _factory(provider: str, model: str, submit_mcp_url: str) -> Any:
        factory_calls.append((provider, model, submit_mcp_url))
        return stub

    mock = _mock_conductor(
        stages=["diagnosis", "mitigation"],
        # startup -> diagnosis; post-session poll -> done
        status_sequence=["diagnosis", "done"],
    )
    with patch("sregym_agents.cli_agent.driver.requests.get", side_effect=mock):
        driver._run(_args(), agent_factory=_factory)

    # Exactly ONE CLI session — both stages handled inside it.
    assert len(stub.calls) == 1
    assert len(factory_calls) == 1
    provider, model, url = factory_calls[0]
    assert provider == "claude"
    assert model == "m"
    assert url == "http://localhost:9954/submit/sse"
    # Prompt must mention both planned stages.
    prompt = stub.calls[0]["prompt"]
    assert "diagnosis" in prompt.lower()
    assert "mitigation" in prompt.lower()


def test_run_waits_for_verifying_to_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Driver must not declare 'completed' while the conductor is still grading.

    After the CLI session returns, the conductor may be in
    `'mitigation (verifying)'` for a brief window. The driver polls
    until the conductor reaches a terminal stage.
    """
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.setattr(driver.time, "sleep", lambda *_: None)

    stub = _StubAgent()
    mock = _mock_conductor(
        stages=["diagnosis", "mitigation"],
        # startup -> diagnosis; post-session polls -> verifying, verifying, done
        status_sequence=[
            "diagnosis",
            "mitigation (verifying)",
            "mitigation (verifying)",
            "done",
        ],
    )
    with patch("sregym_agents.cli_agent.driver.requests.get", side_effect=mock):
        driver._run(_args(), agent_factory=lambda p, m, u: stub)

    assert len(stub.calls) == 1


def test_run_tolerates_missing_final_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the conductor never reaches a terminal stage, the driver gives up gracefully."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    # Shrink the post-stage wait so the test doesn't block for 5 minutes.
    monkeypatch.setattr(driver, "_POST_STAGE_TIMEOUT_S", 1)
    monkeypatch.setattr(driver.time, "sleep", lambda *_: None)

    stub = _StubAgent()
    mock = _mock_conductor(
        stages=["diagnosis"],
        # Conductor stays on "diagnosis" — no terminal state.
        status_sequence=["diagnosis", "diagnosis", "diagnosis"],
    )
    with patch("sregym_agents.cli_agent.driver.requests.get", side_effect=mock):
        driver._run(_args(), agent_factory=lambda p, m, u: stub)

    assert len(stub.calls) == 1


def test_run_logs_dir_writes_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    stub = _StubAgent()
    mock = _mock_conductor(
        stages=["diagnosis", "mitigation"],
        problem_id="p-42",
        status_sequence=["diagnosis", "done"],
    )
    with patch("sregym_agents.cli_agent.driver.requests.get", side_effect=mock):
        driver._run(_args(logs_dir=str(tmp_path)), agent_factory=lambda p, m, u: stub)

    files = list(tmp_path.glob("cli_agent_results_p-42_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["problem_id"] == "p-42"
    assert data["provider"] == "claude"
    assert data["model"] == "m"
    assert data["planned_stages"] == ["diagnosis", "mitigation"]
    assert data["final_stage"] == "done"
    assert data["completed"] is True
    assert data["crashed_with"] is None


def test_run_agent_exception_records_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the CLI raises, record the crash and keep the process from exiting non-zero."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.setattr(driver, "_POST_STAGE_TIMEOUT_S", 1)
    monkeypatch.setattr(driver.time, "sleep", lambda *_: None)

    class _Boom(CodingAgent):
        def generate(self, *a: Any, **k: Any) -> str:
            raise RuntimeError("CLI died")

    mock = _mock_conductor(
        stages=["diagnosis"],
        status_sequence=["diagnosis", "diagnosis"],
    )
    with patch("sregym_agents.cli_agent.driver.requests.get", side_effect=mock):
        # Does not raise.
        driver._run(_args(), agent_factory=lambda p, m, u: _Boom())


def test_run_logs_crash_when_agent_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.setattr(driver, "_POST_STAGE_TIMEOUT_S", 1)
    monkeypatch.setattr(driver.time, "sleep", lambda *_: None)

    class _Boom(CodingAgent):
        def generate(self, *a: Any, **k: Any) -> str:
            raise RuntimeError("CLI died")

    mock = _mock_conductor(
        stages=["diagnosis"],
        status_sequence=["diagnosis", "diagnosis"],
    )
    with patch("sregym_agents.cli_agent.driver.requests.get", side_effect=mock):
        driver._run(
            _args(logs_dir=str(tmp_path)),
            agent_factory=lambda p, m, u: _Boom(),
        )

    files = list(tmp_path.glob("cli_agent_results_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["crashed_with"] is not None
    assert "CLI died" in data["crashed_with"]
    assert data["completed"] is False


# --- Argument parsing ------------------------------------------------------


def test_parse_args_reads_toml_agent_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """[agent.cli_agent] TOML block (via SREGYM_EXPERIMENT_AGENT_CONFIG) sets defaults."""
    monkeypatch.delenv("MODEL_ID", raising=False)
    monkeypatch.setenv(
        "SREGYM_EXPERIMENT_AGENT_CONFIG",
        json.dumps({"provider": "codex", "model": "my-model", "timeout_sec": 42}),
    )
    ns = driver._parse_args([])
    assert ns.provider == "codex"
    assert ns.model == "my-model"
    assert ns.timeout_sec == 42


def test_parse_args_cli_flag_beats_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SREGYM_EXPERIMENT_AGENT_CONFIG",
        json.dumps({"provider": "codex"}),
    )
    ns = driver._parse_args(["--provider", "claude"])
    assert ns.provider == "claude"


def test_parse_args_invalid_toml_json_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_ID", raising=False)
    monkeypatch.setenv("SREGYM_EXPERIMENT_AGENT_CONFIG", "not-json{{")
    ns = driver._parse_args([])
    assert ns.provider == "claude"
    # New whole-session default, up from the old per-stage 600s budget.
    assert ns.timeout_sec == 2000


def test_parse_args_tolerates_sregym_launcher_flags() -> None:
    ns = driver._parse_args(
        [
            "--no-inject-summary",
            "--enable-summary",
            "--summary-dir",
            "/tmp/foo",
            "--summary-model",
            "claude-sonnet-4-6",
        ]
    )
    assert ns.provider == "claude"
    assert ns.timeout_sec == 2000


def test_cli_agent_is_registered_as_external_agent() -> None:
    from sregym_agents.experiment_config import _EXTERNAL_AGENTS

    assert "cli_agent" in _EXTERNAL_AGENTS
