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
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from agentshim.base import BaseAgentSession, register_provider

from agentshim import BaseCodingAgent
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


def test_build_prompt_omits_recall_guidance_when_memory_disabled() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
    )
    assert "`recall`" not in prompt


def test_build_prompt_includes_recall_guidance_when_memory_enabled() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        memory_enabled=True,
    )
    assert "`recall`" in prompt
    assert "memory" in prompt.lower()
    assert "hypotheses" in prompt.lower()


def test_build_prompt_autonomous_includes_recall_guidance_when_memory_enabled() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
        memory_enabled=True,
    )
    assert "`recall`" in prompt


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


def test_build_prompt_autonomous_mentions_playbook_contract_without_root_diagnose_script() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert ".sds/" in prompt
    assert ".sds/diagnose.sh" not in prompt
    assert ".sds/playbooks/" in prompt
    assert "Do not create a top-level triage script under `.sds`" in prompt
    assert "playbook-local helper scripts" in prompt
    assert "quick to run" in prompt
    assert "well-organized" in prompt
    assert "good Bash script best practices" in prompt
    assert "prefer small functions" in prompt
    assert "readable, maintainable, and easy to extend" in prompt


def test_build_prompt_autonomous_omits_observer_detectors_by_default() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert ".sds/diagnostics" not in prompt
    assert "sds-observer-check" not in prompt


def test_build_prompt_autonomous_observer_detectors_when_enabled() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "demo"},
        autonomous=True,
        observer_detectors_enabled=True,
    )
    assert ".sds/diagnostics" in prompt
    assert ".sds/diagnose.sh" not in prompt
    assert "manifest.yaml" in prompt
    assert "sds-observer-check" not in prompt
    assert "Existing observer detectors are run by the driver before the session starts" in prompt
    assert "Defer creating or revising observer detectors until after `submit_done` returns" in prompt
    assert "observer-detector mechanism supersedes the old top-level shell triage entrypoint" in prompt
    assert "do not create a root triage script under `.sds`" in prompt
    assert "The driver will validate observer detectors after you exit" in prompt
    assert ".sds/diagnostics/detectors/missing_endpoints/detector.go" in prompt
    assert "module app-diagnostics" in prompt
    assert "Use Go-safe detector package directory names with underscores" in prompt
    assert "Do not make detector files `package main`" in prompt
    assert "apiVersion: sds.dev/v1alpha1" in prompt
    assert "package: ./detectors/missing_endpoints" in prompt
    assert "sdk.Finding.Playbooks" in prompt
    assert "func New() sdk.Detector" in prompt
    assert "func (Detector) Detect(ctx context.Context, snap sdk.DetectionContext)" in prompt
    assert "snap.ReadyEndpointCountForService" in prompt
    assert "Prefer the sharpest check that identifies a single root cause" in prompt
    assert "A symptom detector must fire only on a persistent condition" in prompt
    assert "exploratory hypotheses" in prompt
    assert "For Service endpoint incidents" in prompt
    assert "should not skip findings because some application-specific dependency is absent" in prompt
    assert "treat that detector ID as the owner of this issue class" in prompt
    assert "Do not create a new overlapping detector or playbook" in prompt


def test_build_prompt_autonomous_includes_observer_preflight_report() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "demo"},
        autonomous=True,
        observer_detectors_enabled=True,
        observer_preflight_report=(
            "Observer preflight completed successfully.\n"
            "1 observer finding(s):\n"
            "1. [warn active missing-endpoints] service frontend has no ready endpoints\n"
            "   detector: missing-endpoints\n"
            "   recommended playbooks:\n"
            "   - .sds/playbooks/service-endpoints/README.md"
        ),
    )
    assert "## Observer Preflight Findings" in prompt
    assert "The driver already ran existing observer diagnostics before this session" in prompt
    assert "Do not run observer commands yourself during preflight" in prompt
    assert "Each finding lists the detector that fired" in prompt
    assert "do not add a second detector or overlapping playbook" in prompt
    assert "service frontend has no ready endpoints" in prompt
    assert ".sds/playbooks/service-endpoints/README.md" in prompt


def test_build_prompt_autonomous_requires_preflight_for_prior_diagnostics() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "Start every run with this preflight workflow" in prompt
    assert ".sds/diagnose.sh" not in prompt
    assert "Check whether `.sds/playbooks/` already exists from prior runs" in prompt
    assert "Use existing playbooks as context only" in prompt
    assert "Only edit source files when you can point to the exact bad setting in the repository" in prompt
    assert "do not spend incident time building repository-local diagnostic tooling" in prompt
    assert "Read the application source code and deployment manifests" in prompt
    assert "direct `kubectl`, source/manifests, logs, and targeted shell commands" in prompt
    assert "playbook-linked helper output should name only" in prompt
    assert "Only flag playbooks that are directly relevant to the detected symptom" in prompt
    assert "Playbooks should be self-contained and avoid overlapping each other" in prompt
    assert "potentially relevant" not in prompt


def test_build_prompt_autonomous_warns_against_interactive_kubectl() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "hotel-reservation"},
        autonomous=True,
    )
    assert "## Command Notes" in prompt
    assert "Do not use `kubectl run -it ... --rm`" in prompt
    assert "Interactive attach can hang after the pod completes" in prompt
    assert "kubectl delete pod curl-test -n hotel-reservation --ignore-not-found" in prompt
    assert "kubectl exec -n hotel-reservation deploy/frontend" in prompt


def test_build_prompt_autonomous_mentions_post_submit_check_improvement() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
    )
    assert "Only after `submit_done` returns" in prompt
    assert "add a new diagnostic check, or enhance an existing one" in prompt
    assert "future run" in prompt
    assert ".sds/diagnose.sh" not in prompt
    assert ".sds/playbooks/" in prompt
    assert "generalizable Markdown playbook" in prompt
    assert "update the fired detector/playbook instead of adding a duplicate detector" not in prompt
    assert "using what you verified from the live cluster and source tree" in prompt
    assert "using the ground-truth feedback" not in prompt
    assert "commit those `.sds/` changes before exiting" in prompt
    assert "store_incident" not in prompt


def test_build_prompt_autonomous_observer_avoids_duplicate_detectors_after_preflight_hit() -> None:
    prompt = driver._build_prompt(
        planned_stages=["diagnosis", "mitigation"],
        app_info={"app_name": "a", "namespace": "n"},
        autonomous=True,
        observer_detectors_enabled=True,
    )
    assert "update the fired detector/playbook instead of adding a duplicate detector" in prompt


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
    from agentshim.mcp_config import HttpMcpServer

    captured: dict[str, Any] = {}

    @register_provider("fake_mcp_provider")
    class _FakeCls(BaseCodingAgent):
        def __init__(self, *, model: str, mcp_servers: list[Any]) -> None:
            captured["model"] = model
            captured["mcp_servers"] = mcp_servers

        def generate(self, prompt: str, cwd=None, timeout=300, silent=False) -> str:
            return ""

    driver._default_agent_factory("fake_mcp_provider", "m-1", "http://h:1234/submit/sse")

    assert captured["model"] == "m-1"
    assert len(captured["mcp_servers"]) == 1
    server = captured["mcp_servers"][0]
    assert isinstance(server, HttpMcpServer)
    assert server.name == driver._SUBMIT_MCP_SERVER_NAME
    assert server.url == "http://h:1234/submit/sse"


# --- _run orchestration ----------------------------------------------------


class _StubSession(BaseAgentSession):
    """Resumable-session stand-in that records each ``generate`` on its agent."""

    def __init__(self, agent: _StubAgent, cwd: str | None, timeout: int) -> None:
        self.agent = agent
        self._cwd = cwd
        self._timeout = timeout
        self.session_id = "stub-session"

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int | None = None,
        silent: bool | None = None,
        on_process_started: Any | None = None,
    ) -> str:
        self.agent.calls.append(
            {
                "prompt": prompt,
                "cwd": cwd if cwd is not None else self._cwd,
                "timeout": timeout if timeout is not None else self._timeout,
            }
        )
        # First call = investigation; later calls = extraction (queued replies).
        if len(self.agent.calls) == 1:
            return self.agent.response
        return self.agent.extra_replies.pop(0) if self.agent.extra_replies else ""


class _StubAgent(BaseCodingAgent):
    """Minimal CodingAgent stand-in.

    ``extra_replies`` are returned by the second and later ``generate`` calls
    (i.e. the post-verdict lesson-extraction turn).
    """

    def __init__(self, response: str = "ok", extra_replies: list[str] | None = None) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.extra_replies = list(extra_replies or [])

    def start_session(
        self,
        cwd: str | None = None,
        timeout: int = 300,
        silent: bool = False,
    ) -> _StubSession:
        return _StubSession(self, cwd, timeout)

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


def _write_observer_manifest(app_root: Path) -> None:
    diagnostics_dir = app_root / ".sds" / "diagnostics"
    diagnostics_dir.mkdir(parents=True)
    (diagnostics_dir / "manifest.yaml").write_text(
        """
apiVersion: sds.dev/v1alpha1
kind: ObserverDiagnostics
detectors:
  - name: demo
    package: ./detectors/demo
""".lstrip()
    )


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


def test_run_autonomous_persistent_workspace_enables_observer_prompt(
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
        app={"app_name": "a", "namespace": "demo"},
        status_sequence=["diagnosis", "done"],
    )
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "done")
    driver._run(_args(application_workspace_mode="persistent"), agent_factory=lambda p, m, u: stub)

    prompt = stub.calls[0]["prompt"]
    assert ".sds/diagnostics" in prompt
    assert "driver runs existing observer detectors before the session starts" in prompt
    assert "sds-observer-check" not in prompt


def test_run_non_autonomous_persistent_workspace_omits_observer_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        app={"app_name": "a", "namespace": "demo"},
        status_sequence=["diagnosis", "done"],
    )
    driver._run(_args(application_workspace_mode="persistent"), agent_factory=lambda p, m, u: stub)

    prompt = stub.calls[0]["prompt"]
    assert ".sds/diagnostics" not in prompt
    assert "sds-observer-check" not in prompt


def test_run_autonomous_persistent_workspace_without_manifest_skips_observer_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("SREGYM_AUTONOMOUS_SUBMIT", "1")
    monkeypatch.setenv("SREGYM_EXP_ENV", str(tmp_path))

    run_calls: list[list[str]] = []
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda command, **_kwargs: run_calls.append(command),
    )

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        app={"app_name": "a", "namespace": "demo"},
        status_sequence=["diagnosis", "done"],
    )
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "done")
    driver._run(_args(application_workspace_mode="persistent"), agent_factory=lambda p, m, u: stub)

    assert len(stub.calls) == 1
    assert run_calls == []


def test_run_autonomous_persistent_workspace_runs_observer_preflight_and_post_agent_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("SREGYM_AUTONOMOUS_SUBMIT", "1")
    monkeypatch.setenv("SREGYM_EXP_ENV", str(tmp_path))
    _write_observer_manifest(tmp_path)

    run_calls: list[list[str]] = []

    def _fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls.append(command)
        if "run-once" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"detector_id":"missing-endpoints","rule_id":"missing-endpoints",'
                    '"status":"active","severity":"warn",'
                    '"summary":"service frontend has no ready endpoints",'
                    '"evidence":"namespace=demo service=frontend ready_endpoints=0",'
                    '"primary_resource":{"kind":"Service","namespace":"demo","name":"frontend"},'
                    '"playbooks":[".sds/playbooks/service-endpoints/README.md"]}\n'
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", _fake_run)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        app={"app_name": "a", "namespace": "demo"},
        status_sequence=["diagnosis", "done"],
    )
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "done")
    driver._run(_args(application_workspace_mode="persistent"), agent_factory=lambda p, m, u: stub)

    assert len(stub.calls) == 1
    expected_check_commands = [
        [
            driver.sys.executable,
            "-m",
            "observer.updater.check_cli",
            "test",
            "--app",
            str(tmp_path),
        ],
        [
            driver.sys.executable,
            "-m",
            "observer.updater.check_cli",
            "run-once",
            "--app",
            str(tmp_path),
            "--namespace",
            "demo",
        ],
    ]
    assert run_calls == expected_check_commands + expected_check_commands
    prompt = stub.calls[0]["prompt"]
    assert "## Observer Preflight Findings" in prompt
    assert "service frontend has no ready endpoints" in prompt
    assert "detector: missing-endpoints" in prompt
    assert "recommended playbooks:" in prompt
    assert ".sds/playbooks/service-endpoints/README.md" in prompt


def test_run_autonomous_persistent_workspace_reprompts_same_session_when_observer_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("SREGYM_AUTONOMOUS_SUBMIT", "1")
    monkeypatch.setenv("SREGYM_EXP_ENV", str(tmp_path))
    _write_observer_manifest(tmp_path)

    run_calls: list[list[str]] = []

    def _fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        run_calls.append(command)
        if len(run_calls) == 3:
            return subprocess.CompletedProcess(command, 1, stdout="build failed", stderr="missing method")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(driver.subprocess, "run", _fake_run)

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        app={"app_name": "a", "namespace": "demo"},
        status_sequence=["diagnosis", "done"],
    )
    monkeypatch.setattr(driver, "get_current_stage_sync", lambda _api_base: "done")
    driver._run(_args(application_workspace_mode="persistent"), agent_factory=lambda p, m, u: stub)

    assert len(stub.calls) == 2
    repair_prompt = stub.calls[1]["prompt"]
    assert "post-submission observer diagnostics check failed" in repair_prompt
    assert "missing method" in repair_prompt
    assert "target the directly observed Kubernetes symptom" in repair_prompt
    assert "do not gate a generic symptom detector on unrelated application resources" in repair_prompt
    assert "Do not call `submit_diagnosis`, `submit_mitigation`, or `submit_done`" in repair_prompt
    assert stub.calls[1]["cwd"] == str(tmp_path)
    assert len(run_calls) == 5


def test_run_non_autonomous_persistent_workspace_skips_post_observer_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)
    monkeypatch.setenv("SREGYM_EXP_ENV", str(tmp_path))
    _write_observer_manifest(tmp_path)

    run_calls: list[list[str]] = []
    monkeypatch.setattr(
        driver.subprocess,
        "run",
        lambda command, **_kwargs: run_calls.append(command),
    )

    stub = _StubAgent()
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        app={"app_name": "a", "namespace": "demo"},
        status_sequence=["diagnosis", "done"],
    )
    driver._run(_args(application_workspace_mode="persistent"), agent_factory=lambda p, m, u: stub)

    assert len(stub.calls) == 1
    assert run_calls == []


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

    class _Boom(BaseCodingAgent):
        def start_session(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("CLI died")

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

    class _Boom(BaseCodingAgent):
        def start_session(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("CLI died")

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


# --- Usage metrics (token + step/turn accounting) --------------------------


class _UsageStubSession(BaseAgentSession):
    """Session stand-in that mirrors agentshim's real ``CLIAgentSession``:
    each ``generate`` writes the run's usage back onto ``agent.last_usage``
    (see ``agentshim/cli_agent.py``: ``self.agent.last_usage = ...``).
    """

    def __init__(self, agent: _UsageStubAgent, cwd: str | None, timeout: int) -> None:
        self.agent = agent
        self._cwd = cwd
        self._timeout = timeout
        self.session_id = "stub-session"

    def generate(
        self,
        prompt: str,
        cwd: str | None = None,
        timeout: int | None = None,
        silent: bool | None = None,
        on_process_started: Any | None = None,
    ) -> str:
        self.agent.calls.append({"prompt": prompt})
        # Mirror agentshim: populate last_usage from the just-finished run.
        self.agent.last_usage = self.agent.next_usage
        return self.agent.response


class _UsageStubAgent(BaseCodingAgent):
    """CodingAgent stand-in that exposes ``last_usage`` like the real
    ``CLICodingAgent`` (initialized to a zero ``ProviderUsage`` in
    ``__init__``, overwritten by each ``generate``)."""

    def __init__(self, usage: Any) -> None:
        from agentshim.usage import ProviderUsage

        self.response = "ok"
        self.calls: list[dict[str, Any]] = []
        self.next_usage = usage
        self.last_usage = ProviderUsage()

    def start_session(self, cwd: str | None = None, timeout: int = 300, silent: bool = False) -> _UsageStubSession:
        return _UsageStubSession(self, cwd, timeout)

    def generate(self, prompt: str, cwd: str | None = None, timeout: int = 300, silent: bool = False) -> str:
        self.calls.append({"prompt": prompt})
        self.last_usage = self.next_usage
        return self.response


def test_run_serializes_usage_metrics_with_tokens_and_turns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The results JSON must carry token usage AND step count (turns).

    Regression: every result file in production showed ``usage_metrics:
    null``. The driver already reads ``agent.last_usage`` and serializes
    ``.to_dict()``; this pins the contract so a populated session usage
    survives into the written JSON.
    """
    from agentshim.usage import ProviderUsage, TokenUsage

    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)

    usage = ProviderUsage(
        tokens=TokenUsage(input_tokens=1500, output_tokens=300, cached_input_tokens=200, turns=7),
        total_cost_usd=0.42,
        provider="claude",
    )
    stub = _UsageStubAgent(usage)
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        problem_id="p-7",
        status_sequence=["diagnosis", "done"],
    )
    driver._run(_args(logs_dir=str(tmp_path)), agent_factory=lambda p, m, u: stub)

    files = list(tmp_path.glob("cli_agent_results_p-7_*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["usage_metrics"] is not None, "usage_metrics must not be null"
    total = data["usage_metrics"]["total"]
    assert total["turns"] == 7, "step count (turns) must be recorded"
    assert total["input_tokens"] == 1500
    assert total["output_tokens"] == 300
    assert total["cached_input_tokens"] == 200
    assert total["total_cost_usd"] == 0.42
    assert total["provider"] == "claude"


# --- Memory (incident lessons) ---------------------------------------------


_EXTRACTION_REPLY = json.dumps(
    {
        "decision": "new",
        "merge_into": None,
        "situation": "frontend 503s; profile CrashLoopBackOff",
        "obvious_guess": "bad image tag",
        "root_cause": "missing DB_HOST env var",
        "tell": "kubectl describe shows env unset",
        "fix": "set DB_HOST on profile deployment",
        "affected_resource": "deployment/profile.env",
    }
)


def _memory_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    return _args(memory_enabled=True, memory_dir=str(tmp_path), **overrides)


def _load_lessons(tmp_path: Path, app: str = "myapp") -> list[Any]:
    from sregym_agents.cli_agent.memory.store import LessonStore

    return LessonStore(tmp_path).load(app)


def test_memory_no_write_when_not_confirmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfirmed run must never produce a lesson (§9)."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)

    stub = _StubAgent(extra_replies=[_EXTRACTION_REPLY])
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        status_sequence=["diagnosis", "diagnosis"],  # never reaches terminal
    )
    driver._run(_memory_args(tmp_path), agent_factory=lambda p, m, u: stub)

    assert _load_lessons(tmp_path) == []
    # Only the investigation turn ran — no extraction turn.
    assert len(stub.calls) == 1


def test_memory_writes_on_full_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)

    stub = _StubAgent(extra_replies=[_EXTRACTION_REPLY])
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        status_sequence=["diagnosis", "done"],
    )
    driver._run(_memory_args(tmp_path), agent_factory=lambda p, m, u: stub)

    lessons = _load_lessons(tmp_path)
    assert len(lessons) == 1
    assert lessons[0].confirmed_by == "verdict"
    assert lessons[0].root_cause == "missing DB_HOST env var"
    # Investigation + extraction turns.
    assert len(stub.calls) == 2


def test_memory_partial_confirmation_flags_unverified_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Diagnosis accepted (stage advanced to mitigation) but not completed →
    write the lesson with confirmed_by=diagnosis_only."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)

    stub = _StubAgent(extra_replies=[_EXTRACTION_REPLY])
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        status_sequence=["diagnosis", "mitigation"],  # advanced, never terminal
    )
    driver._run(_memory_args(tmp_path), agent_factory=lambda p, m, u: stub)

    lessons = _load_lessons(tmp_path)
    assert len(lessons) == 1
    assert lessons[0].confirmed_by == "diagnosis_only"


def test_memory_disabled_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)

    stub = _StubAgent(extra_replies=[_EXTRACTION_REPLY])
    _patch_conductor(
        monkeypatch,
        stages=["diagnosis", "mitigation"],
        status_sequence=["diagnosis", "done"],
    )
    # memory_dir set but memory_enabled defaults off (plain _args).
    driver._run(_args(memory_dir=str(tmp_path)), agent_factory=lambda p, m, u: stub)

    assert _load_lessons(tmp_path) == []
    assert len(stub.calls) == 1


def test_memory_wires_recall_server_into_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With memory on, the default factory must hand the agent a `memory` MCP
    server in addition to the sregym submit server."""
    monkeypatch.setenv("API_HOSTNAME", "localhost")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("MCP_SERVER_PORT", "9954")
    monkeypatch.delenv("SREGYM_EXP_ENV", raising=False)
    monkeypatch.delenv("SREGYM_AUTONOMOUS_SUBMIT", raising=False)

    captured: dict[str, Any] = {}

    @register_provider("fake_memory_provider")
    class _FakeCls(BaseCodingAgent):
        def __init__(self, *, model: str, mcp_servers: list[Any]) -> None:
            captured["mcp_servers"] = mcp_servers

        def start_session(self, cwd=None, timeout=300, silent=False) -> Any:
            return _StubSession(_StubAgent(), cwd, timeout)

        def generate(self, prompt: str, cwd=None, timeout=300, silent=False) -> str:
            return ""

    _patch_conductor(
        monkeypatch,
        stages=["diagnosis"],
        status_sequence=["diagnosis", "done"],
    )
    driver._run(_memory_args(tmp_path, provider="fake_memory_provider"))

    names = {s.name for s in captured["mcp_servers"]}
    assert driver._SUBMIT_MCP_SERVER_NAME in names
    assert "memory" in names


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
                "application_workspace_mode": "persistent",
            }
        ),
    )
    ns = driver._parse_args([])
    assert ns.provider == "codex"
    assert ns.model == "my-model"
    assert ns.timeout_sec == 42
    assert ns.autonomous_prompt_profile == "direct"
    assert ns.application_workspace_mode == "persistent"


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


def test_parse_args_accepts_application_workspace_mode() -> None:
    ns = driver._parse_args(["--application-workspace-mode", "ephemeral"])
    assert ns.application_workspace_mode == "ephemeral"


def test_parse_args_rejects_removed_memory_flags() -> None:
    with pytest.raises(SystemExit):
        driver._parse_args(["--memory-store", "/tmp/incidents.db"])


def test_cli_agent_is_registered_as_external_agent() -> None:
    from libs.sregym_lib.experiment import _EXTERNAL_AGENTS

    assert "cli_agent" in _EXTERNAL_AGENTS
