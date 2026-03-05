from unittest.mock import MagicMock

import pytest

from app_operator.cli_agent.agents.code_analyzer import CodeAnalyzerAgent
from app_operator.config import OperatorConfig
from tests.fixtures.agents import ErrorAgent, StubAgent


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def agent():
    return StubAgent()


def test_run_generates_analysis_files_when_missing(repo_path, agent):
    """Test that CodeAnalyzerAgent generates analysis files when they don't exist.

    When code_analysis.md and deployment_issues.md are missing, the agent should
    invoke the coding agent to generate them.
    """

    # Setup: agent generates the files
    def fake_generate(prompt, cwd=None, timeout=None):
        sds_dir = repo_path / ".sds"
        sds_dir.mkdir(exist_ok=True)
        (sds_dir / "code_analysis.md").touch()
        (sds_dir / "deployment_issues.md").touch()
        return "Done"

    agent.generate = fake_generate

    analyzer = CodeAnalyzerAgent(repo_path, agent)
    assert analyzer.run() is True

    assert (repo_path / ".sds" / "code_analysis.md").exists()
    assert (repo_path / ".sds" / "deployment_issues.md").exists()


def test_run_skips_analysis_if_files_exist(repo_path, agent):
    """Test that CodeAnalyzerAgent skips analysis when files already exist.

    If code_analysis.md and deployment_issues.md are already present, the agent
    should skip analysis and not invoke the coding agent.
    """
    # Setup: files already exist
    sds_dir = repo_path / ".sds"
    sds_dir.mkdir()
    (sds_dir / "code_analysis.md").touch()
    (sds_dir / "deployment_issues.md").touch()

    # Mock generate to fail if called
    agent.generate = MagicMock(side_effect=Exception("Should not be called"))

    analyzer = CodeAnalyzerAgent(repo_path, agent)
    assert analyzer.run() is True


def test_run_returns_false_if_generation_fails(repo_path):
    """Test that CodeAnalyzerAgent handles agent errors gracefully.

    If the coding agent raises an error during generation, the analyzer
    should return False and not create incomplete files.
    """
    agent = ErrorAgent()
    analyzer = CodeAnalyzerAgent(repo_path, agent)

    assert analyzer.run() is False
    assert not (repo_path / ".sds" / "code_analysis.md").exists()


def test_run_returns_false_if_files_not_created(repo_path, agent):
    """Test that CodeAnalyzerAgent fails if expected files aren't created.

    If the agent completes without error but doesn't create the expected
    analysis files, the analyzer should detect this and return False.
    """
    # Setup: agent returns success but doesn't create files
    # StubAgent returns "stub response" by default and does nothing

    analyzer = CodeAnalyzerAgent(repo_path, agent)

    assert analyzer.run() is False


def test_run_uses_operator_config_timeout(repo_path):
    """Test that CodeAnalyzerAgent passes operator_config.agent_timeout to generate()."""
    captured = {}

    class CapturingAgent(StubAgent):
        def generate(self, prompt, cwd=None, timeout=None, silent=False, **kwargs):
            captured["timeout"] = timeout
            sds_dir = repo_path / ".sds"
            sds_dir.mkdir(exist_ok=True)
            (sds_dir / "code_analysis.md").touch()
            (sds_dir / "deployment_issues.md").touch()
            return "Done"

    op_config = OperatorConfig(agent_timeout=1234)
    analyzer = CodeAnalyzerAgent(repo_path, CapturingAgent(), operator_config=op_config)
    analyzer.run()

    assert captured["timeout"] == 1234
