"""Integration tests for CLI Agent phase control."""

import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from app_operator.cli_agent.operator import AppOperator
from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.ui import NullOperatorUI
from tests.fixtures.agents import StubAgent


class TestCLIOperatorPhaseControl:
    """Test phase control in CLI Agent runtime."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            # Create minimal docker-compose.yml
            compose_file = repo_path / "docker-compose.yml"
            compose_file.write_text("services:\n  web:\n    image: nginx\n")
            yield repo_path

    def test_analysis_enabled_by_default(self, temp_repo):
        """Test that code analysis runs by default."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}})
        assert config.operator.phase.code_analysis is True

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Mock the analyzer to track if it runs
        operator.analyzer.run = Mock()

        # Mock deployer and monitor to prevent actual execution
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        operator.run()

        # Verify analyzer was called
        operator.analyzer.run.assert_called_once()

    def test_analysis_skipped_when_disabled(self, temp_repo):
        """Test that code analysis is skipped when disabled."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})
        assert config.operator.phase.code_analysis is False

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Mock the analyzer to track if it runs
        operator.analyzer.run = Mock()

        # Mock deployer and monitor to prevent actual execution
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        operator.run()

        # Verify analyzer was NOT called
        operator.analyzer.run.assert_not_called()

    def test_deployment_succeeds_without_analysis(self, temp_repo):
        """Test that deployment can succeed without analysis files."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Mock deployer to succeed
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        result = operator.run()

        # Verify deployment was attempted and succeeded
        operator.deployer.run.assert_called_once()
        assert result == 0  # Success

    def test_analysis_files_not_created_when_disabled(self, temp_repo):
        """Test that analysis files are not created when disabled."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Mock deployer and monitor
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        operator.run()

        # Verify analysis files don't exist
        sds_dir = temp_repo / ".sds"
        analysis_file = sds_dir / "code_analysis.md"
        issues_file = sds_dir / "deployment_issues.md"

        assert not filesystem.exists(analysis_file)
        assert not filesystem.exists(issues_file)

    def test_ui_stages_reflect_skip(self, temp_repo):
        """Test that UI stages are set correctly when analysis is skipped."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        agent = StubAgent()

        # Use a mock UI to track stage calls
        mock_ui = Mock(spec=NullOperatorUI)
        mock_ui.set_stage = Mock()
        mock_ui.update_status = Mock()

        operator = AppOperator(repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=mock_ui)

        # Mock deployer and monitor
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        operator.run()

        # Verify "Code Analysis" stage was NOT set
        stage_calls = [call[0][0] for call in mock_ui.set_stage.call_args_list]
        assert "Code Analysis" not in stage_calls
        assert "Deployment" in stage_calls

    def test_analysis_enabled_explicitly(self, temp_repo):
        """Test that explicitly enabling analysis works."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": True}}})
        assert config.operator.phase.code_analysis is True

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Mock the analyzer
        operator.analyzer.run = Mock()

        # Mock deployer and monitor
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        operator.run()

        # Verify analyzer was called
        operator.analyzer.run.assert_called_once()
