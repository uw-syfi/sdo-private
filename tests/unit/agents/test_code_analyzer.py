import pytest
from unittest.mock import MagicMock
from app_operator.agents.code_analyzer import CodeAnalyzerAgent
from tests.fixtures.agents import StubAgent, ErrorAgent


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def agent():
    return StubAgent()


def test_run_generates_analysis_files_when_missing(repo_path, agent):
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
    agent = ErrorAgent()
    analyzer = CodeAnalyzerAgent(repo_path, agent)

    assert analyzer.run() is False
    assert not (repo_path / ".sds" / "code_analysis.md").exists()


def test_run_returns_false_if_files_not_created(repo_path, agent):
    # Setup: agent returns success but doesn't create files
    # StubAgent returns "stub response" by default and does nothing

    analyzer = CodeAnalyzerAgent(repo_path, agent)

    assert analyzer.run() is False
