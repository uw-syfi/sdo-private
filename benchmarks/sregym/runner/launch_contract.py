"""Launch checks that catch integration mistakes in seconds, before any cluster or LLM time is spent.

Each of these once cost a run an hour or more:

- a launcher passed a flag the controller image's launcher did not define, so every controller pod exited with
  ``unrecognized arguments`` (``controller-flags``);
- host-side lifecycle validation ran in a validator image whose SDK predated a workload field, so the health judge's
  ``links.yaml`` was rejected and dropped (``validator-sdk``, ``image-tags``);
- an experiment combined options that silently disable a detector or gate (``launch-lint``);
- a seed repository had the wrong shape for the run (``seed``), or a lane was busy (``cluster``), or the host was
  already overloaded (``host-load``), or Codex was not logged in (``codex-auth``).

The functions here return plain findings and read the host through two small methods, so the launch preflight
(:mod:`benchmarks.sregym.runner.preflight`) and the fastloop CLI share them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sdo.controller_install import controller_launch_flags, require_matching_image_tags

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

FindingStatus = Literal["pass", "fail", "unknown"]

CONTROLLER_HELP_COMMAND = ("python3", "-m", "controller.builder.check_cli", "controller", "--help")
#: Where the controller and validator images carry the SDK the host-side lifecycle validates against.
IMAGE_TRAFFIC_SDK = "/opt/sdo/controller/sdk/traffic"
_WORKLOAD_TAG = re.compile(r'(?:json|yaml):"([A-Za-z0-9_]+)')


@dataclass(frozen=True)
class Finding:
    status: FindingStatus
    detail: str
    remedy: str = ""


class ImageRunner(Protocol):
    """Runs one command inside an image and returns its stdout, ``None`` when it cannot run or fails."""

    def image_output(self, ref: str, argv: list[str]) -> str | None: ...


class LoadProbe(Protocol):
    def load_average(self) -> tuple[float, float, float] | None: ...


class ClusterProbe(Protocol):
    def kind_clusters(self) -> list[str] | None: ...
    def cluster_lock_owner(self, cluster: str) -> str | None: ...


@dataclass(frozen=True)
class LaunchFeatures:
    """The SDO options that decide which flags a controller launcher passes and which detectors can fire."""

    closeout_state_gate: bool = False
    max_follow_ups: int = 0
    late_findings: str = "off"
    healthy_baseline: bool = False
    inject_before_resume: bool = False

    @classmethod
    def from_agent_config(cls, section: Mapping[str, Any]) -> LaunchFeatures:
        return cls(
            closeout_state_gate=bool(section.get("closeout_state_gate", False)),
            max_follow_ups=int(section.get("max_follow_ups", 0) or 0),
            late_findings=str(section.get("late_findings", "off")),
            healthy_baseline=bool(section.get("healthy_baseline", False)),
            inject_before_resume=bool(section.get("inject_before_resume", False)),
        )

    def install_features(self) -> dict[str, Any]:
        """The :class:`sdo.controller_install.ControllerInstallConfig` fields behind the controller flags."""

        return {
            "closeout_state_gate": self.closeout_state_gate,
            "max_follow_ups": self.max_follow_ups,
            "late_findings": self.late_findings,
            "healthy_baseline": self.healthy_baseline,
            # Benchmark runs always keep the controller in its own namespace.
            "controller_namespace": "preflight-control",
        }


ALL_FEATURES = LaunchFeatures(closeout_state_gate=True, max_follow_ups=1, late_findings="pull", healthy_baseline=True)


def check_controller_flags(runner: ImageRunner, controller_image: str, features: LaunchFeatures) -> Finding:
    """The controller image's launcher defines every flag the launcher will pass for *features*."""

    help_text = runner.image_output(controller_image, list(CONTROLLER_HELP_COMMAND))
    if not help_text:
        return Finding(
            "fail",
            f"image {controller_image} could not print the controller launcher's --help",
            "check that the image exists and was built from this checkout with scripts/build_sdo_images.sh",
        )
    required = controller_launch_flags(**features.install_features())
    missing = sorted(flag for flag in required if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text))
    if missing:
        return Finding(
            "fail",
            f"image {controller_image} does not define {', '.join(missing)}; its controller pods would exit with "
            "'unrecognized arguments' before any incident ran",
            "rebuild the images from this checkout with scripts/build_sdo_images.sh and pass the new tag everywhere",
        )
    return Finding("pass", f"{controller_image} defines all {len(required)} controller flags the launcher passes")


def workload_tags(source: str) -> set[str]:
    """The JSON and YAML field names declared by Go SDK sources."""

    return set(_WORKLOAD_TAG.findall(source))


def check_validator_sdk(runner: ImageRunner, validator_image: str, sdk_root: Path) -> Finding:
    """The validator image's traffic SDK accepts every workload field this checkout defines.

    A validator on an older SDK rejects a newer field as unknown, and the health judge then drops the workload.
    """

    traffic = sdk_root / "traffic"
    sources = sorted(path for path in traffic.glob("*.go") if not path.name.endswith("_test.go"))
    if not sources:
        return Finding("unknown", f"no traffic SDK sources under {traffic}; cannot compare with {validator_image}")
    expected = workload_tags("\n".join(path.read_text(encoding="utf-8") for path in sources))
    image_source = runner.image_output(validator_image, ["sh", "-c", f"cat {IMAGE_TRAFFIC_SDK}/*.go"])
    if not image_source:
        return Finding(
            "fail",
            f"image {validator_image} could not show its traffic SDK at {IMAGE_TRAFFIC_SDK}",
            "check that the validator image exists and was built from this checkout",
        )
    missing = sorted(expected - workload_tags(image_source))
    if missing:
        return Finding(
            "fail",
            f"validator image {validator_image} lacks SDK fields this checkout defines: {', '.join(missing)}; the "
            "health judge's workloads using them would be rejected as unknown and dropped",
            "rebuild the images from this checkout and pass the same tag for the controller and the validator",
        )
    return Finding("pass", f"{validator_image} accepts all {len(expected)} traffic SDK fields of this checkout")


def check_image_tags(*, controller_image: str, responder_image: str, validator_image: str) -> Finding:
    """The controller and validator share one SDO build tag; a responder on another tag is suspicious."""

    try:
        require_matching_image_tags(controller_image=controller_image, validator_image=validator_image)
    except ValueError as exc:
        return Finding("fail", str(exc), "pass the same SDO_IMAGE_TAG for controller, responder and validator")
    tags = {ref.rpartition(":")[2] for ref in (controller_image, responder_image, validator_image) if ":" in ref}
    if len(tags) > 1:
        return Finding(
            "unknown",
            f"controller, responder and validator are on different tags ({', '.join(sorted(tags))})",
            "build and pass all three from one SDO_IMAGE_TAG unless the mix is intended",
        )
    return Finding("pass", "controller, responder and validator images are on one tag")


def check_host_load(
    host: LoadProbe,
    *,
    max_load: float,
    wait_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    poll_seconds: float = 15.0,
) -> Finding:
    """The 1-minute load average is at most *max_load*, waiting up to *wait_seconds* for it to fall."""

    deadline = monotonic() + wait_seconds
    while True:
        load = host.load_average()
        if load is None:
            return Finding("unknown", "the host load average is unavailable")
        if load[0] <= max_load:
            return Finding("pass", f"host load {load[0]:.1f} is within {max_load:.0f}")
        if monotonic() >= deadline:
            return Finding(
                "fail",
                f"host load is {load[0]:.1f} (limit {max_load:.0f}); health timeouts and not-Ready pods follow at this "
                "load and would be misread as product failures",
                "wait for other jobs to finish, or set SDO_PREFLIGHT_WAIT_LOAD_SECONDS to wait for the load to fall",
            )
        sleep(min(poll_seconds, max(deadline - monotonic(), 0.0)))


def check_codex_auth(codex_home: Path) -> Finding:
    """Codex is logged in, which the SDO agents, the baseline and the judge all need (quota is not checked here)."""

    if (codex_home / "auth.json").is_file():
        return Finding("pass", f"Codex credentials present under {codex_home}")
    return Finding("fail", f"no Codex credentials at {codex_home / 'auth.json'}", "run `codex login`")


def lint_launch(features: LaunchFeatures) -> list[str]:
    """Option combinations that run but quietly measure something else."""

    notes: list[str] = []
    if features.closeout_state_gate and features.max_follow_ups == 0:
        notes.append(
            "the close-out state gate sends an incident back through the follow-up chain, and max_follow_ups is 0: "
            "it can only close incidents as 'exhausted'"
        )
    if features.inject_before_resume:
        notes.append(
            "inject-before-resume lands faults while the controller is paused at its start, before the link prober "
            "has a baseline: link-reachability findings (an isolating NetworkPolicy, for one) cannot fire; use the "
            "deferred injection gate (conductor path) or inject after the first clear evaluation"
        )
    return notes


def check_launch_lint(features: LaunchFeatures) -> Finding:
    notes = lint_launch(features)
    if notes:
        return Finding("unknown", "; ".join(notes))
    return Finding("pass", "no risky option combinations")


def check_seed(seed: Path, *, expect: Literal["cold", "attested"]) -> Finding:
    """The seed repository has the shape the run assumes: a cold source-only repo, or one with a finished lifecycle."""

    if not (seed / ".git").exists():
        return Finding(
            "fail", f"seed {seed} is not a git repository", "pass a git repository with the application source"
        )
    has_sdo = (seed / ".sdo").is_dir()
    if expect == "cold":
        if has_sdo:
            return Finding(
                "fail",
                f"seed {seed} already has .sdo/, so the run would not exercise a cold lifecycle",
                "seed from a source-only repository (git archive of the application)",
            )
        return Finding("pass", f"seed {seed} is a cold source-only repository")
    provenance = seed / ".sdo" / "lifecycle-provenance.yaml"
    if not provenance.is_file():
        return Finding(
            "fail",
            f"seed {seed} has no .sdo/lifecycle-provenance.yaml, so it is not an attested lifecycle seed",
            "seed from the workspace of a run whose lifecycle committed, or run the cold lifecycle",
        )
    links = seed / ".sdo" / "diagnostics" / "traffic" / "workloads" / "links.yaml"
    if not links.is_file():
        return Finding(
            "unknown",
            f"attested seed {seed} has no traffic links workload; link-reachability detection is off in this run",
        )
    return Finding("pass", f"seed {seed} is an attested lifecycle seed with a links workload")


def check_cluster_available(probe: ClusterProbe, cluster: str) -> Finding:
    """The lane's cluster is not in use by another experiment (an existing one is reused)."""

    clusters = probe.kind_clusters()
    if clusters is None:
        return Finding("unknown", "kind clusters could not be listed")
    if cluster not in clusters:
        return Finding("pass", f"cluster {cluster} does not exist yet and will be created")
    owner = probe.cluster_lock_owner(cluster)
    if owner:
        return Finding(
            "fail", f"cluster {cluster} is in use by {owner}", "choose another --cluster-prefix or --worker-id"
        )
    return Finding("pass", f"cluster {cluster} exists and is free; it will be reused")


def inspect_seed(seed: Path) -> Finding:
    """A seed repository is a git repository that is either source-only (cold) or a finished lifecycle (attested)."""

    if not (seed / ".git").exists():
        return Finding(
            "fail", f"seed {seed} is not a git repository", "pass a git repository with the application source"
        )
    if not (seed / ".sdo").is_dir():
        return Finding("pass", f"seed {seed} is a cold source-only repository (the lifecycle will run)")
    return check_seed(seed, expect="attested")
