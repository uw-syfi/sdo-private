"""Tests for fix loop detection in the deployment agent."""

import pytest

from app_operator.cli_agent.agents.deployer import DeploymentAgent
from app_operator.prompts.deployer import create_fix_prompt
from tests.fixtures.agents import TrackingAgent


@pytest.fixture
def repo_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sds_dir = repo / ".sds"
    sds_dir.mkdir()
    (sds_dir / "deploy.sh").write_text("#!/bin/bash\n")
    (sds_dir / "health_check.sh").write_text("#!/bin/bash\n")
    (sds_dir / "logs").mkdir()
    return repo


@pytest.fixture
def agent(repo_path):
    return DeploymentAgent(repo_path, TrackingAgent(response="ok"))


class TestDetectFixLoop:
    """Tests for DeploymentAgent._detect_fix_loop."""

    def test_returns_none_when_too_few_attempts(self, agent):
        """No loop can exist with fewer than 3 attempts."""
        assert agent._detect_fix_loop(1) is None
        assert agent._detect_fix_loop(2) is None

    def test_returns_none_when_no_summaries_exist(self, agent):
        """No loop when there are no summary files on disk."""
        assert agent._detect_fix_loop(5) is None

    def test_returns_none_when_summaries_differ(self, agent, repo_path):
        """Distinct summaries should not trigger a loop warning."""
        logs = repo_path / ".sds" / "logs"
        (logs / "fix_summary_1.log").write_text("Fixed port mismatch in docker-compose.yml")
        (logs / "fix_summary_2.log").write_text("Added ca-certificates to Dockerfile")
        (logs / "fix_summary_3.log").write_text("Changed gRPC keepalive timeout to 30s")
        assert agent._detect_fix_loop(4) is None

    def test_detects_loop_with_identical_summaries(self, agent, repo_path):
        """Identical consecutive summaries should trigger loop detection."""
        logs = repo_path / ".sds" / "logs"
        same_text = "Changed consul command to ['consul', 'agent', '-dev', '-client', '0.0.0.0']"
        (logs / "fix_summary_1.log").write_text(same_text)
        (logs / "fix_summary_2.log").write_text(same_text)
        (logs / "fix_summary_3.log").write_text(same_text)

        warning = agent._detect_fix_loop(4)
        assert warning is not None
        assert "Fix Loop Detected" in warning
        assert "3" in warning  # 3 consecutive similar fixes

    def test_detects_loop_with_similar_summaries(self, agent, repo_path):
        """Summaries that are similar but not identical should also trigger."""
        logs = repo_path / ".sds" / "logs"
        (logs / "fix_summary_1.log").write_text(
            "Changed consul command to ['consul', 'agent', '-dev']. Updated docker-compose.yml."
        )
        (logs / "fix_summary_2.log").write_text(
            "Changed consul command to ['consul', 'agent', '-dev']. Modified docker-compose.yml service."
        )
        (logs / "fix_summary_3.log").write_text(
            "Changed consul command to ['consul', 'agent', '-dev']. Updated docker-compose.yml file."
        )

        warning = agent._detect_fix_loop(4)
        assert warning is not None
        assert "Fix Loop Detected" in warning

    def test_detects_loop_when_only_two_recent_summaries_are_similar(self, agent, repo_path):
        """Two similar recent summaries are enough to trigger the lower threshold."""
        logs = repo_path / ".sds" / "logs"
        same_text = "Fixed consul startup"
        (logs / "fix_summary_1.log").write_text("Unrelated earlier fix")
        (logs / "fix_summary_2.log").write_text(same_text)
        (logs / "fix_summary_3.log").write_text(same_text)

        warning = agent._detect_fix_loop(4)
        assert warning is not None
        assert "The last 2 attempts" in warning

    def test_loop_resets_when_broken_by_different_fix(self, agent, repo_path):
        """A different fix in the middle should break the loop."""
        logs = repo_path / ".sds" / "logs"
        same_text = "Fixed consul startup command"
        (logs / "fix_summary_1.log").write_text(same_text)
        (logs / "fix_summary_2.log").write_text("Completely different: rewrote Dockerfile")
        (logs / "fix_summary_3.log").write_text(same_text)

        assert agent._detect_fix_loop(4) is None

    def test_warning_includes_actionable_advice(self, agent, repo_path):
        """The warning should contain concrete guidance for breaking out."""
        logs = repo_path / ".sds" / "logs"
        same = "Applied same fix again"
        for i in range(1, 4):
            (logs / f"fix_summary_{i}.log").write_text(same)

        warning = agent._detect_fix_loop(4)
        assert "fundamentally different approach" in warning
        assert "container logs" in warning


class TestCreateFixPromptWithLoopWarning:
    """Tests for loop_warning parameter in create_fix_prompt."""

    def test_loop_warning_included_in_prompt(self, tmp_path):
        """When loop_warning is provided, it should appear in the rendered prompt."""
        repo = tmp_path / "repo"
        repo.mkdir()
        sds_dir = repo / ".sds"
        sds_dir.mkdir()
        (sds_dir / "logs").mkdir()
        (sds_dir / "logs" / "fix_summary_1.log").write_text("prev fix")

        prompt = create_fix_prompt(
            repo_path=repo,
            attempt=2,
            max_attempts=10,
            error_context="## Error\nexit code 1",
            deploy_script_path=repo / ".sds" / "deploy.sh",
            health_check_script_path=repo / ".sds" / "health_check.sh",
            loop_warning="\n\n## CRITICAL: Fix Loop Detected\nYou are stuck.",
        )
        assert "Fix Loop Detected" in prompt
        assert "You are stuck" in prompt

    def test_no_loop_warning_by_default(self, tmp_path):
        """When loop_warning is None, prompt should not contain loop warning."""
        repo = tmp_path / "repo"
        repo.mkdir()
        sds_dir = repo / ".sds"
        sds_dir.mkdir()

        prompt = create_fix_prompt(
            repo_path=repo,
            attempt=1,
            max_attempts=10,
            error_context="## Error\nexit code 1",
            deploy_script_path=repo / ".sds" / "deploy.sh",
            health_check_script_path=repo / ".sds" / "health_check.sh",
        )
        assert "Fix Loop Detected" not in prompt
