"""The launch checks that catch integration mistakes in seconds (flag contract, validator SDK, host, seed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from benchmarks.sregym.runner.launch_contract import (
    ALL_FEATURES,
    LaunchFeatures,
    check_cluster_available,
    check_codex_auth,
    check_controller_flags,
    check_host_load,
    check_image_tags,
    check_launch_lint,
    check_seed,
    check_validator_sdk,
    inspect_seed,
    workload_tags,
)
from sdo.controller_install import controller_launch_flags

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
SDK_ROOT = REPOSITORY_ROOT / "controller" / "sdk"


@dataclass
class FakeRunner:
    outputs: dict[str, str | None] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    def image_output(self, ref: str, argv: list[str]) -> str | None:
        return self.outputs.get(ref)


def _flags(**features: object) -> frozenset[str]:
    """The flags the benchmark launchers pass: they always keep the controller in its own namespace."""

    return controller_launch_flags(controller_namespace="preflight-control", **features)


def _help_with(flags: frozenset[str]) -> str:
    return "usage: sdo-detector-check controller\n" + "\n".join(f"  {flag} VALUE" for flag in sorted(flags))


def test_a_controller_image_that_lacks_a_launcher_flag_fails() -> None:
    """Regression: every controller pod exited on --closeout-state-gate before any incident ran."""

    flags = _flags(closeout_state_gate=True, max_follow_ups=1) - {"--closeout-state-gate"}
    runner = FakeRunner({"controller:old": _help_with(flags)})

    finding = check_controller_flags(
        runner, "controller:old", LaunchFeatures(closeout_state_gate=True, max_follow_ups=1)
    )

    assert finding.status == "fail"
    assert "--closeout-state-gate" in finding.detail
    assert "build_sdo_images" in finding.remedy


def test_a_missing_flag_is_ignored_when_the_run_does_not_use_its_feature() -> None:
    flags = _flags() - {"--closeout-state-gate"}

    finding = check_controller_flags(
        FakeRunner({"controller:old": _help_with(flags)}), "controller:old", LaunchFeatures()
    )

    assert finding.status == "pass"


def test_a_controller_image_with_every_flag_passes() -> None:
    runner = FakeRunner(
        {
            "controller:new": _help_with(
                _flags(**{k: v for k, v in ALL_FEATURES.install_features().items() if k != "controller_namespace"})
            )
        }
    )

    assert check_controller_flags(runner, "controller:new", ALL_FEATURES).status == "pass"


def test_a_flag_name_inside_a_longer_flag_does_not_count() -> None:
    flags = _flags(max_follow_ups=1) - {"--follow-up-cooldown"}
    text = _help_with(flags) + "\n  --follow-up-cooldown-extra VALUE"

    finding = check_controller_flags(
        FakeRunner({"controller:x": text}), "controller:x", LaunchFeatures(max_follow_ups=1)
    )

    assert finding.status == "fail"
    assert "--follow-up-cooldown" in finding.detail


def test_an_image_that_cannot_run_fails_with_a_remedy() -> None:
    finding = check_controller_flags(FakeRunner({}), "controller:none", LaunchFeatures())

    assert finding.status == "fail"
    assert finding.remedy


def test_workload_tags_come_from_json_and_yaml_tags() -> None:
    assert workload_tags('Links []Link `json:"links,omitempty" yaml:"links"`\nName string `yaml:"name"`') == {
        "links",
        "name",
    }


def test_the_checkout_defines_a_links_workload_field() -> None:
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (SDK_ROOT / "traffic").glob("*.go"))

    assert "links" in workload_tags(sources)


def test_a_validator_image_on_an_older_sdk_fails_naming_the_missing_field() -> None:
    """Regression: the stale validator rejected `links` as unknown and the health judge dropped the workload."""

    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SDK_ROOT / "traffic").glob("*.go")
        if not path.name.endswith("_test.go")
    )
    stale = sources.replace('json:"links', 'json:"renamed_links').replace('yaml:"links', 'yaml:"renamed_links')
    assert "links" not in workload_tags(stale)

    finding = check_validator_sdk(FakeRunner({"validator:old": stale}), "validator:old", SDK_ROOT)

    assert finding.status == "fail"
    assert "links" in finding.detail
    assert "rebuild" in finding.remedy


def test_a_validator_image_built_from_this_checkout_passes() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SDK_ROOT / "traffic").glob("*.go")
        if not path.name.endswith("_test.go")
    )

    assert check_validator_sdk(FakeRunner({"validator:new": sources}), "validator:new", SDK_ROOT).status == "pass"


def test_a_validator_image_that_cannot_show_its_sdk_fails(tmp_path: Path) -> None:
    assert check_validator_sdk(FakeRunner({}), "validator:none", SDK_ROOT).status == "fail"
    assert check_validator_sdk(FakeRunner({}), "validator:none", tmp_path).status == "unknown"


def test_image_tags_must_match_between_controller_and_validator() -> None:
    assert (
        check_image_tags(
            controller_image="sdo-controller:mi2",
            responder_image="sdo-sregym-responder:mi2",
            validator_image="sdo-detector-validator:mi2",
        ).status
        == "pass"
    )
    mismatch = check_image_tags(
        controller_image="sdo-controller:mi2",
        responder_image="sdo-sregym-responder:mi2",
        validator_image="sdo-detector-validator:v0.1.0",
    )
    assert mismatch.status == "fail"
    assert "same SDO_IMAGE_TAG" in mismatch.remedy


def test_a_responder_on_another_tag_is_flagged_not_failed() -> None:
    finding = check_image_tags(
        controller_image="sdo-controller:mi2",
        responder_image="sdo-sregym-responder:mi1",
        validator_image="sdo-detector-validator:mi2",
    )

    assert finding.status == "unknown"


@dataclass
class FakeLoad:
    values: list[float]

    def load_average(self) -> tuple[float, float, float] | None:
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return (value, value, value)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_load_below_the_limit_passes_without_waiting() -> None:
    clock = FakeClock()

    finding = check_host_load(FakeLoad([12.0]), max_load=20, sleep=clock.sleep, monotonic=clock.monotonic)

    assert finding.status == "pass"
    assert clock.slept == []


def test_load_above_the_limit_fails_when_not_asked_to_wait() -> None:
    clock = FakeClock()

    finding = check_host_load(FakeLoad([35.0]), max_load=20, sleep=clock.sleep, monotonic=clock.monotonic)

    assert finding.status == "fail"
    assert "SDO_PREFLIGHT_WAIT_LOAD_SECONDS" in finding.remedy


def test_load_that_falls_within_the_wait_passes() -> None:
    clock = FakeClock()

    finding = check_host_load(
        FakeLoad([35.0, 30.0, 18.0]), max_load=20, wait_seconds=120, sleep=clock.sleep, monotonic=clock.monotonic
    )

    assert finding.status == "pass"
    assert len(clock.slept) == 2


def test_load_that_never_falls_fails_after_the_wait() -> None:
    clock = FakeClock()

    finding = check_host_load(
        FakeLoad([40.0]), max_load=20, wait_seconds=45, sleep=clock.sleep, monotonic=clock.monotonic
    )

    assert finding.status == "fail"
    assert clock.now == pytest.approx(45.0)


def test_codex_auth_must_exist(tmp_path: Path) -> None:
    assert check_codex_auth(tmp_path).status == "fail"
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    assert check_codex_auth(tmp_path).status == "pass"


def test_lint_flags_inject_before_resume_as_blinding_the_link_probe() -> None:
    finding = check_launch_lint(LaunchFeatures(inject_before_resume=True))

    assert finding.status == "unknown"
    assert "link" in finding.detail


def test_lint_flags_the_closeout_gate_without_follow_ups() -> None:
    assert check_launch_lint(LaunchFeatures(closeout_state_gate=True)).status == "unknown"
    assert check_launch_lint(LaunchFeatures(closeout_state_gate=True, max_follow_ups=2)).status == "pass"
    assert check_launch_lint(LaunchFeatures()).status == "pass"


def test_features_read_from_an_sdo_agent_config() -> None:
    features = LaunchFeatures.from_agent_config(
        {"closeout_state_gate": True, "max_follow_ups": 3, "late_findings": "pull", "healthy_baseline": True}
    )

    assert features == ALL_FEATURES.__class__(
        closeout_state_gate=True, max_follow_ups=3, late_findings="pull", healthy_baseline=True
    )


def _git_repo(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    return path


def test_a_cold_seed_must_not_carry_sdo_memory(tmp_path: Path) -> None:
    seed = _git_repo(tmp_path / "seed")

    assert check_seed(seed, expect="cold").status == "pass"
    (seed / ".sdo").mkdir()
    assert check_seed(seed, expect="cold").status == "fail"


def test_an_attested_seed_needs_lifecycle_provenance_and_links(tmp_path: Path) -> None:
    seed = _git_repo(tmp_path / "seed")
    assert check_seed(seed, expect="attested").status == "fail"

    (seed / ".sdo").mkdir()
    (seed / ".sdo" / "lifecycle-provenance.yaml").write_text("{}", encoding="utf-8")
    assert check_seed(seed, expect="attested").status == "unknown"

    links = seed / ".sdo" / "diagnostics" / "traffic" / "workloads"
    links.mkdir(parents=True)
    (links / "links.yaml").write_text("links: []", encoding="utf-8")
    assert check_seed(seed, expect="attested").status == "pass"


def test_a_seed_that_is_not_a_git_repository_fails(tmp_path: Path) -> None:
    assert check_seed(tmp_path, expect="cold").status == "fail"


@dataclass
class FakeClusters:
    clusters: list[str] | None
    locks: dict[str, str] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    def kind_clusters(self) -> list[str] | None:
        return self.clusters

    def cluster_lock_owner(self, cluster: str) -> str | None:
        return self.locks.get(cluster)


def test_a_cluster_in_use_by_another_experiment_fails() -> None:
    probe = FakeClusters(["mi-w60"], {"mi-w60": "pid 123"})

    assert check_cluster_available(probe, "mi-w60").status == "fail"
    assert check_cluster_available(FakeClusters(["mi-w60"]), "mi-w60").status == "pass"
    assert check_cluster_available(FakeClusters([]), "mi-w60").status == "pass"
    assert check_cluster_available(FakeClusters(None), "mi-w60").status == "unknown"


def test_inspecting_a_seed_accepts_cold_and_attested_and_rejects_half_built(tmp_path: Path) -> None:
    seed = _git_repo(tmp_path / "seed")
    assert inspect_seed(seed).status == "pass"
    assert "cold" in inspect_seed(seed).detail

    (seed / ".sdo").mkdir()
    assert inspect_seed(seed).status == "fail"  # .sdo without lifecycle provenance: a half-built lifecycle

    (seed / ".sdo" / "lifecycle-provenance.yaml").write_text("{}", encoding="utf-8")
    assert inspect_seed(seed).status == "unknown"  # attested, but no links workload

    assert inspect_seed(tmp_path).status == "fail"
