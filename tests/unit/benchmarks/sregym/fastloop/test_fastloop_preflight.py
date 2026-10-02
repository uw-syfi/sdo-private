"""Fastloop starts every `up` and `run` with the launch preflight, which `--no-preflight` opts out of."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from benchmarks.sregym.fastloop import cli, preflight
from benchmarks.sregym.runner.launch_contract import CONTROLLER_HELP_COMMAND, IMAGE_TRAFFIC_SDK
from benchmarks.sregym.runner.preflight import PreflightSettings
from sdo.controller_install import controller_launch_flags

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]


@dataclass
class FakeHost:
    """A healthy host; tests break one thing at a time."""

    home: Path
    missing_images: set[str] = field(default_factory=set)  # pyright: ignore[reportUnknownVariableType]
    controller_help: dict[str, str] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    traffic_sdk: dict[str, str] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    clusters: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    locks: dict[str, str] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    load: tuple[float, float, float] = (3.0, 3.0, 3.0)

    def image(self, ref: str) -> object | None:
        return None if ref in self.missing_images else object()

    def image_output(self, ref: str, argv: list[str]) -> str | None:
        if tuple(argv) == CONTROLLER_HELP_COMMAND:
            return self.controller_help.get(ref) or "\n".join(
                sorted(
                    controller_launch_flags(
                        controller_namespace="x",
                        closeout_state_gate=True,
                        max_follow_ups=1,
                        late_findings="pull",
                        healthy_baseline=True,
                    )
                )
            )
        if argv[:2] == ["sh", "-c"] and IMAGE_TRAFFIC_SDK in argv[2]:
            if ref in self.traffic_sdk:
                return self.traffic_sdk[ref]
            traffic = REPOSITORY_ROOT / "controller" / "sdk" / "traffic"
            return "\n".join(
                p.read_text(encoding="utf-8") for p in traffic.glob("*.go") if not p.name.endswith("_test.go")
            )
        return None

    def load_average(self) -> tuple[float, float, float] | None:
        return self.load

    def kind_clusters(self) -> list[str] | None:
        return self.clusters

    def cluster_lock_owner(self, cluster: str) -> str | None:
        return self.locks.get(cluster)


@pytest.fixture
def host(tmp_path: Path) -> FakeHost:
    (tmp_path / "home" / ".codex").mkdir(parents=True)
    (tmp_path / "home" / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    return FakeHost(home=tmp_path / "home")


@pytest.fixture
def seed(tmp_path: Path) -> Path:
    (tmp_path / "seed" / ".git").mkdir(parents=True)
    return tmp_path / "seed"


def _up_args(tmp_path: Path, seed: Path, *extra: str):
    return cli.build_parser().parse_args(
        [
            "up",
            "--run-dir",
            str(tmp_path / "run"),
            "--seed",
            str(seed),
            "--controller-image",
            "sdo-controller:mi2",
            "--responder-image",
            "sdo-sregym-responder:mi2",
            "--validator-image",
            "sdo-detector-validator:mi2",
            "--cluster-prefix",
            "mi-w",
            "--worker-id",
            "61",
            *extra,
        ]
    )


def _run_args(tmp_path: Path, *extra: str):
    return cli.build_parser().parse_args(["run", "--run-dir", str(tmp_path / "run"), "--agent", "sdo", *extra])


IMAGES = preflight.Images(
    controller="sdo-controller:mi2", responder="sdo-sregym-responder:mi2", validator="sdo-detector-validator:mi2"
)


def _names(checks: list) -> set[str]:
    return {check.name for check in checks}


def test_a_healthy_up_passes_every_check(tmp_path: Path, host: FakeHost, seed: Path) -> None:
    checks = preflight.up_checks(_up_args(tmp_path, seed), host, settings=PreflightSettings())

    assert all(check.status != "fail" for check in checks), checks
    assert {
        "image-tags",
        "images",
        "controller-flags",
        "validator-sdk",
        "seed",
        "cluster",
        "host-load",
        "codex-auth",
    } <= _names(checks)


def test_up_fails_on_a_stale_validator_image(tmp_path: Path, host: FakeHost, seed: Path) -> None:
    """Regression: the lifecycle ran on a validator whose SDK lacked `links`, and the judge dropped the workload."""

    host.traffic_sdk["sdo-detector-validator:mi2"] = 'Name string `json:"name"`'

    checks = preflight.up_checks(_up_args(tmp_path, seed), host, settings=PreflightSettings())

    assert next(c for c in checks if c.name == "validator-sdk").status == "fail"


def test_up_fails_on_a_missing_image_a_busy_cluster_and_a_half_built_seed(
    tmp_path: Path, host: FakeHost, seed: Path
) -> None:
    host.missing_images = {"sdo-controller:mi2"}
    host.clusters = ["mi-w61"]
    host.locks = {"mi-w61": "pid 42"}
    (seed / ".sdo").mkdir()

    checks = preflight.up_checks(_up_args(tmp_path, seed), host, settings=PreflightSettings())

    failed = {check.name for check in checks if check.status == "fail"}
    assert {"images", "cluster", "seed"} <= failed


def test_run_checks_the_images_against_the_features_the_run_enables(tmp_path: Path, host: FakeHost) -> None:
    """Regression: the controller launcher lacked --closeout-state-gate and every controller pod exited."""

    help_without_gate = "\n".join(
        sorted(controller_launch_flags(controller_namespace="x", max_follow_ups=1) - {"--closeout-state-gate"})
    )
    host.controller_help["sdo-controller:mi2"] = help_without_gate

    with_gate = preflight.run_checks(
        _run_args(tmp_path, "--closeout-state-gate", "--max-follow-ups", "1"),
        IMAGES,
        host,
        settings=PreflightSettings(),
    )
    without_gate = preflight.run_checks(
        _run_args(tmp_path, "--max-follow-ups", "1"), IMAGES, host, settings=PreflightSettings()
    )

    assert next(c for c in with_gate if c.name == "controller-flags").status == "fail"
    assert next(c for c in without_gate if c.name == "controller-flags").status == "pass"


def test_run_flags_inject_before_resume_as_blinding_the_link_probe(tmp_path: Path, host: FakeHost) -> None:
    checks = preflight.run_checks(
        _run_args(tmp_path, "--inject-before-resume"), IMAGES, host, settings=PreflightSettings()
    )

    lint = next(c for c in checks if c.name == "launch-lint")
    assert lint.status == "unknown"
    assert "link" in lint.detail


def test_a_codex_baseline_run_skips_the_sdo_image_checks(tmp_path: Path, host: FakeHost) -> None:
    args = cli.build_parser().parse_args(["run", "--run-dir", str(tmp_path), "--agent", "codex"])

    checks = preflight.run_checks(args, IMAGES, host, settings=PreflightSettings())

    assert _names(checks) == {"host-load", "codex-auth"}


def test_the_options_default_to_running_the_preflight(tmp_path: Path, seed: Path) -> None:
    assert _up_args(tmp_path, seed).no_preflight is False
    assert _up_args(tmp_path, seed, "--no-preflight").no_preflight is True
    assert _run_args(tmp_path, "--no-preflight").no_preflight is True


def test_a_failed_preflight_aborts_with_every_failure_and_its_fix(tmp_path: Path, host: FakeHost, seed: Path) -> None:
    host.missing_images = {"sdo-controller:mi2"}
    checks = preflight.up_checks(_up_args(tmp_path, seed), host, settings=PreflightSettings())

    with pytest.raises(SystemExit) as raised:
        preflight.enforce(checks, mode="enforce")

    assert "[images]" in str(raised.value)
    assert "--no-preflight" in str(raised.value)


def test_warn_mode_prints_failures_and_continues(
    tmp_path: Path, host: FakeHost, seed: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host.missing_images = {"sdo-controller:mi2"}
    checks = preflight.up_checks(_up_args(tmp_path, seed), host, settings=PreflightSettings())

    preflight.enforce(checks, mode="warn")

    assert "FAIL" in capsys.readouterr().out


def test_up_runs_the_preflight_before_touching_the_run_dir(
    tmp_path: Path, host: FakeHost, seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.missing_images = {"sdo-controller:mi2"}
    monkeypatch.setattr(preflight, "SystemHost", lambda: host)
    args = _up_args(tmp_path, seed)

    with pytest.raises(SystemExit, match="images"):
        cli._up(args)

    assert not (tmp_path / "run").exists()


def test_no_preflight_skips_the_checks(
    tmp_path: Path, host: FakeHost, seed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[Any] = []
    monkeypatch.setattr(preflight, "SystemHost", lambda: called.append("probed") or host)

    cli.run_launch_preflight(_up_args(tmp_path, seed, "--no-preflight"), stage="up")

    assert called == []
