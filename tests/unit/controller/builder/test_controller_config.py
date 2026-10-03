from __future__ import annotations

import json
from pathlib import Path

from controller.builder.check_cli import _build_parser, _controller_config
from controller.builder.manifest import duration_nanoseconds

# Seam 3: _controller_config is the single source for the launcher -> controller
# boundary (serialized as protojson and handed over via --config). These tests
# pin that every launch input reaches the config and survives the canonical
# protojson encoding. The Go side (controller/runtime controller_config_test.go)
# owns the inverse: loading the protojson and reproducing the controller flags.

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

# Every field a fully-specified `controller` command populates. A new launch
# input must be wired into _controller_config (and show up here) or this fails,
# so the launcher can never silently drop a flag during the proto migration.
_EXPECTED_CONFIG_FIELDS = {
    "app_root",
    "application",
    "broker",
    "broker_args",
    "broker_worktree_root",
    "control_namespace",
    "deployed_commit",
    "dispatcher",
    "dispatcher_args",
    "dispatcher_mode",
    "duration",
    "exit_after_closure",
    "follow_up_cooldown",
    "lease_name",
    "max_follow_ups",
    "namespace",
    "prober_binary",
    "prober_image",
    "repair_policy",
    "repository_mount_path",
    "repository_pvc",
    "repository_pvc_subpath",
    "responder_credentials_secret",
    "responder_env",
    "responder_image",
    "response_timeout",
    "source_commit",
    "verification_timeout",
}


def _config_from(command: list[str], *, prober_binary: Path | None) -> object:
    args = _build_parser().parse_args(command)
    return _controller_config(
        args,
        app_root=Path("/srv/app"),
        worktree_root=Path("/wt"),
        responder_args=list(args.responder_arg),
        broker_args=list(args.broker_arg),
        prober_binary=prober_binary,
    )


def _as_json(config: object) -> dict:
    from sdo.contracts.proto import to_canonical_json

    return json.loads(to_canonical_json(config))


def test_controller_config_populates_every_launch_input() -> None:
    config = _config_from(_COMMAND, prober_binary=Path("/wt/.sdo-prober/deadbeef/sdo-prober"))

    assert config.namespace == "demo"
    assert config.app_root == "/srv/app"
    assert config.dispatcher_mode == "job"
    assert config.dispatcher == "/usr/bin/python3"
    assert list(config.dispatcher_args) == ["-m", "sdo.agent_runtime.responder.job"]
    assert list(config.responder_env) == ["A=1", "B=2"]
    assert config.responder_image == "img:responder"
    assert config.repository_pvc == "pvc"
    assert config.repository_mount_path == "/workspace"
    assert config.repository_pvc_subpath == "sub"
    assert config.responder_credentials_secret == "sec"
    assert config.broker == "/usr/bin/python3"
    assert list(config.broker_args) == ["-m", "sdo.agent_runtime.responder.broker_cli"]
    assert config.broker_worktree_root == "/wt"
    assert config.max_follow_ups == 3
    assert config.repair_policy == "recorded-actions"
    assert config.lease_name == "lease"
    assert config.control_namespace == "demo-sdo"
    assert config.application == "myapp"
    assert config.source_commit == "abc123"
    assert config.deployed_commit == "def456"
    assert config.prober_binary == "/wt/.sdo-prober/deadbeef/sdo-prober"
    assert config.prober_image == "img:prober"
    assert config.exit_after_closure is True
    assert config.restart_after_closure is False

    assert config.response_timeout.ToNanoseconds() == duration_nanoseconds("30m")
    assert config.verification_timeout.ToNanoseconds() == duration_nanoseconds("2m")
    assert config.follow_up_cooldown.ToNanoseconds() == duration_nanoseconds("45s")
    assert config.duration.ToNanoseconds() == duration_nanoseconds("10m")


def test_canonical_json_carries_exactly_the_expected_fields() -> None:
    config = _config_from(_COMMAND, prober_binary=Path("/wt/.sdo-prober/deadbeef/sdo-prober"))
    payload = _as_json(config)

    # protojson omits zero-valued fields, so a false bool never appears.
    assert set(payload) == _EXPECTED_CONFIG_FIELDS
    assert payload["duration"] == "600s"
    assert payload["response_timeout"] == "1800s"
    assert payload["max_follow_ups"] == 3
    assert payload["exit_after_closure"] is True
    assert "restart_after_closure" not in payload


def test_supervise_sets_restart_and_omits_prober_when_absent() -> None:
    command = [flag for flag in _COMMAND if flag != "--exit-after-closure"] + ["--supervise"]
    config = _config_from(command, prober_binary=None)
    payload = _as_json(config)

    assert config.restart_after_closure is True
    assert config.exit_after_closure is False
    assert config.prober_binary == ""
    assert payload["restart_after_closure"] is True
    assert "exit_after_closure" not in payload
    assert "prober_binary" not in payload
    assert "prober_image" not in payload
