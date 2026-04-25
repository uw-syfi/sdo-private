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


def test_build_prompt_omits_incident_memory_instructions() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
    )
    assert "recall_incident" not in prompt
    assert "store_incident" not in prompt
    assert "Incident memory" not in prompt


# --- Autonomous-submit prompt variant ---------------------------------------


def test_build_prompt_autonomous_uses_per_stage_tool_names() -> None:
    """When autonomous_submit is on, the prompt must reference
    ``submit_diagnosis`` / ``submit_mitigation`` instead of ``submit``, and
    must tell the agent no grading verdict will come back."""
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "submit_diagnosis" in prompt
    assert "submit_mitigation" in prompt
    # Must not describe a stage-routing single-`submit` workflow — that is
    # the non-autonomous contract and would mislead the agent here.
    assert "whichever stage is currently active" not in prompt
    # The self-verification framing is the whole point of autonomous mode.
    assert "kubectl" in prompt.lower()
    # Must not promise a grading verdict from the tool response.
    assert "verdict" not in prompt.lower()
    assert "whether your answer was accepted" in prompt
    assert "ground-truth root cause" not in prompt


def test_build_prompt_non_autonomous_is_unchanged() -> None:
    """Default (autonomous=False) still produces the legacy
    single-``submit`` prompt so existing runs don't regress."""
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
    )
    assert "submit_diagnosis" not in prompt
    assert "submit_mitigation" not in prompt


def test_build_prompt_autonomous_diagnosis_only() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "submit_diagnosis" in prompt
    assert "submit_mitigation" not in prompt


def test_build_prompt_autonomous_mitigation_only() -> None:
    prompt = driver._build_prompt(
        planned_stages=["mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "submit_mitigation" in prompt
    assert "submit_diagnosis" not in prompt


def test_build_prompt_autonomous_direct_profile_omits_sds_contract() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
        autonomous_prompt_profile="direct",
    )
    assert ".sds/" not in prompt
    assert ".sds/diagnose.sh" not in prompt
    assert ".sds/playbooks/" not in prompt
    assert "Repository-local scripts are optional, not expected" in prompt
    assert "do not treat script-writing or repo-local tooling as part of the task" in prompt


def test_build_prompt_autonomous_mentions_persistent_diagnostic_contract() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert ".sds/" in prompt
    assert ".sds/diagnose.sh" in prompt
    assert ".sds/playbooks/" in prompt
    assert "quick to run" in prompt
    assert "well-organized" in prompt
    assert "good Bash script best practices" in prompt
    assert "prefer small functions" in prompt
    assert "dispatch into helper scripts" in prompt
    assert "readable, maintainable, and easy to extend" in prompt


def test_build_prompt_autonomous_requires_preflight_for_prior_diagnostics() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "Start every run with this preflight workflow" in prompt
    assert "If `.sds/diagnose.sh` exists, run it at the beginning of the session" in prompt
    assert "Check whether `.sds/diagnose.sh` or related `.sds/` diagnostics already exist" in prompt
    assert "Only edit source files when you can point to the exact bad setting in the repository" in prompt
    assert "repository-local diagnostic scripts" in prompt
    assert "Read the application source code and deployment manifests" in prompt
    assert "quickly triage the cluster and flag previously seen issues" in prompt
    assert "print which playbook file(s) under `.sds/playbooks/` are relevant" in prompt


def test_build_prompt_autonomous_mentions_post_submit_check_improvement() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "Only after `submit_done` returns" in prompt
    assert "add a new diagnostic check, or enhance an existing one" in prompt
    assert "future run" in prompt
    assert ".sds/diagnose.sh" in prompt
    assert ".sds/playbooks/" in prompt
    assert "generalizable Markdown playbook" in prompt
    assert "using what you verified from the live cluster and source tree" in prompt
    assert "using the ground-truth feedback" not in prompt
    assert "commit those `.sds/` changes before exiting" in prompt
    assert "store_incident" not in prompt


def test_build_prompt_autonomous_requires_source_fix_then_redeploy() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "Only edit source files when you can point to the exact bad setting in the repository" in prompt
    assert "fix the real file in place first" in prompt
    assert "apply or redeploy the updated manifests/source" in prompt
    assert "running cluster now reflects that source change" in prompt
    assert "Do not treat a source edit as complete until the updated deployment is live and verified" in prompt


def test_build_prompt_autonomous_requires_multi_fault_full_health_verification() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "Assume there may be multiple concurrent faults" in prompt
    assert "all user traffic is healthy across all relevant paths" in prompt
    assert "system internals also appear healthy" in prompt
    assert "not just the first path that starts working again" in prompt
    assert (
        "Only call `submit_mitigation` once you are confident the application is genuinely "
        "healthy across all relevant traffic paths and internal checks" in prompt
    )


def test_build_prompt_autonomous_mentions_rich_feedback_when_enabled() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
        submit_done_returns_feedback=True,
    )
    assert "returns rich feedback" in prompt
    assert "ground-truth root cause" in prompt
    assert "using the ground-truth feedback" in prompt


def test_build_prompt_rejects_unknown_autonomous_prompt_profile() -> None:
    with pytest.raises(ValueError, match="Unknown autonomous prompt profile"):
        driver._build_prompt(
            planned_stages=["diagnosis"],
            app_info={"app_name": "a", "namespace": "n"},
            autonomous=True,
            autonomous_prompt_profile="unknown",
        )


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


def _patch_conductor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stages: list[str] | None = None,
    problem_id: str = "p-1",
    app: dict[str, Any] | None = None,
    status_sequence: list[str] | None = None,
) -> None:
    """Patch the libs.sregym_lib.conductor helpers the driver imports.

    ``status_sequence`` is the sequence of stage values observed by the
    driver. The first entry is what the startup ``poll_stage_sync``
    returns; subsequent entries feed the post-session
    ``wait_for_stages_or_last_seen_sync`` — which the driver calls once
    and is expected to return the final (last observed) stage.
    """
    stages = stages or ["diagnosis"]
    app = app or {"app_name": "myapp", "namespace": "myns"}
    seq = list(status_sequence) if status_sequence else [stages[0], "done"]

    monkeypatch.setattr(driver, "get_app_info", lambda _api_base: app)
    monkeypatch.setattr(driver, "get_problem_id", lambda _api_base: problem_id)
    monkeypatch.setattr(driver, "get_planned_stages", lambda _api_base: list(stages))
    monkeypatch.setattr(
        driver,
        "poll_stage_sync",
        lambda _api_base, *, wait_for, timeout, on_timeout="raise": seq[0],
    )
    # The last stage in status_sequence is what the driver treats as the
    # final observed stage.
    monkeypatch.setattr(
        driver,
        "wait_for_stages_or_last_seen_sync",
        lambda _api_base, *, expected, timeout: seq[-1],
    )


def _args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "provider": "claude",
        "model": "m",
        "logs_dir": None,
        "timeout_sec": 30,
        "autonomous_prompt_profile": "sds",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_run_autonomous_does_not_block_on_terminal_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In autonomous mode the conductor never advances to a terminal stage
    (submit_autonomous deliberately does not touch the sequential state
    machine). The driver must not spin for _POST_STAGE_TIMEOUT_S waiting
    for a transition that will never happen — otherwise every autonomous
    run burns 5 minutes between problems."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("SREGYM_AUTONOMOUS_SUBMIT", "1")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    # If the driver called wait_for_stages_or_last_seen_sync it would poll
    # /status forever against our non-advancing conductor; make the call
    # blow up so the test fails loudly if the branch is wrong.
    def _fail_post_stage(*_a: Any, **_kw: Any) -> str | None:
        raise AssertionError("wait_for_stages_or_last_seen_sync must not run in autonomous mode")

    monkeypatch.setattr(driver, "wait_for_stages_or_last_seen_sync", _fail_post_stage)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        status_sequence=["diagnosis", "diagnosis"],
    )
    # Autonomous path calls get_current_stage_sync instead of the polling
    # helper — patch it so the driver reports a non-None final stage.
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "diagnosis")
    driver._run(_args(), agent_factory=lambda p, m, u: stub)


def test_run_autonomous_env_var_picks_autonomous_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``SREGYM_AUTONOMOUS_SUBMIT=1`` is set in the environment, the
    driver must render the autonomous prompt variant (``submit_diagnosis``
    / ``submit_mitigation``). Env-var plumbing is the critical path: the
    worker sets this before the driver starts so the MCP server and the
    prompt stay consistent."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("SREGYM_AUTONOMOUS_SUBMIT", "1")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        status_sequence=["diagnosis", "done"],
    )
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "done")
    driver._run(_args(), agent_factory=lambda p, m, u: stub)

    prompt = stub.calls[0]["prompt"]
    assert "submit_diagnosis" in prompt
    assert "submit_mitigation" in prompt
    assert "whether your answer was accepted" in prompt


def test_run_autonomous_submit_done_feedback_env_var_updates_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("SREGYM_AUTONOMOUS_SUBMIT", "1")
    monkeypatch.setenv("SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK", "1")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        status_sequence=["diagnosis", "done"],
    )
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "done")
    driver._run(_args(), agent_factory=lambda p, m, u: stub)

    prompt = stub.calls[0]["prompt"]
    assert "returns rich feedback" in prompt
    assert "ground-truth root cause" in prompt


def test_run_autonomous_direct_profile_uses_direct_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("SREGYM_AUTONOMOUS_SUBMIT", "1")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        status_sequence=["diagnosis", "done"],
    )
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "done")
    driver._run(_args(autonomous_prompt_profile="direct"), agent_factory=lambda p, m, u: stub)

    prompt = stub.calls[0]["prompt"]
    assert ".sds/" not in prompt
    assert "Repository-local scripts are optional, not expected" in prompt


def test_run_happy_path_single_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """One CLI session per problem, with MCP submit URL wired through."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("MCP_SERVER_PORT", "9954")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)

    stub = _StubAgent()
    factory_calls: list[tuple[str, str, str]] = []

    def _factory(provider: str, model: str, submit_mcp_url: str) -> Any:
        factory_calls.append((provider, model, submit_mcp_url))
        return stub

    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        # startup -> diagnosis; post-session poll -> done
        status_sequence=["diagnosis", "done"],
    )
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
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        # startup -> diagnosis; post-session lib poll resolves to "done"
        status_sequence=["diagnosis", "done"],
    )
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
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        # Conductor stays on "diagnosis" — no terminal state.
        status_sequence=["diagnosis", "diagnosis"],
    )
    driver._run(_args(), agent_factory=lambda p, m, u: stub)

    assert len(stub.calls) == 1


def test_run_logs_dir_writes_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        problem_id="p-42",
        status_sequence=["diagnosis", "done"],
    )
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

    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        status_sequence=["diagnosis", "diagnosis"],
    )
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

    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        status_sequence=["diagnosis", "diagnosis"],
    )
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
        json.dumps(
            {
                "provider": "codex",
                "model": "my-model",
                "timeout_sec": 42,
                "autonomous_prompt_profile": "direct",
            }
        ),
    )
    ns = driver._parse_args([])
    assert ns.provider == "codex"
    assert ns.model == "my-model"
    assert ns.timeout_sec == 42
    assert ns.autonomous_prompt_profile == "direct"


def test_parse_args_cli_flag_beats_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SREGYM_EXPERIMENT_AGENT_CONFIG",
        json.dumps({"provider": "codex", "autonomous_prompt_profile": "sds"}),
    )
    ns = driver._parse_args(["--provider", "claude", "--autonomous-prompt-profile", "direct"])
    assert ns.provider == "claude"
    assert ns.autonomous_prompt_profile == "direct"


def test_parse_args_invalid_toml_json_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_ID", raising=False)
    monkeypatch.setenv("SREGYM_EXPERIMENT_AGENT_CONFIG", "not-json{{")
    ns = driver._parse_args([])
    assert ns.provider == "claude"
    # New whole-session default, up from the old per-stage 600s budget.
    assert ns.timeout_sec == 2000
    assert ns.autonomous_prompt_profile == "sds"


def test_parse_args_invalid_autonomous_prompt_profile_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SREGYM_EXPERIMENT_AGENT_CONFIG",
        json.dumps({"autonomous_prompt_profile": "bogus"}),
    )
    with pytest.raises(ValueError, match="Unknown autonomous prompt profile"):
        driver._parse_args([])


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


def test_parse_args_rejects_removed_memory_flags() -> None:
    with pytest.raises(SystemExit):
        driver._parse_args(["--memory-store", "/tmp/incidents.db"])


def test_cli_agent_is_registered_as_external_agent() -> None:
    from libs.sregym_lib.experiment import _EXTERNAL_AGENTS

    assert "cli_agent" in _EXTERNAL_AGENTS
