from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from controller.builder.check_cli import _build_parser, _controller_config, _controller_flag_args
from controller.builder.manifest import duration_nanoseconds

# Seam 3 parity ratchet: the legacy controller launch flags (_controller_flag_args)
# and the typed ControllerConfig (_controller_config) must carry the same values,
# so the cutover to `--config` is behavior-preserving. Combined with the Go-side
# drift guard (controllerConfigToArgs <-> run.go) and the Go config round-trip,
# this closes the loop end to end. If either builder drifts, this fails here.

_COMMAND = [
    "controller",
    "--app",
    "/srv/app",
    "--namespace",
    "demo",
    "--control-namespace",
    "demo-sdo",
    "--application",
    "myapp",
    "--source-commit",
    "abc123",
    "--deployed-commit",
    "def456",
    "--responder-image",
    "img:responder",
    "--prober-image",
    "img:prober",
    "--repository-pvc",
    "pvc",
    "--repository-mount-path",
    "/workspace",
    "--repository-pvc-subpath",
    "sub",
    "--credentials-secret",
    "sec",
    "--worktree-root",
    "/wt",
    "--responder-command",
    "/usr/bin/python3",
    "--responder-arg=-m",
    "--responder-arg",
    "sdo.agent_runtime.responder.job",
    "--responder-env",
    "A=1",
    "--responder-env",
    "B=2",
    "--broker-command",
    "/usr/bin/python3",
    "--broker-arg=-m",
    "--broker-arg",
    "sdo.agent_runtime.responder.broker_cli",
    "--response-timeout",
    "30m",
    "--verification-timeout",
    "2m",
    "--max-follow-ups",
    "3",
    "--follow-up-cooldown",
    "45s",
    "--repair-policy",
    "recorded-actions",
    "--duration",
    "10m",
    "--lease-name",
    "lease",
    "--exit-after-closure",
]

_REPEATED_FLAGS = {"--dispatcher-arg", "--responder-env", "--broker-arg"}
_BOOL_FLAGS = {"--exit-after-closure", "--restart-after-closure"}

_EXPECTED_FLAG_NAMES = {
    "--namespace",
    "--app-root",
    "--dispatcher-mode",
    "--dispatcher",
    "--dispatcher-arg",
    "--responder-env",
    "--responder-image",
    "--repository-pvc",
    "--repository-mount-path",
    "--responder-credentials-secret",
    "--broker",
    "--broker-arg",
    "--broker-worktree-root",
    "--response-timeout",
    "--verification-timeout",
    "--max-follow-ups",
    "--follow-up-cooldown",
    "--repair-policy",
    "--lease-name",
    "--control-namespace",
    "--application",
    "--source-commit",
    "--deployed-commit",
    "--repository-pvc-subpath",
    "--duration",
    "--exit-after-closure",
    "--prober-binary",
    "--prober-image",
}


def _parse(flags: list[str]) -> tuple[dict[str, str], dict[str, list[str]], set[str]]:
    pairs: dict[str, str] = {}
    repeated: dict[str, list[str]] = defaultdict(list)
    bools: set[str] = set()
    index = 0
    while index < len(flags):
        flag = flags[index]
        if flag in _BOOL_FLAGS:
            bools.add(flag)
            index += 1
        elif flag in _REPEATED_FLAGS:
            repeated[flag].append(flags[index + 1])
            index += 2
        else:
            pairs[flag] = flags[index + 1]
            index += 2
    return pairs, repeated, bools


def _build_both() -> tuple[list[str], object]:
    args = _build_parser().parse_args(_COMMAND)
    common = {
        "app_root": Path("/srv/app"),
        "worktree_root": Path("/wt"),
        "responder_args": list(args.responder_arg),
        "broker_args": list(args.broker_arg),
        "prober_binary": Path("/wt/.sdo-prober/deadbeef/sdo-prober"),
    }
    return _controller_flag_args(args, **common), _controller_config(args, **common)


def test_every_launch_flag_has_a_config_field() -> None:
    flags, _ = _build_both()
    pairs, repeated, bools = _parse(flags)
    assert set(pairs) | set(repeated) | bools == _EXPECTED_FLAG_NAMES


def test_scalar_flags_match_the_config() -> None:
    flags, config = _build_both()
    pairs, _, _ = _parse(flags)

    assert pairs["--namespace"] == config.namespace
    assert pairs["--app-root"] == config.app_root
    assert pairs["--dispatcher-mode"] == config.dispatcher_mode == "job"
    assert pairs["--dispatcher"] == config.dispatcher
    assert pairs["--responder-image"] == config.responder_image
    assert pairs["--repository-pvc"] == config.repository_pvc
    assert pairs["--repository-mount-path"] == config.repository_mount_path
    assert pairs["--responder-credentials-secret"] == config.responder_credentials_secret
    assert pairs["--broker"] == config.broker
    assert pairs["--broker-worktree-root"] == config.broker_worktree_root
    assert pairs["--repair-policy"] == config.repair_policy
    assert pairs["--lease-name"] == config.lease_name
    assert pairs["--control-namespace"] == config.control_namespace
    assert pairs["--application"] == config.application
    assert pairs["--source-commit"] == config.source_commit
    assert pairs["--deployed-commit"] == config.deployed_commit
    assert pairs["--repository-pvc-subpath"] == config.repository_pvc_subpath
    assert pairs["--prober-binary"] == config.prober_binary
    assert pairs["--prober-image"] == config.prober_image
    assert int(pairs["--max-follow-ups"]) == config.max_follow_ups


def test_duration_flags_match_the_config() -> None:
    flags, config = _build_both()
    pairs, _, _ = _parse(flags)

    assert duration_nanoseconds(pairs["--response-timeout"]) == config.response_timeout.ToNanoseconds()
    assert duration_nanoseconds(pairs["--verification-timeout"]) == config.verification_timeout.ToNanoseconds()
    assert duration_nanoseconds(pairs["--follow-up-cooldown"]) == config.follow_up_cooldown.ToNanoseconds()
    assert duration_nanoseconds(pairs["--duration"]) == config.duration.ToNanoseconds()


def test_repeated_and_bool_flags_match_the_config() -> None:
    flags, config = _build_both()
    _, repeated, bools = _parse(flags)

    assert repeated["--dispatcher-arg"] == list(config.dispatcher_args)
    assert repeated["--responder-env"] == list(config.responder_env)
    assert repeated["--broker-arg"] == list(config.broker_args)
    assert ("--exit-after-closure" in bools) == config.exit_after_closure
    assert ("--restart-after-closure" in bools) == config.restart_after_closure


def test_supervise_sets_restart_after_closure() -> None:
    command = [flag for flag in _COMMAND if flag != "--exit-after-closure"] + ["--supervise"]
    args = _build_parser().parse_args(command)
    common = {
        "app_root": Path("/srv/app"),
        "worktree_root": Path("/wt"),
        "responder_args": list(args.responder_arg),
        "broker_args": list(args.broker_arg),
        "prober_binary": None,
    }
    flags = _controller_flag_args(args, **common)
    config = _controller_config(args, **common)
    assert "--restart-after-closure" in flags
    assert config.restart_after_closure is True
    assert config.exit_after_closure is False
    # No prober this time: neither side emits the prober flags/fields.
    assert "--prober-binary" not in flags
    assert config.prober_binary == ""
