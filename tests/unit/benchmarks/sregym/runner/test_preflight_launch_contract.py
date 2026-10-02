"""The launch-contract checks inside the launch preflight: they run for SDO arms and fail with actionable errors."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest
from sregym_fake_host import CODEX_PIN, REPOSITORY_ROOT, FakeHost

from benchmarks.sregym.runner.experiment import ExperimentConfig, RunnerEnv
from benchmarks.sregym.runner.launch_contract import CONTROLLER_HELP_COMMAND
from benchmarks.sregym.runner.preflight import PreflightReport, PreflightSettings, check_images, run_preflight
from sdo.controller_install import controller_launch_flags

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_790_000_000.0


def _sdo(**sdo_codex: object) -> ExperimentConfig:
    env = RunnerEnv(judge_model_id="codex-gpt-6-luna", worker_cpu_limit="3", kind_worker_nodes=1, reuse_cluster=True)
    section = {
        "provider": "codex",
        "model": "gpt-6-luna",
        "controller_image": "sdo-controller:mi2",
        "responder_image": "sdo-sregym-responder:mi2",
        "validator_image": "sdo-detector-validator:mi2",
        **sdo_codex,
    }
    return ExperimentConfig(
        agent="sdo_codex",
        model="gpt-6-luna",
        reasoning_effort="medium",
        parallel=1,
        agent_timeout=3600,
        app_filter="hotel_reservation",
        deploy_from_source=True,
        env=env,
        agent_config={"sdo_codex": section},
    )


@pytest.fixture
def sregym_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "sregym"
    (directory / "logs").mkdir(parents=True)
    (directory / "SREGym-applications" / "hotelReservation" / "helm-chart").mkdir(parents=True)
    (directory / "agents.yaml").write_text(
        f"agents:\n- name: codex\n  install_script: install-codex.sh\n  agent_version: {CODEX_PIN}\n", encoding="utf-8"
    )
    return directory


def _run(host: FakeHost, sregym_dir: Path, *configs: ExperimentConfig, **settings: float) -> PreflightReport:
    return run_preflight(
        list(configs),
        project_root=REPOSITORY_ROOT,
        sregym_dir=sregym_dir,
        env={},
        host=host,
        settings=PreflightSettings(**settings),
        now=NOW,
    )


def _check(report: PreflightReport, name: str):
    return next(check for check in report.checks if check.name == name)


def test_a_current_image_set_passes_every_launch_check(fake_host: FakeHost, sregym_dir: Path) -> None:
    report = _run(fake_host, sregym_dir, _sdo(closeout_state_gate=True, max_follow_ups=2))

    for name in ("image-tags", "controller-flags", "validator-sdk", "launch-lint", "host-load", "codex-auth"):
        assert _check(report, name).status == "pass", _check(report, name)


def test_a_controller_image_without_the_closeout_flag_fails_the_preflight(
    fake_host: FakeHost, sregym_dir: Path
) -> None:
    """Regression: the controller pods exited on --closeout-state-gate and no incident ever ran."""

    flags = controller_launch_flags(controller_namespace="x", closeout_state_gate=True, max_follow_ups=2)
    fake_host.controller_help["sdo-controller:mi2"] = "\n".join(sorted(flags - {"--closeout-state-gate"}))

    report = _run(fake_host, sregym_dir, _sdo(closeout_state_gate=True, max_follow_ups=2))

    check = _check(report, "controller-flags")
    assert check.status == "fail"
    assert "--closeout-state-gate" in check.detail
    assert not report.ok


def test_a_validator_image_without_the_links_field_fails_the_preflight(fake_host: FakeHost, sregym_dir: Path) -> None:
    """Regression: the stale validator rejected `links` as unknown and the health judge dropped links.yaml."""

    traffic = REPOSITORY_ROOT / "controller" / "sdk" / "traffic"
    sources = "\n".join(p.read_text(encoding="utf-8") for p in traffic.glob("*.go") if not p.name.endswith("_test.go"))
    fake_host.traffic_sdk["sdo-detector-validator:mi2"] = sources.replace('json:"links', 'json:"old_links').replace(
        'yaml:"links', 'yaml:"old_links'
    )

    report = _run(fake_host, sregym_dir, _sdo())

    check = _check(report, "validator-sdk")
    assert check.status == "fail"
    assert "links" in check.detail


def test_a_controller_and_validator_on_different_tags_fail(fake_host: FakeHost, sregym_dir: Path) -> None:
    report = _run(fake_host, sregym_dir, _sdo(validator_image="sdo-detector-validator:v0.1.0"))

    assert _check(report, "image-tags").status == "fail"


def test_a_missing_image_is_reported_once_by_the_image_check_and_not_probed(
    fake_host: FakeHost, sregym_dir: Path
) -> None:
    fake_host.missing_images = {"sdo-controller:mi2"}

    report = _run(fake_host, sregym_dir, _sdo())

    assert _check(report, "sdo-images").status == "fail"
    assert all(check.name != "controller-flags" for check in report.checks)


def test_an_overloaded_host_fails_unless_it_is_below_the_limit(fake_host: FakeHost, sregym_dir: Path) -> None:
    fake_host.load = (35.0, 30.0, 20.0)

    assert _check(_run(fake_host, sregym_dir, _sdo()), "host-load").status == "fail"
    assert _check(_run(fake_host, sregym_dir, _sdo(), max_load=40.0), "host-load").status == "pass"


def test_missing_codex_credentials_fail(fake_host: FakeHost, sregym_dir: Path) -> None:
    (fake_host.home / ".codex" / "auth.json").unlink()

    assert _check(_run(fake_host, sregym_dir, _sdo()), "codex-auth").status == "fail"


def test_risky_option_combinations_are_flagged_without_failing(fake_host: FakeHost, sregym_dir: Path) -> None:
    report = _run(fake_host, sregym_dir, _sdo(closeout_state_gate=True, max_follow_ups=0))

    assert _check(report, "launch-lint").status == "unknown"
    assert report.ok


def test_stages_with_the_same_images_and_features_are_checked_once(fake_host: FakeHost, sregym_dir: Path) -> None:
    config = _sdo()

    report = _run(fake_host, sregym_dir, config, dataclasses.replace(config))

    assert _check(report, "controller-flags").detail.count("controller flags") == 1


def test_the_image_only_preflight_checks_the_flag_contract_and_the_sdk(fake_host: FakeHost) -> None:
    fake_host.controller_help["ci/controller:1"] = "usage: nothing"

    report = check_images(
        {"controller_image": "ci/controller:1", "validator_image": "ci/validator:1"},
        project_root=REPOSITORY_ROOT,
        sregym_dir=REPOSITORY_ROOT / "third_party" / "sregym",
        host=fake_host,
    )

    assert _check(report, "controller-flags").status == "fail"
    assert _check(report, "validator-sdk").status == "pass"
    assert CONTROLLER_HELP_COMMAND[0] == "python3"
