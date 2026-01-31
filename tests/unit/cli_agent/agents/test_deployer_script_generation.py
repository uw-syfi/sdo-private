import pytest
from unittest.mock import Mock
from app_operator.cli_agent.agents.deployer import generate_scripts
from app_operator.prompts.deployment_context import analyze_repository
from tests.fixtures.agents import ScriptGeneratingAgent


@pytest.fixture
def stub_agent():
    """Create a script-generating agent for testing."""
    return ScriptGeneratingAgent(responses=[
        "#!/bin/bash\necho deploy",
        "#!/bin/bash\necho health",
    ])


def test_analyze_repository_detects_languages(tmp_path):
    (tmp_path / "package.json").touch()
    (tmp_path / "go.mod").touch()

    context = analyze_repository(tmp_path)
    assert isinstance(context, str)
    assert "Repository: " in context


def test_generate_scripts_creates_files(tmp_path, stub_agent):
    # Setup repo
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").touch()

    success, message = generate_scripts(str(repo), stub_agent)

    assert success is True
    assert (repo / ".sds" / "deploy.sh").exists()
    assert (repo / ".sds" / "health_check.sh").exists()

    # Verify content
    assert (repo / ".sds" / "deploy.sh").read_text(
        encoding="utf-8"
    ) == "#!/bin/bash\necho deploy"
    assert (repo / ".sds" / "health_check.sh").read_text(
        encoding="utf-8"
    ) == "#!/bin/bash\necho health"


def test_generate_scripts_sends_correct_prompts(tmp_path, stub_agent):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").touch()

    generate_scripts(str(repo), stub_agent)

    assert len(stub_agent.calls) == 2

    # Check first call (deploy.sh)
    prompt1, cwd1, _ = stub_agent.calls[0]
    assert "deploy.sh" in prompt1
    assert cwd1 == str(repo)

    # Check second call (health_check.sh)
    prompt2, cwd2, _ = stub_agent.calls[1]
    assert "health_check.sh" in prompt2
    assert cwd2 == str(repo)


def test_generate_scripts_handles_agent_errors(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    error_agent = Mock()
    error_agent.generate.side_effect = RuntimeError("API Error")

    success, message = generate_scripts(str(repo), error_agent)

    assert success is False
    assert "API Error" in message
    assert not (repo / ".sds" / "deploy.sh").exists()
