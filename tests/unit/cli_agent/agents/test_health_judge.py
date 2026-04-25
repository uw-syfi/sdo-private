import subprocess
from typing import Any

import pytest
from agentshim import BaseCodingAgent

from app_operator.cli_agent.agents.health_judge import AppHealthJudge
from app_operator.core import AgentError
from app_operator.prompts import PromptLoader


class PlainStubAgent(BaseCodingAgent):
    """Agent that returns exactly the configured response, without auto-detection."""

    def __init__(self, response="stub response"):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt, cwd=None, timeout=300, **kwargs):  # type: ignore[override]
        self.calls.append({"prompt": prompt, "cwd": cwd, "timeout": timeout})
        return self.response


HEALTHY_RESPONSE = (
    "All services are running.\n"
    "<health_verdict>healthy</health_verdict>\n"
    "<health_assessment>All 5 services healthy.</health_assessment>\n"
    "<diagnosis></diagnosis>\n"
    "<script_fixed>false</script_fixed>"
)

UNHEALTHY_RESPONSE = (
    "Some services are down.\n"
    "<health_verdict>unhealthy</health_verdict>\n"
    "<health_assessment>MongoDB is crashing.</health_assessment>\n"
    "<diagnosis>mongodb: OOMKill</diagnosis>\n"
    "<script_fixed>false</script_fixed>"
)

SCRIPT_FIXED_RESPONSE = (
    "<health_verdict>healthy</health_verdict>\n"
    "<health_assessment>Fixed script to use correct service names.</health_assessment>\n"
    "<diagnosis></diagnosis>\n"
    "<script_fixed>true</script_fixed>"
)

GARBAGE_RESPONSE = "I looked at the services and everything seems fine, no XML here."


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sds = repo / ".sds"
    sds.mkdir()
    hc = sds / "health_check.sh"
    hc.write_text("#!/bin/bash\nexit 0\n")
    hc.chmod(0o755)
    return repo


@pytest.fixture
def health_check_script(repo_path):
    return repo_path / ".sds" / "health_check.sh"


def _make_judge(repo_path, health_check_script, agent, **kwargs):
    return AppHealthJudge(
        repo_path=repo_path,
        coding_agent=agent,
        health_check_script=health_check_script,
        **kwargs,
    )


# --- Core parsing tests ---


def test_assess_parses_healthy_verdict(repo_path, health_check_script):
    agent = PlainStubAgent(response=HEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()
    assert verdict.healthy is True
    assert verdict.diagnosis == ""


def test_assess_parses_unhealthy_verdict(repo_path, health_check_script):
    agent = PlainStubAgent(response=UNHEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()
    assert verdict.healthy is False
    assert "MongoDB" in verdict.assessment


def test_assess_parses_diagnosis(repo_path, health_check_script):
    agent = PlainStubAgent(response=UNHEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()
    assert verdict.diagnosis == "mongodb: OOMKill"


def test_assess_healthy_has_empty_diagnosis(repo_path, health_check_script):
    # Even if the agent puts text in <diagnosis>, healthy verdicts get empty diagnosis
    response = (
        "<health_verdict>healthy</health_verdict>\n"
        "<health_assessment>All good.</health_assessment>\n"
        "<diagnosis>should be ignored</diagnosis>\n"
        "<script_fixed>false</script_fixed>"
    )
    agent = PlainStubAgent(response=response)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()
    assert verdict.healthy is True
    assert verdict.diagnosis == ""


def test_assess_detects_script_fixed(repo_path, health_check_script):
    agent = PlainStubAgent(response=SCRIPT_FIXED_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()
    assert verdict.script_was_fixed is True


# --- Retry tests ---


def test_assess_retries_on_unparseable_response(repo_path, health_check_script):
    call_count = {"n": 0}
    responses = [GARBAGE_RESPONSE, HEALTHY_RESPONSE]

    class MultiAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            idx = call_count["n"]
            call_count["n"] += 1
            self.response = responses[min(idx, len(responses) - 1)]
            return super().generate(prompt, **kwargs)

    agent = MultiAgent()
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()

    assert call_count["n"] == 2
    assert verdict.healthy is True


def test_assess_exhausts_retries(repo_path, health_check_script):
    agent = PlainStubAgent(response=GARBAGE_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess(max_retries=2)

    assert verdict.healthy is False
    assert "structured output" in verdict.assessment
    assert len(agent.calls) == 3  # 1 initial + 2 retries


# --- Error handling tests ---


def test_assess_handles_agent_error(repo_path, health_check_script):
    class FailAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            raise AgentError("agent crashed")

    judge = _make_judge(repo_path, health_check_script, FailAgent())
    verdict = judge.assess()
    assert verdict.healthy is False
    assert "agent crashed" in verdict.assessment


def test_assess_handles_timeout(repo_path, health_check_script):
    class TimeoutAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            raise subprocess.TimeoutExpired(cmd="agent", timeout=300)

    judge = _make_judge(repo_path, health_check_script, TimeoutAgent())
    verdict = judge.assess()
    assert verdict.healthy is False
    assert "Agent failed" in verdict.assessment


# --- Trajectory recording tests ---


def test_assess_records_trajectory(repo_path, health_check_script):
    from app_operator.trajectory import NullTrajectoryRecorder

    class TrackingRecorder(NullTrajectoryRecorder):
        def __init__(self):
            self.messages = []

        def add_assistant_message(self, msg, **kwargs):  # type: ignore[override]
            self.messages.append(msg)

    recorder = TrackingRecorder()
    agent = PlainStubAgent(response=HEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent, recorder=recorder)
    judge.assess()
    assert any("healthy" in m for m in recorder.messages)


# --- Template rendering test ---


def test_assess_renders_correct_template(repo_path, health_check_script, monkeypatch):
    rendered_calls = []

    class FakeLoader:
        def render(self, template_name, **kwargs):
            rendered_calls.append((template_name, kwargs))
            return "fake prompt"

    import app_operator.cli_agent.agents.health_judge as hj_module

    monkeypatch.setattr(hj_module, "get_loader", lambda dspy_config: FakeLoader())

    agent = PlainStubAgent(response=HEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent)
    judge.assess()

    # The monkeypatched get_loader captures the user prompt render call
    assert len(rendered_calls) == 1
    template_name, kwargs = rendered_calls[0]
    assert template_name == "health_judge_agent/user.jinja2"
    assert kwargs["repo_path"] == repo_path
    assert kwargs["health_check_script"] == health_check_script
    assert "platform" in kwargs
    assert kwargs["structured_output"] is False


# --- Template content tests ---


def test_assess_health_template_xml_when_structured_output_false(repo_path, health_check_script):
    loader = PromptLoader()
    rendered = loader.render(
        "deployer/assess_health.jinja2",
        repo_path=repo_path,
        health_check_script=health_check_script,
        platform="docker",
        structured_output=False,
    )
    assert "<health_verdict>" in rendered
    assert "<health_assessment>" in rendered
    assert "<script_fixed>" in rendered
    assert "structured response" not in rendered


def test_assess_health_template_no_xml_when_structured_output_true(repo_path, health_check_script):
    loader = PromptLoader()
    rendered = loader.render(
        "deployer/assess_health.jinja2",
        repo_path=repo_path,
        health_check_script=health_check_script,
        platform="docker",
        structured_output=True,
    )
    assert "<health_verdict>" not in rendered
    assert "structured response" in rendered
    assert "**healthy**" in rendered
    assert "**script_was_fixed**" in rendered


def test_assess_health_template_forbids_runtime_mutations(repo_path, health_check_script):
    loader = PromptLoader()
    rendered = loader.render(
        "deployer/assess_health.jinja2",
        repo_path=repo_path,
        health_check_script=health_check_script,
        platform="docker",
        structured_output=False,
    )
    assert "NEVER install packages" in rendered
    assert "transient startup log noise" in rendered


# --- Robustness tests ---


def test_assess_survives_agent_runtime_error(repo_path, health_check_script):
    class BadAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            raise RuntimeError("unexpected crash")

    judge = _make_judge(repo_path, health_check_script, BadAgent())
    verdict = judge.assess()
    assert verdict.healthy is False
    assert "unexpected crash" in verdict.assessment


def test_assess_survives_agent_oserror(repo_path, health_check_script):
    class BadAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            raise OSError("binary not found")

    judge = _make_judge(repo_path, health_check_script, BadAgent())
    verdict = judge.assess()
    assert verdict.healthy is False
    assert "binary not found" in verdict.assessment


def test_assess_survives_agent_keyboard_interrupt(repo_path, health_check_script):
    class BadAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            raise KeyboardInterrupt

    judge = _make_judge(repo_path, health_check_script, BadAgent())
    with pytest.raises(KeyboardInterrupt):
        judge.assess()


def test_assess_survives_agent_returns_none(repo_path, health_check_script):
    class NoneAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            return None

    judge = _make_judge(repo_path, health_check_script, NoneAgent())
    verdict = judge.assess(max_retries=1)
    assert verdict.healthy is False


def test_assess_survives_agent_returns_empty_string(repo_path, health_check_script):
    call_count = {"n": 0}

    class EmptyThenValid(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ""
            return HEALTHY_RESPONSE

    judge = _make_judge(repo_path, health_check_script, EmptyThenValid())
    verdict = judge.assess()
    assert call_count["n"] == 2
    assert verdict.healthy is True


def test_assess_survives_agent_returns_huge_response(repo_path, health_check_script):
    huge = "x" * 1_000_000 + "\n" + HEALTHY_RESPONSE
    agent = PlainStubAgent(response=huge)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()
    assert verdict.healthy is True


def test_assess_survives_malformed_xml_tags(repo_path, health_check_script):
    # Unclosed tag
    response = "<health_verdict>healthy\n<health_assessment>ok</health_assessment>"
    agent = PlainStubAgent(response=response)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess(max_retries=0)
    assert verdict.healthy is False

    # Empty tag
    response2 = "<health_verdict></health_verdict>\n<health_assessment>ok</health_assessment>"
    agent2 = PlainStubAgent(response=response2)
    judge2 = _make_judge(repo_path, health_check_script, agent2)
    verdict2 = judge2.assess(max_retries=0)
    assert verdict2.healthy is False


def test_assess_survives_recorder_error(repo_path, health_check_script):
    from app_operator.trajectory import NullTrajectoryRecorder

    class BrokenRecorder(NullTrajectoryRecorder):
        def add_assistant_message(self, msg, **kwargs):  # type: ignore[override]
            raise RuntimeError("recorder broken")

    agent = PlainStubAgent(response=HEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent, recorder=BrokenRecorder())
    verdict = judge.assess()
    assert verdict.healthy is True


def test_assess_survives_prompt_render_error(repo_path, health_check_script, monkeypatch):
    import app_operator.cli_agent.agents.health_judge as hj_module

    class BrokenLoader:
        def render(self, *args, **kwargs):
            raise RuntimeError("template not found")

    monkeypatch.setattr(hj_module, "get_loader", lambda dspy_config: BrokenLoader())

    agent = PlainStubAgent(response=HEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent)
    verdict = judge.assess()
    assert verdict.healthy is False
    assert "render" in verdict.assessment.lower() or "template" in verdict.assessment.lower()


def test_assess_survives_filesystem_error(repo_path, health_check_script):
    from app_operator.core import RealFilesystem

    class BrokenFS(RealFilesystem):
        def exists(self, path):
            raise OSError("disk error")

    # filesystem errors during construction shouldn't matter;
    # the judge only uses filesystem indirectly via prompts
    agent = PlainStubAgent(response=HEALTHY_RESPONSE)
    judge = _make_judge(repo_path, health_check_script, agent, filesystem=BrokenFS())
    verdict = judge.assess()
    assert verdict.healthy is True


def test_assess_timeout_does_not_hang(repo_path, health_check_script):
    class TimeoutAgent(PlainStubAgent):
        def generate(self, prompt, **kwargs):  # type: ignore[override]
            raise subprocess.TimeoutExpired(cmd="agent", timeout=10)

    judge = _make_judge(repo_path, health_check_script, TimeoutAgent())
    verdict = judge.assess()
    assert verdict.healthy is False
    assert "Agent failed" in verdict.assessment
