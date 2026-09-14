from __future__ import annotations

import os
import signal
import subprocess
from typing import TYPE_CHECKING

import pytest

from sdo.operational_memory.sandbox import ContainerSandboxRunner

if TYPE_CHECKING:
    from pathlib import Path


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

    command = captured["command"]
    assert isinstance(command, list)
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
    assert "SDO_GO_CACHE_SEED=/opt/sdo/go-build-cache" in command
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


def test_container_sandbox_kills_process_group_and_removes_named_container_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimedOutProcess:
        pid = 4321
        returncode = None

        def __init__(self) -> None:
            self.communicate_calls = 0

        def communicate(self, **kwargs: object) -> tuple[str, str]:
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired(["docker"], kwargs.get("timeout"))
            self.returncode = -signal.SIGKILL
            return "partial stdout", "partial stderr"

    process = TimedOutProcess()
    launched: dict[str, object] = {}
    killed: list[tuple[int, signal.Signals]] = []
    cleanup_commands: list[list[str]] = []

    class FakeWatchdog:
        def terminate(self) -> None:
            pass

        def wait(self, **_kwargs: object) -> int:
            return 0

    def fake_popen(command: list[str], **kwargs: object) -> TimedOutProcess:
        launched.update(command=command, kwargs=kwargs)
        return process

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        cleanup_commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("sdo.operational_memory.sandbox.subprocess.Popen", fake_popen)
    monkeypatch.setattr("sdo.operational_memory.sandbox.subprocess.run", fake_run)
    monkeypatch.setattr("sdo.operational_memory.sandbox.os.killpg", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(ContainerSandboxRunner, "_start_cleanup_watchdog", lambda *_args: FakeWatchdog())

    result = ContainerSandboxRunner(runtime="docker", timeout_seconds=1).run(tmp_path)

    command = launched["command"]
    assert isinstance(command, list)
    container_name = command[command.index("--name") + 1]
    assert launched["kwargs"]["start_new_session"] is True
    assert killed == [(4321, signal.SIGKILL)]
    assert cleanup_commands == [
        ["docker", "image", "inspect", "--format={{.Id}}", "sdo-detector-validator:v0.1.0"],
        ["docker", "rm", "--force", container_name],
    ]
    assert result.returncode == 124
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"


def test_container_sandbox_cleans_named_container_when_worker_receives_sigterm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers: dict[signal.Signals, object] = {}
    launched_command: list[str] = []
    cleanup_commands: list[list[str]] = []

    class FakeWatchdog:
        def terminate(self) -> None:
            pass

        def wait(self, **_kwargs: object) -> int:
            return 0

    class TerminatedProcess:
        pid = 9876
        returncode = None

        def communicate(self, **_kwargs: object) -> tuple[str, str]:
            handler = handlers.get(signal.SIGTERM)
            assert callable(handler), "validator must install a SIGTERM cleanup handler"
            handler(signal.SIGTERM, None)
            raise AssertionError("SIGTERM handler must terminate validation")

    def fake_popen(command: list[str], **_kwargs: object) -> TerminatedProcess:
        launched_command.extend(command)
        return TerminatedProcess()

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        cleanup_commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_signal(signum: signal.Signals, handler: object) -> object:
        previous = handlers.get(signum, signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    monkeypatch.setattr("sdo.operational_memory.sandbox.subprocess.Popen", fake_popen)
    monkeypatch.setattr("sdo.operational_memory.sandbox.subprocess.run", fake_run)
    monkeypatch.setattr("sdo.operational_memory.sandbox.signal.signal", fake_signal)
    monkeypatch.setattr("sdo.operational_memory.sandbox.os.killpg", lambda *_args: None)
    monkeypatch.setattr(ContainerSandboxRunner, "_start_cleanup_watchdog", lambda *_args: FakeWatchdog())

    with pytest.raises(SystemExit) as captured:
        ContainerSandboxRunner(runtime="docker").run(tmp_path)

    container_name = launched_command[launched_command.index("--name") + 1]
    assert captured.value.code == 128 + signal.SIGTERM
    assert cleanup_commands == [
        ["docker", "image", "inspect", "--format={{.Id}}", "sdo-detector-validator:v0.1.0"],
        ["docker", "rm", "--force", container_name],
    ]


def test_container_sandbox_default_timeout_allows_cold_offline_go_build(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    result = ContainerSandboxRunner(runtime="docker", command_runner=fake_run).run(tmp_path)

    assert captured["timeout"] == 600
    assert result.returncode == 0
