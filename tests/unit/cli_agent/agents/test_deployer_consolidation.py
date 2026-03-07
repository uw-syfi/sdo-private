from unittest.mock import MagicMock

import pytest

from app_operator.cli_agent.agents.deployer import DeploymentAgent, get_fix_summary_path
from app_operator.config import OperatorConfig, OperatorPhaseConfig
from tests.fixtures.agents import ConfigurableAgent, StubAgent


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()
    (sds_dir / "logs").mkdir()
    (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
    (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
    return repo


@pytest.fixture
def consolidation_agent():
    agent = ConfigurableAgent()
    # When asked to consolidate (which involves "fix_summary"), return a structured summary
    # We can assume the prompt will contain "fix_summary" or similar from the template
    agent.set_response("consolidated summary", "## Attempt 1\nFix summary 1")
    agent.set_default_response("stub response")
    return agent


def test_update_consolidated_summary_creates_new_file(repo_path):
    agent_mock = ConfigurableAgent()
    # Configure it to return what we expect the summary to look like
    expected_content = "## Attempt 1\nFix summary 1"
    agent_mock.set_default_response(expected_content)

    agent = DeploymentAgent(repo_path, agent_mock)
    summary = "Fix summary 1"

    # Pre-create log file as the method reads it for history
    (repo_path / ".sds" / "logs" / "fix_summary_1.log").write_text(summary)

    agent._update_consolidated_summary(1, summary)

    summary_file = get_fix_summary_path(repo_path / ".sds")
    assert summary_file.exists()
    content = summary_file.read_text()
    assert expected_content == content


def test_update_consolidated_summary_appends(repo_path):
    agent_mock = ConfigurableAgent()
    expected_content = "## Attempt 1\nOld summary\n## Attempt 2\nFix summary 2"
    agent_mock.set_default_response(expected_content)

    agent = DeploymentAgent(repo_path, agent_mock)

    # Setup initial state
    summary_file = get_fix_summary_path(repo_path / ".sds")
    summary_file.write_text("## Attempt 1\n\nOld summary\n")

    summary_2 = "Fix summary 2"
    (repo_path / ".sds" / "logs" / "fix_summary_2.log").write_text(summary_2)

    agent._update_consolidated_summary(2, summary_2)

    content = summary_file.read_text()
    assert expected_content == content


def test_update_consolidated_summary_respects_interval(repo_path, monkeypatch):
    # Set interval to 2
    monkeypatch.setattr("app_operator.cli_agent.agents.deployer.FIX_SUMMARY_CONSOLIDATION_INTERVAL", 2)

    agent_mock = ConfigurableAgent()
    expected_content = "## Attempt 1\nsummary 1\n## Attempt 2\nsummary 2"
    agent_mock.set_default_response(expected_content)

    agent = DeploymentAgent(repo_path, agent_mock)

    # Attempt 1: Should not update
    agent._update_consolidated_summary(1, "summary 1")
    summary_file = get_fix_summary_path(repo_path / ".sds")
    assert not summary_file.exists()

    # Attempt 2: Should update
    (repo_path / ".sds" / "logs" / "fix_summary_1.log").write_text("summary 1")
    (repo_path / ".sds" / "logs" / "fix_summary_2.log").write_text("summary 2")

    agent._update_consolidated_summary(2, "summary 2")

    assert summary_file.exists()
    content = summary_file.read_text()
    assert expected_content == content


def test_run_cleans_summary_on_fresh_start(repo_path, monkeypatch):
    agent = DeploymentAgent(repo_path, StubAgent())
    summary_file = get_fix_summary_path(repo_path / ".sds")
    summary_file.write_text("Old summary")

    # Mock _get_next_attempt_number to return 1
    monkeypatch.setattr(agent, "_get_next_attempt_number", lambda: 1)

    # Mock run_deploy_command to just return success to stop loop
    agent.run_deploy_command = MagicMock(return_value={"success": True, "exit_code": 0})

    agent.run(max_attempts=1)

    # Should be removed
    assert not summary_file.exists()


def test_run_keeps_summary_on_resume(repo_path, monkeypatch):
    agent = DeploymentAgent(repo_path, StubAgent())
    summary_file = get_fix_summary_path(repo_path / ".sds")
    summary_file.write_text("Old summary")

    # Mock _get_next_attempt_number to return 2
    monkeypatch.setattr(agent, "_get_next_attempt_number", lambda: 2)

    agent.run_deploy_command = MagicMock(return_value={"success": True, "exit_code": 0})

    agent.run(max_attempts=1)

    assert summary_file.exists()
    assert summary_file.read_text() == "Old summary"


def test_fix_with_agent_skips_consolidation_when_disabled(repo_path, monkeypatch):
    """When fix_summary_consolidation=False, _fix_with_agent does not create the consolidated summary file."""
    agent_mock = ConfigurableAgent()
    agent_mock.set_default_response("<summary>Fix applied</summary>")

    operator_config = OperatorConfig(phase=OperatorPhaseConfig(fix_summary_consolidation=False))
    deployer = DeploymentAgent(repo_path, agent_mock, operator_config=operator_config)

    deploy_result = {"success": False, "exit_code": 1, "stdout": "", "stderr": "error"}
    deployer._fix_with_agent(deploy_result, None, 1, 5)

    summary_file = get_fix_summary_path(repo_path / ".sds")
    assert not summary_file.exists()


def test_fix_with_agent_creates_consolidation_when_enabled(repo_path, monkeypatch):
    """When fix_summary_consolidation=True (default), _fix_with_agent creates the consolidated summary file."""
    agent_mock = ConfigurableAgent()
    agent_mock.set_default_response("<summary>Fix applied</summary>")

    deployer = DeploymentAgent(repo_path, agent_mock)

    deploy_result = {"success": False, "exit_code": 1, "stdout": "", "stderr": "error"}
    deployer._fix_with_agent(deploy_result, None, 1, 5)

    summary_file = get_fix_summary_path(repo_path / ".sds")
    assert summary_file.exists()


def test_run_skips_summary_cleanup_when_disabled(repo_path, monkeypatch):
    """When fix_summary_consolidation=False, run() does not remove existing summary on fresh start."""
    operator_config = OperatorConfig(phase=OperatorPhaseConfig(fix_summary_consolidation=False))
    deployer = DeploymentAgent(repo_path, StubAgent(), operator_config=operator_config)

    summary_file = get_fix_summary_path(repo_path / ".sds")
    summary_file.write_text("Old summary")

    monkeypatch.setattr(deployer, "_get_next_attempt_number", lambda: 1)
    deployer.run_deploy_command = MagicMock(return_value={"success": True, "exit_code": 0})

    deployer.run(max_attempts=1)

    assert summary_file.exists()
    assert summary_file.read_text() == "Old summary"
