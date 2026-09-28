"""The opt-in "Codex + verify" baseline: config, prompt text and its route to SREGym."""

from __future__ import annotations

import subprocess
import textwrap
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.runner.codex_baseline import (
    PROMPT_APPENDIX_ENV,
    VERIFY_PROTOCOL_PROMPT,
    CodexBaselineConfig,
    PromptAppendixImageError,
    ensure_agent_image_supports_prompt_appendix,
)
from benchmarks.sregym.runner.experiment import (
    ExperimentConfig,
    _serialize_config,
    config_to_env,
    load_experiment_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def _codex(agent_config: dict | None = None) -> ExperimentConfig:
    return ExperimentConfig(agent="codex", model="gpt-6-luna", agent_config=agent_config or {})


def test_the_protocol_is_off_by_default() -> None:
    assert CodexBaselineConfig().verify_protocol is False
    assert CodexBaselineConfig().prompt_appendix() == ""


def test_the_protocol_config_rejects_unknown_keys_and_non_booleans() -> None:
    with pytest.raises(ValueError, match="verify_protocl"):
        CodexBaselineConfig.from_agent_config({"verify_protocl": True})
    with pytest.raises(TypeError, match="verify_protocol"):
        CodexBaselineConfig.from_agent_config({"verify_protocol": "yes"})


def test_an_invalid_codex_agent_config_fails_at_load(tmp_path: Path) -> None:
    source = tmp_path / "exp.toml"
    source.write_text('[runner]\nagent = "codex"\n\n[agent.codex]\nverify_protocl = true\n', encoding="utf-8")

    with pytest.raises(ValueError, match="verify_protocl"):
        load_experiment_config(source)


@pytest.mark.parametrize(
    "requirement",
    [
        # (a) reproduce a user-facing symptom first and record it
        "reproduce a concrete user-facing symptom",
        "end-to-end request",
        "no ready endpoints",
        "exact command you ran and its output",
        # (b) evidence tying the root cause to the symptom
        "evidence that ties the root cause to that symptom",
        # (c) re-run the same check after the fix until it passes, then submit
        "re-run the same symptom check",
        "until it passes",
        "Only then submit the mitigation",
        # (d) delete helper pods and Jobs before submitting
        "delete every helper pod, Job",
    ],
)
def test_the_protocol_prompt_states_each_requirement(requirement: str) -> None:
    assert requirement in VERIFY_PROTOCOL_PROMPT


@pytest.mark.parametrize(
    "hint", ["configmap", "failure-admin", "mongo", "hotel", "decoy", "red herring", "geo", "rate", "selector"]
)
def test_the_protocol_prompt_names_no_fault_or_decoy(hint: str) -> None:
    assert hint not in VERIFY_PROTOCOL_PROMPT.lower()


def test_verify_protocol_reaches_sregym_as_the_prompt_appendix(tmp_path: Path) -> None:
    env = config_to_env(_codex({"codex": {"verify_protocol": True}}), tmp_path)

    assert env[PROMPT_APPENDIX_ENV] == VERIFY_PROTOCOL_PROMPT


def test_the_plain_baseline_never_inherits_a_stray_appendix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROMPT_APPENDIX_ENV, "leaked from the shell")

    assert PROMPT_APPENDIX_ENV not in config_to_env(_codex(), tmp_path)
    assert PROMPT_APPENDIX_ENV not in config_to_env(_codex({"codex": {"verify_protocol": False}}), tmp_path)
    assert PROMPT_APPENDIX_ENV not in config_to_env(ExperimentConfig(agent="sdo_codex"), tmp_path)


def test_verify_protocol_survives_the_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "exp.toml"
    source.write_text(
        textwrap.dedent(
            """
            [runner]
            agent = "codex"

            [agent.codex]
            verify_protocol = true
            """
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "snapshot.toml"
    snapshot.write_text(_serialize_config(load_experiment_config(source)), encoding="utf-8")

    assert load_experiment_config(snapshot).agent_config["codex"] == {"verify_protocol": True}


class _FakeDocker:
    def __init__(self, *, image_exists: bool, has_hook: bool) -> None:
        self.image_exists = image_exists
        self.has_hook = has_hook
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if self.image_exists else 1, "", "")
        return subprocess.CompletedProcess(argv, 0 if self.has_hook else 1, "", "")


def test_the_image_check_is_skipped_without_an_appendix() -> None:
    docker = _FakeDocker(image_exists=True, has_hook=False)

    ensure_agent_image_supports_prompt_appendix({}, run=docker)

    assert docker.calls == []


def test_a_missing_image_passes_because_sregym_builds_it_from_this_checkout() -> None:
    docker = _FakeDocker(image_exists=False, has_hook=False)

    ensure_agent_image_supports_prompt_appendix({PROMPT_APPENDIX_ENV: "x"}, run=docker)

    assert len(docker.calls) == 1


def test_a_stale_image_without_the_hook_is_rejected_with_the_rebuild_command() -> None:
    docker = _FakeDocker(image_exists=True, has_hook=False)

    with pytest.raises(PromptAppendixImageError, match="docker/agents/build.sh"):
        ensure_agent_image_supports_prompt_appendix({PROMPT_APPENDIX_ENV: "x"}, run=docker)

    ensure_agent_image_supports_prompt_appendix(
        {PROMPT_APPENDIX_ENV: "x"}, run=_FakeDocker(image_exists=True, has_hook=True)
    )
