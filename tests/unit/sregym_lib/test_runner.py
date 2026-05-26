"""Unit tests for libs.sregym_lib.runner helpers."""

from __future__ import annotations

import json
from pathlib import Path

from libs.sregym_lib.runner import _inject_memory_defaults


def _cfg(env: dict[str, str]) -> dict:
    return json.loads(env["SREGYM_EXPERIMENT_AGENT_CONFIG"])


def test_inject_memory_defaults_sets_dir_when_enabled() -> None:
    env = {"SREGYM_EXPERIMENT_AGENT_CONFIG": json.dumps({"memory_enabled": True})}
    out = _inject_memory_defaults(env, "cli_agent", Path("/logs/exp1"))
    assert _cfg(out)["memory_dir"] == str(Path("/logs/exp1") / "memory")


def test_inject_memory_defaults_noop_when_disabled() -> None:
    env = {"SREGYM_EXPERIMENT_AGENT_CONFIG": json.dumps({"memory_enabled": False})}
    out = _inject_memory_defaults(env, "cli_agent", Path("/logs/exp1"))
    assert "memory_dir" not in _cfg(out)


def test_inject_memory_defaults_preserves_explicit_dir() -> None:
    env = {"SREGYM_EXPERIMENT_AGENT_CONFIG": json.dumps({"memory_enabled": True, "memory_dir": "/custom/store"})}
    out = _inject_memory_defaults(env, "cli_agent", Path("/logs/exp1"))
    assert _cfg(out)["memory_dir"] == "/custom/store"


def test_inject_memory_defaults_only_for_cli_agent() -> None:
    env = {"SREGYM_EXPERIMENT_AGENT_CONFIG": json.dumps({"memory_enabled": True})}
    out = _inject_memory_defaults(env, "crucible", Path("/logs/exp1"))
    assert out is env  # untouched for other agents


def test_inject_memory_defaults_tolerates_missing_config() -> None:
    out = _inject_memory_defaults({}, "cli_agent", Path("/logs/exp1"))
    assert out == {}
