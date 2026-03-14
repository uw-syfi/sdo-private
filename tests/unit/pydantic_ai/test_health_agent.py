"""Tests for HealthAgent.run_check explicit parameter contract."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from app_operator.config import AgentConfig, Config, DeploymentConfig, RuntimeConfig
from app_operator.filesystem import InMemoryFilesystem
from app_operator.prompts import PromptLoader
from app_operator.pydantic_ai._deps import OperatorDeps
from app_operator.pydantic_ai._responses import HealthVerdictResponse
from app_operator.pydantic_ai.agents.health import HealthAgent
from app_operator.trajectory import Phase

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_config():
    return Config(
        agent=AgentConfig(provider="openai", model="gpt-4o"),
        deployment=DeploymentConfig(platform="docker", target="local"),
        runtime=RuntimeConfig(impl="pydantic_ai"),
    )


def _make_recorder():
    recorder = MagicMock()
    recorder.record_run = MagicMock()
    return recorder


def _make_health_agent(tmp_path):
    """Return a HealthAgent wired with a TestModel and in-memory filesystem."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    filesystem = InMemoryFilesystem()
    filesystem.mkdir(repo_path)

    config = _make_config()
    loader = MagicMock(spec=PromptLoader)
    loader.render.return_value = "mocked prompt"

    deps = OperatorDeps(
        repo_path=repo_path,
        filesystem=filesystem,
        loader=loader,
        config=config,
    )

    recorder = _make_recorder()

    agent = HealthAgent(
        model="test",
        model_settings=None,
        tools=[],
        deps=deps,
        recorder=recorder,
    )

    # Replace the internal pydantic_ai Agent with a TestModel-backed one that
    # returns a valid HealthVerdictResponse.
    agent._agent = Agent(
        TestModel(call_tools=[]),
        deps_type=OperatorDeps,
        output_type=HealthVerdictResponse,
    )

    return agent, repo_path, filesystem


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_check_deployment_phase_succeeds(tmp_path):
    """run_check works correctly for DEPLOYMENT phase with attempt=1."""
    agent, repo_path, filesystem = _make_health_agent(tmp_path)

    with patch("app_operator.pydantic_ai.agents.health.write_log_file") as mock_write:
        verdict = agent.run_check(phase=Phase.DEPLOYMENT, attempt=1)

    assert isinstance(verdict, HealthVerdictResponse)
    mock_write.assert_called_once()
    log_path_arg = mock_write.call_args[0][1]
    assert "health_check_attempt_1" in str(log_path_arg)


def test_run_check_monitoring_phase_succeeds(tmp_path):
    """run_check works correctly for MONITORING phase with cycle=1."""
    agent, repo_path, filesystem = _make_health_agent(tmp_path)

    with patch("app_operator.pydantic_ai.agents.health.write_log_file") as mock_write:
        verdict = agent.run_check(phase=Phase.MONITORING, cycle=1)

    assert isinstance(verdict, HealthVerdictResponse)
    mock_write.assert_called_once()
    log_path_arg = mock_write.call_args[0][1]
    assert "check_1_" in str(log_path_arg)
    assert "monitor" in str(log_path_arg)


def test_run_check_deployment_phase_missing_attempt_raises(tmp_path):
    """run_check raises ValueError if attempt is missing for DEPLOYMENT phase."""
    agent, _repo_path, _filesystem = _make_health_agent(tmp_path)

    with pytest.raises(ValueError, match="attempt is required for DEPLOYMENT phase"):
        agent.run_check(phase=Phase.DEPLOYMENT)


def test_run_check_monitoring_phase_missing_cycle_raises(tmp_path):
    """run_check raises ValueError if cycle is missing for MONITORING phase."""
    agent, _repo_path, _filesystem = _make_health_agent(tmp_path)

    with pytest.raises(ValueError, match="cycle is required for MONITORING phase"):
        agent.run_check(phase=Phase.MONITORING)
