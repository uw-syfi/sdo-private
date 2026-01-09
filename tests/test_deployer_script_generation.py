import pytest
from pathlib import Path
from unittest.mock import Mock
from app_operator.agents.deployer import generate_scripts, _analyze_repository


class StubAgent:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or ["#!/bin/bash\necho deploy", "#!/bin/bash\necho health"]
        self.call_count = 0

    def generate(self, prompt: str, cwd: str | None = None, timeout: int = 300) -> str:
        self.calls.append((prompt, cwd, timeout))

        # Simulate agent writing files
        if cwd:
            # We need to ensure .sds directory exists as the agent would create files there
            # But the agent might expect the directory to exist or create it.
            # In generate_scripts, .sds is created before calling agent.

            sds_dir = Path(cwd) / ".sds"
            # It should already exist because generate_scripts creates it.

            content = self.responses[self.call_count % len(self.responses)]

            # Determine which file to write based on prompt or call order
            # The prompt contains the filename instructions.
            filename = "deploy.sh"
            if "Create the file at: .sds/health_check.sh" in prompt:
                filename = "health_check.sh"
            elif "Create the file at: .sds/deploy.sh" in prompt:
                filename = "deploy.sh"

            (sds_dir / filename).write_text(content, encoding="utf-8")

        self.call_count += 1
        return "I have generated the scripts."


@pytest.fixture
def stub_agent():
    return StubAgent()


def test_analyze_repository_detects_docker(tmp_path):
    (tmp_path / "docker-compose.yml").touch()
    (tmp_path / "Dockerfile").touch()

    context = _analyze_repository(tmp_path)

    assert "Found docker-compose.yml" in context
    assert "Found Dockerfile" in context


def test_analyze_repository_detects_k8s_and_languages(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "package.json").touch()
    (tmp_path / "go.mod").touch()

    context = _analyze_repository(tmp_path)

    assert "Found Kubernetes manifests directory" in context
    assert "Found package.json" in context
    assert "Found go.mod" in context


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
    assert (repo / ".sds" / "deploy.sh").read_text(encoding="utf-8") == "#!/bin/bash\necho deploy"
    assert (
        repo /
        ".sds" /
        "health_check.sh").read_text(
        encoding="utf-8") == "#!/bin/bash\necho health"


def test_generate_scripts_sends_correct_prompts(tmp_path, stub_agent):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").touch()

    generate_scripts(str(repo), stub_agent)

    assert len(stub_agent.calls) == 2

    # Check first call (deploy.sh)
    prompt1, cwd1, _ = stub_agent.calls[0]
    assert "deploy.sh" in prompt1
    assert "Found docker-compose.yml" in prompt1
    assert cwd1 == str(repo)

    # Check second call (health_check.sh)
    prompt2, cwd2, _ = stub_agent.calls[1]
    assert "health_check.sh" in prompt2
    assert "Found docker-compose.yml" in prompt2
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
