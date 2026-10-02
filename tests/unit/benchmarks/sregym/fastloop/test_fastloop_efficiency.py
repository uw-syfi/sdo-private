from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.fastloop import cli
from benchmarks.sregym.fastloop.cli import build_parser
from benchmarks.sregym.fastloop.environment import FastloopEnvironment, Images
from sdo.agent_runtime.lifecycle.seed_cache import CACHE_DIR_ENV as SEED_ENV
from sdo.agent_runtime.lifecycle.validation_cache import CACHE_DIR_ENV as VALIDATION_ENV

if TYPE_CHECKING:
    from pathlib import Path


def _args(run_dir: Path, *extra: str):
    return build_parser().parse_args(["run", "--run-dir", str(run_dir), "--agent", "sdo", *extra])


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(SEED_ENV, raising=False)
    monkeypatch.delenv(VALIDATION_ENV, raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


def test_the_validation_cache_is_shared_by_runs_with_different_run_dirs(tmp_path: Path) -> None:
    first = cli._validation_cache(_args(tmp_path / "a"))
    second = cli._validation_cache(_args(tmp_path / "b"))

    assert first is not None
    assert second is not None
    assert first.directory == second.directory
    assert tmp_path / "a" not in first.directory.parents
    assert first.directory.is_relative_to(tmp_path / "home")


def test_the_validation_cache_directory_can_be_chosen_and_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VALIDATION_ENV, str(tmp_path / "chosen"))
    chosen = cli._validation_cache(_args(tmp_path / "a"))
    assert chosen is not None
    assert chosen.directory == tmp_path / "chosen"

    assert cli._validation_cache(_args(tmp_path / "a", "--no-validation-cache")) is None


def test_the_cold_lifecycle_is_cached_for_the_next_run_by_default(tmp_path: Path) -> None:
    first = cli._seed_cache(_args(tmp_path / "a"))
    second = cli._seed_cache(_args(tmp_path / "b"))

    assert first is not None
    assert second is not None
    assert first.directory == second.directory
    assert first.directory.is_relative_to(tmp_path / "home")


def test_a_cold_lifecycle_run_does_not_use_the_cache(tmp_path: Path) -> None:
    assert cli._seed_cache(_args(tmp_path / "a", "--cold-lifecycle")) is None


def test_the_seed_cache_directory_can_be_chosen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SEED_ENV, str(tmp_path / "env-seeds"))
    from_env = cli._seed_cache(_args(tmp_path / "a"))
    assert from_env is not None
    assert from_env.directory == tmp_path / "env-seeds"

    chosen = cli._seed_cache(_args(tmp_path / "a", "--lifecycle-seed-cache-dir", str(tmp_path / "flag-seeds")))
    assert chosen is not None
    assert chosen.directory == tmp_path / "flag-seeds"


def test_the_run_command_takes_a_detection_timeout() -> None:
    assert build_parser().parse_args(["run", "--run-dir", "/x", "--agent", "sdo"]).detection_timeout is None
    parsed = build_parser().parse_args(["run", "--run-dir", "/x", "--agent", "sdo", "--detection-timeout", "240"])
    assert parsed.detection_timeout == 240.0


def test_the_sdo_agent_gets_the_seed_cache_and_the_detection_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from benchmarks.sregym import adapter
    from benchmarks.sregym.fastloop import sdo_agent

    captured: dict[str, object] = {}
    lifecycle_calls: list[dict[str, object]] = []

    class RecordingAgent:
        def __init__(self, settings: sdo_agent.SdoAgentSettings, **arguments: object) -> None:
            captured["settings"] = settings
            captured.update(arguments)

    def record_lifecycle(*_args: object, **kwargs: object) -> bool:
        lifecycle_calls.append(kwargs)
        return False

    monkeypatch.setattr(sdo_agent, "SdoPersistentAgent", RecordingAgent)
    monkeypatch.setattr(adapter, "run_or_reuse_lifecycle", record_lifecycle)
    environment = FastloopEnvironment(
        run_dir=tmp_path,
        cluster="fastloop-w0",
        kubeconfig=tmp_path / "kubeconfig",
        namespace="hotel-reservation",
        application="Hotel Reservation",
        workspace=tmp_path / "workspace",
        sregym_dir=tmp_path / "sregym",
        private_tmp=None,
        images=Images(controller="sdo-controller:t1", responder="sdo-sregym-responder:t1", validator="v:t1"),
    )

    cli._sdo_agent(_args(tmp_path, "--detection-timeout", "240"), environment, tmp_path / "results")
    captured["run_lifecycle"](object())  # type: ignore[operator]

    assert captured["settings"].detection_timeout_seconds == 240.0  # type: ignore[attr-defined]
    seed_cache = lifecycle_calls[0]["seed_cache"]
    assert seed_cache is not None
    assert seed_cache.directory.is_relative_to(tmp_path / "home")  # type: ignore[attr-defined]

    lifecycle_calls.clear()
    cli._sdo_agent(_args(tmp_path, "--cold-lifecycle"), environment, tmp_path / "results")
    captured["run_lifecycle"](object())  # type: ignore[operator]
    assert lifecycle_calls[0]["seed_cache"] is None


def _up_args(*extra: str):
    return build_parser().parse_args(["up", "--run-dir", "/x", *extra])


def test_up_holds_for_calm_host_load_by_default_and_records_it(tmp_path: Path) -> None:
    from benchmarks.sregym.fastloop.hostguard import LoadPolicy, LoadWait

    policies: list[LoadPolicy] = []

    def fake_wait(policy: LoadPolicy, *_clock: object) -> LoadWait:
        policies.append(policy)
        return LoadWait(calm=True, waited_seconds=42.0, final_load=9.0, peak_load=31.0)

    result = cli._hold_for_calm_load(_up_args(), tmp_path, wait=fake_wait)

    assert result is not None
    assert policies[0].max_load == 20.0
    recorded = json.loads((tmp_path / "host-load.json").read_text(encoding="utf-8"))
    assert recorded["peak_load"] == 31.0
    assert recorded["calm"] is True


def test_up_proceeds_but_flags_the_run_when_the_host_never_calms_down(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from benchmarks.sregym.fastloop.hostguard import LoadWait

    def fake_wait(*_args: object) -> LoadWait:
        return LoadWait(calm=False, waited_seconds=1800.0, final_load=44.0, peak_load=66.0)

    with caplog.at_level("WARNING"):
        result = cli._hold_for_calm_load(_up_args(), tmp_path, wait=fake_wait)

    assert result is not None
    assert result.calm is False
    assert "load-contaminated" in caplog.text
    assert json.loads((tmp_path / "host-load.json").read_text(encoding="utf-8"))["calm"] is False


def test_the_load_governor_can_be_turned_off_or_tuned(tmp_path: Path) -> None:
    def fail_wait(*_args: object):
        raise AssertionError("must not wait")

    assert cli._hold_for_calm_load(_up_args("--no-load-governor"), tmp_path, wait=fail_wait) is None
    parsed = _up_args("--max-load", "12", "--calm-seconds", "30", "--max-load-wait", "600")
    assert (parsed.max_load, parsed.calm_seconds, parsed.max_load_wait) == (12.0, 30.0, 600.0)


def test_injecting_before_resume_warns_that_the_link_probe_has_no_baseline(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        cli._cluster_ops(_args(tmp_path, "--inject-before-resume"), "hotel-reservation")

    assert "link probe" in caplog.text
    assert "NetworkPolicy" in caplog.text


def test_resuming_normally_does_not_warn_about_the_link_probe(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        cli._cluster_ops(_args(tmp_path), "hotel-reservation")

    assert "link probe" not in caplog.text
