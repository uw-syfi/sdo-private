from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"
if not (BENCH_ROOT / "sregym" / "agent_exit.py").exists():
    pytest.skip(
        "bench/sregym submodule not checked out — skipping agent_exit tests",
        allow_module_level=True,
    )
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

agent_exit = importlib.import_module("sregym.agent_exit")
agent_registry = importlib.import_module("sregym.agent_registry")
DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS = agent_exit.DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS
resolve_graceful_exit_timeout_seconds = agent_exit.resolve_graceful_exit_timeout_seconds
wait_for_process_exit = agent_exit.wait_for_process_exit
AgentRegistration = agent_registry.AgentRegistration
list_agents = agent_registry.list_agents


class _FakeProcess:
    def __init__(self, *, exit_after_polls: int):
        self._exit_after_polls = exit_after_polls
        self._poll_count = 0
        self.returncode: int | None = None

    def poll(self) -> int | None:
        self._poll_count += 1
        if self._poll_count >= self._exit_after_polls:
            self.returncode = 0
        return self.returncode


@pytest.mark.asyncio
async def test_wait_for_process_exit_times_out_for_generic_agent_cutoff():
    proc = _FakeProcess(exit_after_polls=32)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    completed = await wait_for_process_exit(
        proc,
        timeout_seconds=DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS,
        sleep=fake_sleep,
    )

    assert completed is False
    assert proc.returncode is None
    assert len(sleep_calls) == int(DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS)


@pytest.mark.asyncio
async def test_wait_for_process_exit_allows_unbounded_finalization():
    proc = _FakeProcess(exit_after_polls=32)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    completed = await wait_for_process_exit(
        proc,
        timeout_seconds=None,
        sleep=fake_sleep,
    )

    assert completed is True
    assert proc.returncode == 0
    assert len(sleep_calls) > int(DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS)


def test_graceful_exit_timeout_defaults_to_generic_cutoff():
    assert resolve_graceful_exit_timeout_seconds(None) == DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS
    assert (
        resolve_graceful_exit_timeout_seconds(AgentRegistration(name="generic-agent"))
        == DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS
    )


def test_graceful_exit_timeout_can_be_opted_out_per_agent():
    assert resolve_graceful_exit_timeout_seconds(AgentRegistration(name="crucible", wait_for_natural_exit=True)) is None


def test_list_agents_parses_optional_wait_for_natural_exit(tmp_path: Path):
    registry_path = tmp_path / "agents.yaml"
    registry_path.write_text(
        "agents:\n"
        "  - name: generic-agent\n"
        "    kickoff_command: python -m generic\n"
        "  - name: crucible\n"
        "    kickoff_command: uv run python -m sregym_agents.crucible.driver\n"
        "    wait_for_natural_exit: true\n"
    )

    agents = list_agents(registry_path)

    assert agents["generic-agent"].wait_for_natural_exit is None
    assert agents["crucible"].wait_for_natural_exit is True
