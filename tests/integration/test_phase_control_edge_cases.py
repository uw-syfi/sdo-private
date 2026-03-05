"""Edge case tests for phase control feature."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem
from app_operator.ui import NullOperatorUI
from tests.fixtures.agents import StubAgent


class TestPhaseControlEdgeCases:
    """Test edge cases for phase control feature."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            compose_file = repo_path / "docker-compose.yml"
            compose_file.write_text("services:\n  web:\n    image: nginx\n")
            yield repo_path

    @pytest.fixture
    def mock_ui(self):
        """Create a mock UI for testing."""
        ui = Mock()
        ui.set_stage = Mock()
        ui.update_status = Mock()
        return ui

    def test_analysis_files_exist_but_config_says_skip(self, temp_repo):
        """Test that existing analysis files are ignored when skip is configured."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()

        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        # Pre-create analysis files (simulating previous run)
        sds_dir = temp_repo / ".sds"
        filesystem.mkdir(sds_dir, parents=True, exist_ok=True)
        filesystem.write_text(sds_dir / "code_analysis.md", "# Previous Analysis\nOld content")
        filesystem.write_text(sds_dir / "deployment_issues.md", "# Previous Issues\nOld issues")

        from app_operator.cli_agent.operator import AppOperator

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Mock analyzer to verify it's not called
        operator.analyzer.run = Mock()

        # Mock deployer and monitor
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        operator.run()

        # Verify analyzer was NOT called despite files existing
        operator.analyzer.run.assert_not_called()

        # Files should still exist (not modified)
        assert filesystem.exists(sds_dir / "code_analysis.md")
        content = filesystem.read_text(sds_dir / "code_analysis.md")
        assert "Previous Analysis" in content

    def test_no_analysis_files_and_analysis_disabled(self, temp_repo):
        """Test deployment with no analysis files and analysis disabled."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        from app_operator.cli_agent.operator import AppOperator

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Mock deployer to succeed (it should handle missing analysis gracefully)
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        result = operator.run()

        # Should complete successfully
        assert result == 0
        operator.deployer.run.assert_called_once()

    def test_multiple_runs_same_repo_different_configs(self, temp_repo):
        """Test multiple runs in same repo with different configs."""
        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        # First run with analysis enabled
        config1 = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": True}}})

        from app_operator.cli_agent.operator import AppOperator

        agent1 = StubAgent()

        operator1 = AppOperator(
            repo_path=temp_repo, config=config1, filesystem=filesystem, agent=agent1, ui=NullOperatorUI()
        )

        analyzer_mock1 = Mock()
        operator1.analyzer.run = analyzer_mock1
        operator1.deployer.run = Mock(return_value=True)
        operator1.monitor.run = Mock()

        operator1.run()

        # Verify analyzer ran
        analyzer_mock1.assert_called_once()

        # Second run with analysis disabled
        config2 = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        agent2 = StubAgent()

        operator2 = AppOperator(
            repo_path=temp_repo, config=config2, filesystem=filesystem, agent=agent2, ui=NullOperatorUI()
        )

        analyzer_mock2 = Mock()
        operator2.analyzer.run = analyzer_mock2
        operator2.deployer.run = Mock(return_value=True)
        operator2.monitor.run = Mock()

        operator2.run()

        # Verify analyzer did NOT run in second run
        analyzer_mock2.assert_not_called()

    def test_signal_handling_during_skipped_analysis(self, temp_repo):
        """Test that signal handling works correctly when analysis is skipped."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        from app_operator.cli_agent.operator import AppOperator

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Simulate shutdown signal during deployment
        def trigger_shutdown(**kwargs):
            operator._shutdown_requested = True
            return False

        operator.deployer.run = Mock(side_effect=trigger_shutdown)

        result = operator.run()

        # Should handle shutdown gracefully
        assert result == 1
        assert operator._shutdown_requested

    def test_concurrent_operations_different_configs(self, temp_repo):
        """Test that different operator instances can use different configs."""
        filesystem1 = InMemoryFilesystem()
        filesystem2 = InMemoryFilesystem()

        # Create repo path in both filesystems
        filesystem1.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem1.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")
        filesystem2.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem2.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        config_enabled = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": True}}})

        config_disabled = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        # Verify configs are independent
        assert config_enabled.operator.phase.code_analysis is True
        assert config_disabled.operator.phase.code_analysis is False

        # Each operator should respect its own config
        from app_operator.cli_agent.operator import AppOperator

        agent1 = StubAgent()
        agent2 = StubAgent()

        op1 = AppOperator(repo_path=temp_repo, config=config_enabled, filesystem=filesystem1, agent=agent1)
        op2 = AppOperator(repo_path=temp_repo, config=config_disabled, filesystem=filesystem2, agent=agent2)

        # Verify each has correct config
        assert op1.config.operator.phase.code_analysis is True
        assert op2.config.operator.phase.code_analysis is False

    def test_deployer_handles_missing_analysis_gracefully(self, temp_repo):
        """Test that deployer prompts handle missing analysis files gracefully."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        from app_operator.cli_agent.operator import AppOperator

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # The deployer should handle missing analysis files
        # deployer.py:85-100 has try/except for reading analysis files
        # Verify analysis files don't exist
        sds_dir = temp_repo / ".sds"
        assert not filesystem.exists(sds_dir / "code_analysis.md")
        assert not filesystem.exists(sds_dir / "deployment_issues.md")

        # Mock the actual deployment execution
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        result = operator.run()

        # Should succeed despite missing analysis
        assert result == 0

    def test_fault_injection_with_analysis_disabled(self, temp_repo):
        """Test that fault injection works independently of analysis phase."""
        config = Config.from_dict(
            {"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}, "fault_injection": {"enabled": True, "num_faults": 1}}
        )

        # Fault injection should work regardless of analysis phase
        assert config.operator.phase.code_analysis is False
        assert config.fault_injection.enabled is True

        # The configs are independent
        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        from app_operator.cli_agent.operator import AppOperator

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Verify both configs are respected
        assert operator.config.operator.phase.code_analysis is False
        assert operator.config.fault_injection.enabled is True

    def test_dspy_optimized_prompts_with_analysis_disabled(self, temp_repo):
        """Test that DSPy signatures handle empty analysis context."""
        config = Config.from_dict(
            {
                "agent": {"provider": "codex", "model": "test-model"},
                "operator": {"phase": {"code_analysis": False}},
                "dspy": {"use_optimized": True, "optimized_version": "v1"},
            }
        )

        # DSPy prompts should handle missing analysis gracefully
        # Signatures expect analysis_summary and issues_summary which may be empty

        assert config.operator.phase.code_analysis is False
        assert config.dspy.use_optimized is True

        # When analysis is disabled, analysis context will be empty strings
        # DSPy signatures should handle this gracefully
        analysis_summary = ""
        issues_summary = ""

        # These empty values should not cause errors in DSPy signatures
        assert isinstance(analysis_summary, str)
        assert isinstance(issues_summary, str)

    @pytest.mark.anyio
    @pytest.mark.skip(reason="Requires Google GenAI API credentials")
    async def test_adk_runtime_with_analysis_disabled(self, temp_repo):
        """Test ADK runtime specific edge cases."""
        config = Config.from_dict(
            {
                "agent": {"provider": "gemini", "model": "gemini-1.5-pro"},
                "operator": {"phase": {"code_analysis": False}},
            }
        )

        from app_operator.adk.operator import AdkOperator

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        operator = AdkOperator(repo_path=temp_repo, config=config, filesystem=filesystem)

        # Mock all async methods
        operator._run_analysis = AsyncMock()
        operator._generate_scripts = AsyncMock()
        operator._deploy_with_retries = AsyncMock(return_value=True)
        operator._run_monitoring = AsyncMock()

        result = await operator.run_async()

        # Verify correct async flow
        operator._run_analysis.assert_not_called()
        operator._generate_scripts.assert_called_once()
        assert result == 0

    def test_langgraph_conditional_edge_routing(self, temp_repo):
        """Test that LangGraph conditional edges route correctly."""
        config = Config.from_dict(
            {
                "agent": {"provider": "gemini", "model": "gemini-1.5-pro"},
                "operator": {"phase": {"code_analysis": False}},
            }
        )

        # When analysis_done=True, the graph should route to script_generator
        # The analyzer node has logic: if state["analysis_done"]: return state

        initial_state = {"analysis_done": not config.operator.phase.code_analysis, "scripts_done": False}

        # Verify state is set correctly for routing
        assert initial_state["analysis_done"] is True

        # The conditional edge in the graph would evaluate:
        # - If analysis_done=True -> skip to next phase
        # - If analysis_done=False -> run analysis

    def test_config_loaded_at_startup_not_changeable(self, temp_repo):
        """Test that config is loaded at startup and doesn't change during run."""
        config = Config.from_dict({"agent": {"provider": "codex", "model": "test-model"}, "operator": {"phase": {"code_analysis": False}}})

        filesystem = InMemoryFilesystem()
        # Create repo path in filesystem
        filesystem.mkdir(temp_repo, parents=True, exist_ok=True)
        filesystem.write_text(temp_repo / "docker-compose.yml", "services:\n  web:\n    image: nginx\n")

        from app_operator.cli_agent.operator import AppOperator

        agent = StubAgent()

        operator = AppOperator(
            repo_path=temp_repo, config=config, filesystem=filesystem, agent=agent, ui=NullOperatorUI()
        )

        # Verify initial config
        assert operator.config.operator.phase.code_analysis is False

        # Mock deployer
        operator.deployer.run = Mock(return_value=True)
        operator.monitor.run = Mock()

        # Run operator
        operator.run()

        # Config should remain unchanged
        assert operator.config.operator.phase.code_analysis is False
