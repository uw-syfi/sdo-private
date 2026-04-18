"""Unit tests for sregym_agents.fault_verifier.

Covers the pure pieces (prompt rendering, JSON extraction) and the
orchestration entry points (run_fault_verifier with a stub agent,
CLI main()). ClaudeCodeCodingAgent is never instantiated here — tests
inject a stub via the agent_factory seam.
"""

from __future__ import annotations

import json

from sregym_agents.fault_verifier import FaultVerification, run_fault_verifier
from sregym_agents.fault_verifier.verifier import build_prompt, parse_agent_output

# --- Prompt rendering ------------------------------------------------------


def test_build_prompt_includes_root_cause_app_name_namespace():
    prompt = build_prompt(
        problem_id="revoke_auth_mongodb-1",
        root_cause="The ConfigMap `geo-config` is missing critical keys.",
        app_name="social_network",
        namespace="social-net",
    )
    assert "revoke_auth_mongodb-1" in prompt
    assert "social_network" in prompt
    assert "social-net" in prompt
    assert "geo-config" in prompt


def test_build_prompt_declares_output_contract():
    prompt = build_prompt(
        problem_id="p",
        root_cause="rc",
        app_name="a",
        namespace="n",
    )
    # Must instruct the agent to emit a JSON block with these three keys.
    assert "fault_confirmed" in prompt
    assert "other_faults" in prompt
    assert "reasoning" in prompt
    assert "```json" in prompt


def test_build_prompt_mentions_kubectl_and_kubeconfig():
    prompt = build_prompt(
        problem_id="p",
        root_cause="rc",
        app_name="a",
        namespace="n",
    )
    assert "kubectl" in prompt.lower()
    # The agent should know the kubeconfig is preconfigured.
    assert "kubeconfig" in prompt.lower() or "KUBECONFIG" in prompt


# --- JSON extraction -------------------------------------------------------


def test_parse_agent_output_happy_path():
    raw = """\
I investigated the cluster and found the expected NetworkPolicy.

```json
{
  "fault_confirmed": true,
  "other_faults": [],
  "reasoning": "Saw the policy blocking payment-service."
}
```
"""
    res = parse_agent_output(raw, elapsed_s=1.5)
    assert res.fault_confirmed is True
    assert res.other_faults == []
    assert "payment-service" in res.reasoning
    assert res.parse_error is None
    assert res.elapsed_s == 1.5
    assert res.raw_output == raw


def test_parse_agent_output_no_json_block():
    raw = "I looked at the cluster but did not emit structured output."
    res = parse_agent_output(raw, elapsed_s=0.5)
    assert res.fault_confirmed is None
    assert res.parse_error is not None
    assert "json" in res.parse_error.lower()
    assert res.raw_output == raw


def test_parse_agent_output_malformed_json():
    raw = "```json\n{not: valid, json}\n```"
    res = parse_agent_output(raw, elapsed_s=0.1)
    assert res.fault_confirmed is None
    assert res.parse_error is not None


def test_parse_agent_output_prefers_last_json_block():
    raw = """\
```json
{"fault_confirmed": false, "other_faults": [], "reasoning": "old guess"}
```
After more investigation:
```json
{"fault_confirmed": true, "other_faults": ["unexpected x"], "reasoning": "final"}
```
"""
    res = parse_agent_output(raw, elapsed_s=0.0)
    assert res.fault_confirmed is True
    assert res.other_faults == ["unexpected x"]
    assert res.reasoning == "final"


def test_parse_agent_output_coerces_other_faults_to_list_of_str():
    raw = """```json
{"fault_confirmed": true, "other_faults": ["a", "b"], "reasoning": ""}
```"""
    res = parse_agent_output(raw, elapsed_s=0.0)
    assert res.other_faults == ["a", "b"]
    assert res.fault_confirmed is True


def test_parse_agent_output_missing_required_key_is_parse_error():
    raw = """```json
{"fault_confirmed": true}
```"""
    res = parse_agent_output(raw, elapsed_s=0.0)
    assert res.fault_confirmed is None
    assert res.parse_error is not None


# --- run_fault_verifier with stub agent ------------------------------------


class _StubAgent:
    def __init__(self, response: str):
        self._response = response
        self.received_prompt: str | None = None
        self.received_timeout: int | None = None
        self.received_cwd: str | None = None

    def generate(self, prompt, cwd=None, timeout=300, silent=False, **kwargs):
        self.received_prompt = prompt
        self.received_timeout = timeout
        self.received_cwd = cwd
        return self._response


def test_run_fault_verifier_passes_prompt_and_timeout_through():
    stub = _StubAgent(response='```json\n{"fault_confirmed": true, "other_faults": [], "reasoning": "ok"}\n```')
    res = run_fault_verifier(
        problem_id="pid-1",
        root_cause="NetworkPolicy X blocks Y",
        app_name="social_network",
        namespace="social-net",
        kubeconfig_path="/tmp/kc",
        timeout_s=120,
        agent_factory=lambda: stub,
    )
    assert isinstance(res, FaultVerification)
    assert res.fault_confirmed is True
    assert res.parse_error is None
    assert stub.received_prompt is not None
    assert "NetworkPolicy X blocks Y" in stub.received_prompt
    assert stub.received_timeout == 120


def test_run_fault_verifier_handles_agent_exception():
    class _Boom:
        def generate(self, *a, **kw):
            raise RuntimeError("claude exploded")

    res = run_fault_verifier(
        problem_id="pid",
        root_cause="x",
        app_name="a",
        namespace="ns",
        kubeconfig_path="/tmp/kc",
        agent_factory=_Boom,
    )
    assert res.fault_confirmed is None
    assert res.parse_error is not None
    assert "claude exploded" in res.parse_error


def test_run_fault_verifier_records_elapsed_time():
    stub = _StubAgent(response='```json\n{"fault_confirmed": false, "other_faults": [], "reasoning": "nope"}\n```')
    res = run_fault_verifier(
        problem_id="pid",
        root_cause="rc",
        app_name="a",
        namespace="ns",
        kubeconfig_path="/tmp/kc",
        agent_factory=lambda: stub,
    )
    assert res.elapsed_s >= 0.0
    assert res.fault_confirmed is False


# --- CLI main() ------------------------------------------------------------


def test_main_writes_output_json(tmp_path, monkeypatch):
    from sregym_agents.fault_verifier import __main__ as cli

    captured = {}

    def fake_run_fault_verifier(**kwargs):
        captured.update(kwargs)
        return FaultVerification(
            fault_confirmed=True,
            other_faults=[],
            reasoning="all good",
            raw_output="raw",
            parse_error=None,
            elapsed_s=2.5,
        )

    monkeypatch.setattr(cli, "run_fault_verifier", fake_run_fault_verifier)

    out_path = tmp_path / "out.json"
    rc = cli.main(
        [
            "--problem-id",
            "pid",
            "--root-cause",
            "some fault",
            "--app-name",
            "social_network",
            "--namespace",
            "social-net",
            "--kubeconfig-path",
            "/tmp/kc",
            "--output",
            str(out_path),
            "--timeout-sec",
            "77",
        ]
    )
    assert rc == 0
    data = json.loads(out_path.read_text())
    assert data["fault_confirmed"] is True
    assert data["reasoning"] == "all good"
    assert data["elapsed_s"] == 2.5
    # Args propagated to run_fault_verifier
    assert captured["problem_id"] == "pid"
    assert captured["root_cause"] == "some fault"
    assert captured["app_name"] == "social_network"
    assert captured["namespace"] == "social-net"
    assert captured["kubeconfig_path"] == "/tmp/kc"
    assert captured["timeout_s"] == 77


def test_main_exits_zero_on_parse_error(tmp_path, monkeypatch):
    """Parse errors are recorded in the JSON, not raised as non-zero exit."""
    from sregym_agents.fault_verifier import __main__ as cli

    def fake_run_fault_verifier(**kwargs):
        return FaultVerification(
            fault_confirmed=None,
            other_faults=[],
            reasoning="",
            raw_output="",
            parse_error="no JSON block",
            elapsed_s=0.1,
        )

    monkeypatch.setattr(cli, "run_fault_verifier", fake_run_fault_verifier)
    out_path = tmp_path / "out.json"
    rc = cli.main(
        [
            "--problem-id",
            "pid",
            "--root-cause",
            "rc",
            "--app-name",
            "a",
            "--namespace",
            "ns",
            "--kubeconfig-path",
            "/tmp/kc",
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    data = json.loads(out_path.read_text())
    assert data["fault_confirmed"] is None
    assert data["parse_error"] == "no JSON block"
