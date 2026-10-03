"""The Go controller parses the ControllerConfig the Python launcher writes.

This is the inverse of ``tests/fixtures/sdo/contracts/go`` (Go writes, Python
reads). Seam 3 makes the launcher -> controller boundary a single
``ControllerConfig`` serialized as canonical protojson and mounted via
``--config``. Nothing in CI launches a real controller with that file, so a
drift between Python's encoder and Go's ``LoadControllerConfig`` /
``controllerConfigToArgs`` would only surface as a controller pod failing to
start mid-experiment.

This test pins the launcher's real output (built by ``check_cli``'s own
``_controller_config`` from a representative ``controller`` command) into a
committed golden. ``controller/runtime`` has a matching Go test that loads this
exact file through the controller's parser. Regenerate the golden with::

    SDO_UPDATE_PYTHON_CONTRACT_FIXTURES=1 uv run --extra test pytest \\
        tests/unit/sdo/contracts/test_python_controller_config_fixture.py

and then the Go side (``go test ./... -run TestPythonControllerConfigFixture``
in ``controller/runtime``) has to accept the new output.
"""

from __future__ import annotations

import os
from pathlib import Path

from controller.builder.check_cli import _build_parser, _controller_config
from sdo.contracts.proto import to_canonical_json

_UPDATE_ENV = "SDO_UPDATE_PYTHON_CONTRACT_FIXTURES"

_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "sdo" / "contracts" / "python" / "controller_config.json"

# A representative, fully specified production launch (job dispatcher mode, a
# prober, follow-ups, both commits, a repository subpath). The Go test asserts
# the flags this expands to, so keep the two in lockstep when editing.
_COMMAND = [
    "controller",
    "--app",
    "/workspace/application",
    "--namespace",
    "hotel-reservation",
    "--control-namespace",
    "hotel-reservation-sdo",
    "--application",
    "hotel-reservation",
    "--source-commit",
    "abc1234def",
    "--deployed-commit",
    "99887766aa",
    "--responder-image",
    "sdo-responder:v1",
    "--prober-image",
    "sdo-controller:v1",
    "--repository-pvc",
    "sdo-repository",
    "--repository-mount-path",
    "/workspace",
    "--repository-pvc-subpath",
    "repo",
    "--credentials-secret",
    "sdo-codex-credentials",
    "--worktree-root",
    "/workspace/worktrees",
    "--responder-command",
    "/usr/bin/python3",
    "--responder-arg=-m",
    "--responder-arg",
    "sdo.agent_runtime.responder.job",
    "--responder-env",
    "SDO_MODEL=luna",
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
    "2",
    "--follow-up-cooldown",
    "30s",
    "--repair-policy",
    "recorded-actions",
    "--duration",
    "1h",
    "--lease-name",
    "sdo-controller",
    "--exit-after-closure",
]

_PROBER_BINARY = Path("/workspace/.sdo-prober/0123456789abcdef/sdo-prober")


def _launcher_config_json() -> str:
    args = _build_parser().parse_args(_COMMAND)
    config = _controller_config(
        args,
        app_root=Path("/workspace/application"),
        worktree_root=Path("/workspace/worktrees"),
        responder_args=list(args.responder_arg),
        broker_args=list(args.broker_arg),
        prober_binary=_PROBER_BINARY,
    )
    return to_canonical_json(config)


def test_python_controller_config_fixture_matches_the_launcher_output() -> None:
    want = _launcher_config_json()
    if os.environ.get(_UPDATE_ENV) == "1":
        _FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        _FIXTURE.write_text(want, encoding="utf-8")
        return
    assert _FIXTURE.exists(), f"missing golden; regenerate with {_UPDATE_ENV}=1"
    assert _FIXTURE.read_text(encoding="utf-8") == want, (
        f"the launcher's ControllerConfig output drifted from the committed golden; "
        f"regenerate with {_UPDATE_ENV}=1 and rerun the Go cross-language test."
    )
