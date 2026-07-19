from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, cast

from sdo.operational_memory.sandbox import ContainerSandboxRunner

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_container_sandbox_is_networkless_readonly_limited_and_credential_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    runner = ContainerSandboxRunner(
        runtime="docker",
        image="validator:test",
        timeout_seconds=7,
        command_runner=fake_run,
    )

    result = runner.run(tmp_path)

    raw_command = captured["command"]
    assert isinstance(raw_command, list)
    command = cast("list[str]", raw_command)
    assert command[command.index("--network") : command.index("--network") + 2] == ["--network", "none"]
    assert "--read-only" in command
    assert "--cap-drop" in command
    assert "ALL" in command
    assert "no-new-privileges" in command
    assert "--cpus" in command
    assert "--memory" in command
    assert "--pids-limit" in command
    assert command[command.index("--user") : command.index("--user") + 2] == [
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]
    assert f"{tmp_path.resolve()}:/workspace:ro" in command
    assert "GOCACHE=/tmp/go-cache" in command
    assert "GOMODCACHE=/go/pkg/mod" in command
    assert "GOPROXY=off" in command
    assert "GOSUMDB=off" in command
    assert "GOMAXPROCS=2" in command
    assert "GOFLAGS=-p=2" in command
    assert "env" in command
    assert "-i" in command
    assert "must-not-leak" not in " ".join(command)
    assert captured["env"] == {"PATH": os.environ.get("PATH", "")}
    assert captured["timeout"] == 7
    assert result.returncode == 0


def test_container_sandbox_converts_hung_validation_to_failure(tmp_path: Path) -> None:
    def timeout(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired("docker", 1)

    result = ContainerSandboxRunner(runtime="docker", timeout_seconds=1, command_runner=timeout).run(tmp_path)

    assert result.returncode == 124
    assert result.timed_out is True
    assert "timed out" in result.stderr


def test_container_sandbox_default_timeout_allows_cold_offline_go_build(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = ContainerSandboxRunner(runtime="docker", command_runner=fake_run).run(tmp_path)

    assert captured["timeout"] == 600
    assert result.returncode == 0
