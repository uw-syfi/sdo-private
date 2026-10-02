"""Flag contract between every controller launcher and the entry points it starts.

The controller Job runs ``sdo-detector-check controller <args>``, which builds the Go runtime command, and runs
``broker_cli`` for the broker. A flag a launcher passes that an entry point does not define makes every controller pod
exit with ``unrecognized arguments`` before any incident runs. These tests parse the real argument lists with the real
parsers, so such a gap fails here, with no cluster.
"""

from __future__ import annotations

import re
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

import pytest

from benchmarks.sregym.adapter import RuntimeConfig
from benchmarks.sregym.adapter.runtime import runtime_resources
from controller.builder import check_cli
from sdo.agent_runtime.responder import broker_cli
from sdo.controller_install import ControllerInstallConfig, controller_resources

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
GO_RUNTIME_SOURCE = REPOSITORY_ROOT / "controller" / "runtime" / "run.go"
#: Go flags whose value is itself a command-line fragment; the fragment is not a flag of the Go runtime.
GO_FLAGS_WITH_FRAGMENT_VALUES = {"--broker-arg", "--dispatcher-arg", "--responder-env"}

_ALL_FEATURES: dict[str, Any] = {
    "controller_namespace": "demo-control",
    "late_findings": "pull",
    "max_follow_ups": 3,
    "follow_up_cooldown_seconds": 30,
    "closeout_state_gate": True,
    "healthy_baseline": True,
    "reflection_session": "fresh",
    "reflection_guidance": "generalize",
}
_ONE_FEATURE_EACH: list[dict[str, Any]] = [
    {},
    {"controller_namespace": "demo-control"},
    {"late_findings": "pull"},
    {"max_follow_ups": 2},
    {"closeout_state_gate": True},
    {"healthy_baseline": True},
    _ALL_FEATURES,
]


def _installer_config(**features: Any) -> ControllerInstallConfig:
    return ControllerInstallConfig(
        repository=Path("/tmp/application"),
        namespace="demo",
        application="demo",
        controller_image="sdo-controller:test",
        responder_image="sdo-responder:test",
        validator_image="sdo-detector-validator:test",
        repository_pvc="repository",
        credentials_secret="credentials",
        model="gpt-6-luna",
        timeout_seconds=60,
        **features,
    )


def _benchmark_config(*, persistent: bool, **features: Any) -> RuntimeConfig:
    return RuntimeConfig(
        repository=Path("/tmp/application"),
        namespace="demo",
        application="demo",
        controller_image="sdo-controller:test",
        responder_image="sdo-responder:test",
        validator_image="sdo-detector-validator:test",
        repository_pvc="repository",
        credentials_secret="credentials",
        model="gpt-6-luna",
        timeout_seconds=60,
        submission_api_base="http://submission:8000",
        persistent=persistent,
        **features,
    )


def _controller_arguments(resources: list[dict[str, Any]]) -> list[str]:
    (job,) = (resource for resource in resources if resource["kind"] == "Job")
    (container,) = (c for c in job["spec"]["template"]["spec"]["containers"] if c["name"] == "controller")
    return list(container["args"])


def launcher_argument_lists(features: dict[str, Any]) -> dict[str, list[str]]:
    """The controller arguments each launcher produces for *features*, by launcher name."""

    return {
        "installer": _controller_arguments(controller_resources(_installer_config(**features))),
        "benchmark-bounded": _controller_arguments(runtime_resources(_benchmark_config(persistent=False, **features))),
        "benchmark-persistent": _controller_arguments(
            runtime_resources(_benchmark_config(persistent=True, **features))
        ),
    }


def unaccepted_arguments(parser: ArgumentParser, arguments: list[str]) -> list[str]:
    """The arguments *parser* does not define (``parse_known_args`` keeps them aside instead of exiting)."""

    _, unknown = parser.parse_known_args(arguments)
    return unknown


def go_runtime_flags(source: str) -> set[str]:
    """Every flag the Go controller runtime defines, read from its flag declarations."""

    typed = re.findall(r'flags\.(?:String|Bool|Duration|Int|Int64|Float64)\(\s*"([a-z][a-z0-9-]*)"', source)
    variadic = re.findall(r'flags\.Var\(\s*&\w+,\s*"([a-z][a-z0-9-]*)"', source)
    return {f"--{name}" for name in (*typed, *variadic)}


def used_go_flags(command: list[str]) -> set[str]:
    """The flags in a Go runtime command, skipping values that are fragments for another program."""

    flags: set[str] = set()
    skip_next = False
    for token in command[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in GO_FLAGS_WITH_FRAGMENT_VALUES:
            flags.add(token)
            skip_next = True
        elif token.startswith("--"):
            flags.add(token)
    return flags


def _broker_arguments(arguments: list[str]) -> list[str]:
    values = [argument.removeprefix("--broker-arg=") for argument in arguments if argument.startswith("--broker-arg=")]
    assert values[:2] == ["-m", "sdo.agent_runtime.responder.broker_cli"], values
    return values[2:]


@pytest.mark.parametrize("features", _ONE_FEATURE_EACH, ids=lambda features: "+".join(sorted(features)) or "defaults")
def test_the_controller_launcher_accepts_every_flag_each_launcher_passes(features: dict[str, Any]) -> None:
    parser = check_cli._build_parser()

    for launcher, arguments in launcher_argument_lists(features).items():
        assert unaccepted_arguments(parser, arguments) == [], f"{launcher} passes flags sdo-detector-check rejects"


@pytest.mark.parametrize("features", _ONE_FEATURE_EACH, ids=lambda features: "+".join(sorted(features)) or "defaults")
def test_the_broker_accepts_every_argument_each_launcher_passes(features: dict[str, Any]) -> None:
    parser = broker_cli._argument_parser()
    required = ["--repository", "/repository", "--worktree-root", "/worktrees"]

    for launcher, arguments in launcher_argument_lists(features).items():
        broker_arguments = [*required, *_broker_arguments(arguments)]
        assert unaccepted_arguments(parser, broker_arguments) == [], f"{launcher} passes flags the broker rejects"


@pytest.mark.parametrize("features", _ONE_FEATURE_EACH, ids=lambda features: "+".join(sorted(features)) or "defaults")
def test_the_go_runtime_defines_every_flag_the_controller_launcher_forwards(features: dict[str, Any]) -> None:
    defined = go_runtime_flags(GO_RUNTIME_SOURCE.read_text(encoding="utf-8"))
    parser = check_cli._build_parser()

    for launcher, arguments in launcher_argument_lists(features).items():
        namespace = parser.parse_args(arguments)
        command = check_cli.runtime_command(
            namespace,
            binary=Path("/build/sdo-controller"),
            app_root=Path("/workspace/application"),
            worktree_root=Path("/workspace/worktrees"),
            prober_binary=Path("/workspace/.sdo-prober/prober"),
        )
        assert sorted(used_go_flags(command) - defined) == [], f"{launcher}: the Go runtime rejects these flags"


def test_closeout_gate_reaches_the_go_runtime_only_when_asked() -> None:
    parser = check_cli._build_parser()

    def forwarded(features: dict[str, Any]) -> set[str]:
        arguments = launcher_argument_lists(features)["benchmark-persistent"]
        command = check_cli.runtime_command(
            parser.parse_args(arguments),
            binary=Path("/build/sdo-controller"),
            app_root=Path("/workspace/application"),
            worktree_root=Path("/workspace/worktrees"),
            prober_binary=None,
        )
        return used_go_flags(command)

    assert "--closeout-state-gate" in forwarded({"closeout_state_gate": True, "max_follow_ups": 1})
    assert "--closeout-state-gate" not in forwarded({})


def test_responder_environment_assignments_are_name_value_pairs() -> None:
    for arguments in launcher_argument_lists(_ALL_FEATURES).values():
        assignments = [
            argument.removeprefix("--responder-env=") for argument in arguments if "--responder-env=" in argument
        ]
        assert assignments
        assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*=.*", assignment, re.DOTALL) for assignment in assignments), (
            assignments
        )


def test_the_contract_check_catches_a_flag_the_launcher_parser_lacks() -> None:
    """Regression for the controller pods that exited on ``--closeout-state-gate`` before any incident ran."""

    parser = ArgumentParser()
    parser.add_argument("--namespace")

    assert unaccepted_arguments(parser, ["--namespace", "demo", "--closeout-state-gate"]) == ["--closeout-state-gate"]


def test_go_flag_extraction_reads_typed_and_repeated_flag_declarations() -> None:
    source = (
        'x := flags.String("app-root", "", "help")\n'
        'y := flags.Bool(\n\t"closeout-state-gate",\n\tfalse,\n\t"help",\n)\n'
        'flags.Var(&brokerArgs, "broker-arg", "help")\n'
    )

    assert go_runtime_flags(source) == {"--app-root", "--closeout-state-gate", "--broker-arg"}
