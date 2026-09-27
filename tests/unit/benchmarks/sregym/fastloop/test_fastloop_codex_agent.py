from __future__ import annotations

import json
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
import pytest

from benchmarks.sregym.fastloop.codex_agent import (
    CodexBaselineAgent,
    CodexSettings,
    SubmissionStub,
    codex_command,
    codex_usage,
)
from benchmarks.sregym.fastloop.loop import InjectionWindow

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def test_stub_walks_diagnosis_then_mitigation_and_records_submissions() -> None:
    with SubmissionStub(app_info={"app_name": "Hotel Reservation", "namespace": "hotel-reservation"}) as stub:
        stub.reset()
        base = stub.url
        assert httpx.get(f"{base}/status").json() == {"stage": "diagnosis"}
        assert httpx.get(f"{base}/get_app").json()["namespace"] == "hotel-reservation"
        assert httpx.post(f"{base}/submit", json={"solution": "configmap missing"}).status_code == 200
        assert httpx.get(f"{base}/status").json() == {"stage": "mitigation"}
        assert httpx.post(f"{base}/submit", json={"solution": ""}).status_code == 200
        assert httpx.get(f"{base}/status").json() == {"stage": "done"}
        done = httpx.post(f"{base}/submit", json={"solution": "again"})
        assert done.json()["status"] == "done"
        assert httpx.post(f"{base}/submit", content=b"not json").status_code == 400

        submissions = stub.submissions()
        assert [(item.stage, item.solution) for item in submissions] == [
            ("diagnosis", "configmap missing"),
            ("mitigation", ""),
        ]
        assert submissions[0].received_at <= submissions[1].received_at
        stub.reset()
        assert stub.submissions() == []


def test_command_mirrors_the_benchmark_client_invocation() -> None:
    command = codex_command("codex", model="gpt-6-luna", reasoning_effort=None, prompt="fix it")

    assert command == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--model",
        "gpt-6-luna",
        "--json",
        "--enable",
        "unified_exec",
        "--",
        "fix it",
    ]
    assert "model_reasoning_effort=high" in codex_command("codex", model="m", reasoning_effort="high", prompt="p")


def test_usage_is_the_last_usage_event_like_the_benchmark_client() -> None:
    stream = "\n".join(
        [
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 1}}),
            "not json",
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 900, "cached_input_tokens": 800, "output_tokens": 20},
                }
            ),
        ]
    )

    usage = codex_usage(stream)

    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (900, 800, 20)


FAKE_CODEX = textwrap.dedent(
    """
    import json, os, sys, urllib.request
    prompt = sys.argv[-1]
    base = prompt.split("endpoint is: ")[1].split("/submit")[0]
    def post(solution):
        request = urllib.request.Request(base + "/submit", data=json.dumps({"solution": solution}).encode(),
                                         headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request).read()
    assert os.environ["KUBECONFIG"].endswith("agent.kubeconfig")
    home = os.environ["CODEX_HOME"]
    # A fresh home per incident: the baseline must not carry Codex memories between incidents.
    assert home.endswith("_missing_configmap_hotel_reservation/codex_home"), home
    assert sorted(os.listdir(home)) == ["auth.json"], os.listdir(home)
    open(os.path.join(home, "memories_1.sqlite"), "w").write("learned")
    post("mongo-geo-script ConfigMap was deleted")
    post("")
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 500, "cached_input_tokens": 400,
                                                         "output_tokens": 7}}))
    """
)


def test_agent_injects_runs_codex_and_reports_submissions_and_tokens(tmp_path: Path) -> None:
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE_CODEX, encoding="utf-8")
    launcher = tmp_path / "codex"
    launcher.write_text(f'#!/bin/sh\nexec {sys.executable} {script} "$@"\n', encoding="utf-8")
    launcher.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text('{"token": "x"}', encoding="utf-8")
    settings = CodexSettings(
        model="gpt-6-luna",
        codex_binary=str(launcher),
        auth_file=auth,
        kubeconfig=tmp_path / "agent.kubeconfig",
        results_dir=tmp_path / "incidents",
        timeout_seconds=60,
    )
    injected: list[str] = []

    def inject() -> InjectionWindow:
        injected.append("fault")
        return InjectionWindow(started_at=T0, finished_at=T0 + timedelta(seconds=6))

    with SubmissionStub(app_info={"app_name": "Hotel Reservation", "namespace": "hotel-reservation"}) as stub:
        agent = CodexBaselineAgent(
            settings,
            stub=stub,
            prompt_for=lambda problem_id, api_base: f"Fix {problem_id}. The submission endpoint is: {api_base}/submit",
        )
        outcome = agent.resolve(0, "missing_configmap_hotel_reservation", inject)
        second = agent.resolve(1, "missing_configmap_hotel_reservation", inject)

    assert injected == ["fault", "fault"]
    assert second.error is None
    assert outcome.diagnosis == "mongo-geo-script ConfigMap was deleted"
    assert outcome.mitigation_applied_at is not None
    assert outcome.resolved_at is not None
    assert outcome.mitigation_applied_at <= outcome.resolved_at
    assert outcome.responder_tokens.input_tokens == 500
    assert outcome.warm_path is None
    assert outcome.detected_at is None
    assert agent.learn(outcome) == outcome
    log = tmp_path / "incidents" / "000_missing_configmap_hotel_reservation" / "codex.jsonl"
    assert "turn.completed" in log.read_text(encoding="utf-8")


def test_settings_validate_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout"):
        CodexSettings(
            model="m",
            auth_file=tmp_path / "auth.json",
            kubeconfig=tmp_path / "k",
            results_dir=tmp_path,
            timeout_seconds=0,
        )
