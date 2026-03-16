"""Tests for RepairAgent — verifies HealthVerdictResponse is accepted directly."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from app_operator.pydantic_ai._responses import HealthVerdictResponse

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repair_agent(repo_path: Path, config):
    """Construct a RepairAgent with mocked pydantic_ai internals."""
    from app_operator.filesystem import InMemoryFilesystem
    from app_operator.prompts import get_loader
    from app_operator.pydantic_ai._deps import OperatorDeps
    from app_operator.pydantic_ai.agents.repair import RepairAgent

    fs = InMemoryFilesystem()
    fs.mkdir(repo_path)
    fs.mkdir(repo_path / ".sds" / "logs")

    deps = OperatorDeps(
        repo_path=repo_path,
        filesystem=fs,
        loader=get_loader(),
        config=config,
    )
    recorder = MagicMock()
    recorder.record_run = MagicMock()

    agent = RepairAgent(
        model="test",
        model_settings=None,
        tools=[],
        deps=deps,
        recorder=recorder,
        max_attempts=3,
    )
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_with_health_verdict_response_no_round_trip(tmp_path, mock_config):
    """RepairAgent.run() accepts HealthVerdictResponse without dict round-trip."""
    health_verdict = HealthVerdictResponse(
        healthy=False,
        assessment="App is down",
        diagnosis="Port 8080 not listening",
        script_was_fixed=False,
    )
    deploy_result = {"success": False, "exit_code": 1, "stdout": "", "stderr": "error"}

    agent = _make_repair_agent(tmp_path / "repo", mock_config)

    mock_run_result = MagicMock()
    mock_run_result.output = MagicMock()
    mock_run_result.output.summary = "Applied fix"

    with patch.object(agent, "_run", return_value=mock_run_result) as mock_run:
        agent.run(deploy_result, health_verdict, attempt=1)

    # _run was called — the agent didn't crash trying to convert HealthVerdictResponse
    mock_run.assert_called_once()

    # prepare_error_context was called with the HealthVerdictResponse directly;
    # verify the prompt passed to _run contains health info from the response
    prompt_arg = mock_run.call_args[0][0]
    assert "UNHEALTHY" in prompt_arg or "App is down" in prompt_arg or "Port 8080" in prompt_arg


def test_run_with_none_health_verdict(tmp_path, mock_config):
    """RepairAgent.run() handles health_verdict=None without error."""
    deploy_result = {"success": False, "exit_code": 1, "stdout": "", "stderr": "error"}

    agent = _make_repair_agent(tmp_path / "repo", mock_config)

    mock_run_result = MagicMock()
    mock_run_result.output = MagicMock()
    mock_run_result.output.summary = "Applied fix"

    with patch.object(agent, "_run", return_value=mock_run_result) as mock_run:
        agent.run(deploy_result, None, attempt=1)

    mock_run.assert_called_once()


def test_health_verdict_response_fields_propagate_to_error_context():
    """prepare_error_context accepts HealthVerdictResponse directly (no HealthVerdict needed)."""
    from app_operator.prompts import prepare_error_context

    health_verdict = HealthVerdictResponse(
        healthy=False,
        assessment="Services unreachable",
        diagnosis="Redis connection refused",
        script_was_fixed=False,
    )
    deploy_result = {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}

    # Should not raise — HealthVerdictResponse satisfies HealthVerdictLike protocol
    context = prepare_error_context(deploy_result, health_verdict)

    assert "UNHEALTHY" in context
    assert "Redis connection refused" in context
    assert "Services unreachable" in context
