"""Fastloop launch preflight: integration checks run before ``up`` and ``run`` spend cluster or LLM time.

The checks are shared with the experiment runner's launch preflight
(:mod:`benchmarks.sregym.runner.launch_contract`): the controller image's launcher defines every flag this run makes
the installer pass, the validator image's SDK accepts this checkout's workload fields, the images share one tag, the
seed has the right shape, the lane is free, the host is not overloaded and Codex is logged in. ``--no-preflight`` opts
out, and ``SDO_PREFLIGHT=warn`` prints failures and continues.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from benchmarks.sregym.fastloop.environment import Images
from benchmarks.sregym.runner import launch_contract
from benchmarks.sregym.runner.preflight import (
    PREFLIGHT_MODE_ENV,
    PreflightCheck,
    PreflightMode,
    PreflightReport,
    PreflightSettings,
    SystemHost,
    check_from_findings,
    preflight_mode,
    render_report,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Mapping

__all__ = ["Images", "SystemHost", "enforce", "launch_settings", "run_checks", "run_launch_preflight", "up_checks"]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SDK_ROOT = PROJECT_ROOT / "controller" / "sdk"


class LaunchHost(Protocol):
    """What the fastloop checks read from the host; the runner's :class:`SystemHost` provides all of it."""

    home: Path

    def image(self, ref: str) -> object | None: ...
    def image_output(self, ref: str, argv: list[str]) -> str | None: ...
    def load_average(self) -> tuple[float, float, float] | None: ...
    def kind_clusters(self) -> list[str] | None: ...
    def cluster_lock_owner(self, cluster: str) -> str | None: ...


def launch_settings(env: Mapping[str, str] | None = None) -> PreflightSettings:
    return PreflightSettings.from_env(os.environ if env is None else env)


def _image_checks(images: Images, host: LaunchHost, features: launch_contract.LaunchFeatures) -> list[PreflightCheck]:
    refs = {"controller": images.controller, "responder": images.responder, "validator": images.validator}
    missing = sorted(ref for ref in refs.values() if host.image(ref) is None)
    checks = [
        check_from_findings(
            "image-tags",
            [
                launch_contract.check_image_tags(
                    controller_image=images.controller,
                    responder_image=images.responder,
                    validator_image=images.validator,
                )
            ],
        )
    ]
    if missing:
        checks.append(
            PreflightCheck(
                "images",
                "fail",
                f"images not present: {', '.join(missing)}",
                "build them with `SDO_IMAGE_TAG=<tag> scripts/build_sdo_images.sh`",
            )
        )
        return checks
    checks.append(PreflightCheck("images", "pass", "controller, responder and validator images are present"))
    checks.append(
        check_from_findings(
            "controller-flags", [launch_contract.check_controller_flags(host, images.controller, features)]
        )
    )
    checks.append(
        check_from_findings("validator-sdk", [launch_contract.check_validator_sdk(host, images.validator, SDK_ROOT)])
    )
    return checks


def _host_checks(host: LaunchHost, settings: PreflightSettings, env: Mapping[str, str]) -> list[PreflightCheck]:
    codex_home = Path(env.get("CODEX_HOME", "").strip() or host.home / ".codex")
    return [
        check_from_findings(
            "host-load",
            [
                launch_contract.check_host_load(
                    host, max_load=settings.max_load, wait_seconds=settings.load_wait_seconds
                )
            ],
        ),
        check_from_findings("codex-auth", [launch_contract.check_codex_auth(codex_home)]),
    ]


def up_checks(
    args: argparse.Namespace,
    host: LaunchHost,
    *,
    settings: PreflightSettings,
    env: Mapping[str, str] | None = None,
) -> list[PreflightCheck]:
    """Checks before ``up``: images, seed, lane and host. No run features are known yet, so defaults apply."""

    images = Images(controller=args.controller_image, responder=args.responder_image, validator=args.validator_image)
    checks = _image_checks(images, host, launch_contract.LaunchFeatures())
    if args.seed is not None:
        checks.append(check_from_findings("seed", [launch_contract.inspect_seed(args.seed.resolve())]))
    cluster = f"{args.cluster_prefix}{args.worker_id}"
    checks.append(check_from_findings("cluster", [launch_contract.check_cluster_available(host, cluster)]))
    checks.extend(_host_checks(host, settings, os.environ if env is None else env))
    return checks


def run_checks(
    args: argparse.Namespace,
    images: Images,
    host: LaunchHost,
    *,
    settings: PreflightSettings,
    env: Mapping[str, str] | None = None,
) -> list[PreflightCheck]:
    """Checks before ``run``: the images against the features this run enables, and risky option combinations."""

    checks: list[PreflightCheck] = []
    if args.agent == "sdo":
        features = launch_contract.LaunchFeatures(
            closeout_state_gate=args.closeout_state_gate,
            max_follow_ups=args.max_follow_ups,
            late_findings=args.late_findings,
            healthy_baseline=args.healthy_baseline,
            inject_before_resume=args.inject_before_resume,
        )
        checks.extend(_image_checks(images, host, features))
        checks.append(check_from_findings("launch-lint", [launch_contract.check_launch_lint(features)]))
    checks.extend(_host_checks(host, settings, os.environ if env is None else env))
    return checks


def enforce(checks: list[PreflightCheck], *, mode: PreflightMode) -> PreflightReport:
    """Print the report; abort on a failure unless *mode* is ``warn``."""

    report = PreflightReport(tuple(checks), {}, mode)
    print(render_report(report), flush=True)
    if not report.ok and mode == "enforce":
        lines = ["fastloop launch preflight failed; nothing was started."]
        for check in report.failures:
            lines.append(f"  [{check.name}] {check.detail}")
            if check.remedy:
                lines.append(f"      fix: {check.remedy}")
        lines.append(f"Pass --no-preflight, or set {PREFLIGHT_MODE_ENV}=warn, to launch anyway.")
        raise SystemExit("\n".join(lines))
    return report


def run_launch_preflight(
    args: argparse.Namespace, *, stage: Literal["up", "run"], images: Images | None = None
) -> PreflightReport | None:
    """The preflight for one fastloop command, or ``None`` when ``--no-preflight`` is given."""

    if args.no_preflight:
        return None
    host = SystemHost()
    settings = launch_settings()
    checks = (
        up_checks(args, host, settings=settings)
        if stage == "up"
        else run_checks(args, images or Images(), host, settings=settings)
    )
    return enforce(checks, mode=preflight_mode(os.environ))
