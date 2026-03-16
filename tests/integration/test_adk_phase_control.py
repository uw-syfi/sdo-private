"""Integration tests for ADK phase control."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem


@pytest.mark.skip(reason="Requires Google GenAI API credentials")
class TestADKPhaseControl:
    """Test phase control in ADK runtime."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            # Create minimal docker-compose.yml
            compose_file = repo_path / "docker-compose.yml"
            compose_file.write_text("services:\n  web:\n    image: nginx\n")
            yield repo_path

    @pytest.mark.anyio
    async def test_run_analysis_called_by_default(self, temp_repo):
        """Test that _run_analysis is called by default."""
        config = Config.from_dict({"agent": {"backend": "gemini", "model": "gemini-1.5-pro"}})
        assert config.operator.phase.code_analysis is True

        from app_operator.adk.operator import AdkOperator

        filesystem = InMemoryFilesystem()

        operator = AdkOperator(repo_path=temp_repo, config=config, filesystem=filesystem)

        # Mock the internal methods
        operator._run_analysis = AsyncMock()
        operator._generate_scripts = AsyncMock()
        operator._deploy_with_retries = AsyncMock(return_value=True)
        operator._run_monitoring = AsyncMock()

        await operator.run_async()

        # Verify _run_analysis was called
        operator._run_analysis.assert_called_once()

    @pytest.mark.anyio
    async def test_run_analysis_skipped_when_disabled(self, temp_repo):
        """Test that _run_analysis is skipped when disabled."""
        config = Config.from_dict(
            {
                "agent": {"backend": "gemini", "model": "gemini-1.5-pro"},
                "operator": {"phase": {"code_analysis": False}},
            }
        )
        assert config.operator.phase.code_analysis is False

        from app_operator.adk.operator import AdkOperator

        filesystem = InMemoryFilesystem()

        operator = AdkOperator(repo_path=temp_repo, config=config, filesystem=filesystem)

        # Mock the internal methods
        operator._run_analysis = AsyncMock()
        operator._generate_scripts = AsyncMock()
        operator._deploy_with_retries = AsyncMock(return_value=True)
        operator._run_monitoring = AsyncMock()

        await operator.run_async()

        # Verify _run_analysis was NOT called
        operator._run_analysis.assert_not_called()

    @pytest.mark.anyio
    async def test_async_behavior_correct(self, temp_repo):
        """Test that async execution flow is correct."""
        config = Config.from_dict(
            {
                "agent": {"backend": "gemini", "model": "gemini-1.5-pro"},
                "operator": {"phase": {"code_analysis": False}},
            }
        )

        from app_operator.adk.operator import AdkOperator

        filesystem = InMemoryFilesystem()

        operator = AdkOperator(repo_path=temp_repo, config=config, filesystem=filesystem)

        # Track call order
        call_order = []

        async def track_analysis():
            call_order.append("analysis")

        async def track_scripts():
            call_order.append("scripts")

        async def track_deploy():
            call_order.append("deploy")
            return True

        async def track_monitor():
            call_order.append("monitor")

        operator._run_analysis = track_analysis
        operator._generate_scripts = track_scripts
        operator._deploy_with_retries = track_deploy
        operator._run_monitoring = track_monitor

        await operator.run_async()

        # When analysis is disabled, it should not appear in call order
        assert "analysis" not in call_order
        assert call_order == ["scripts", "deploy", "monitor"]

    @pytest.mark.anyio
    async def test_script_generation_proceeds_without_analysis(self, temp_repo):
        """Test that script generation proceeds when analysis is skipped."""
        config = Config.from_dict(
            {
                "agent": {"backend": "gemini", "model": "gemini-1.5-pro"},
                "operator": {"phase": {"code_analysis": False}},
            }
        )

        from app_operator.adk.operator import AdkOperator

        filesystem = InMemoryFilesystem()

        operator = AdkOperator(repo_path=temp_repo, config=config, filesystem=filesystem)

        # Mock methods
        operator._run_analysis = AsyncMock()
        operator._generate_scripts = AsyncMock()
        operator._deploy_with_retries = AsyncMock(return_value=True)
        operator._run_monitoring = AsyncMock()

        result = await operator.run_async()

        # Verify workflow proceeded
        operator._run_analysis.assert_not_called()
        operator._generate_scripts.assert_called_once()
        operator._deploy_with_retries.assert_called_once()
        assert result == 0

    @pytest.mark.anyio
    async def test_analysis_enabled_explicitly(self, temp_repo):
        """Test that explicitly enabling analysis works."""
        config = Config.from_dict(
            {"agent": {"backend": "gemini", "model": "gemini-1.5-pro"}, "operator": {"phase": {"code_analysis": True}}}
        )
        assert config.operator.phase.code_analysis is True

        from app_operator.adk.operator import AdkOperator

        filesystem = InMemoryFilesystem()

        operator = AdkOperator(repo_path=temp_repo, config=config, filesystem=filesystem)

        # Mock methods
        operator._run_analysis = AsyncMock()
        operator._generate_scripts = AsyncMock()
        operator._deploy_with_retries = AsyncMock(return_value=True)
        operator._run_monitoring = AsyncMock()

        await operator.run_async()

        # Verify _run_analysis was called
        operator._run_analysis.assert_called_once()

    @pytest.mark.anyio
    async def test_deployment_succeeds_without_analysis(self, temp_repo):
        """Test that deployment can succeed without analysis."""
        config = Config.from_dict(
            {
                "agent": {"backend": "gemini", "model": "gemini-1.5-pro"},
                "operator": {"phase": {"code_analysis": False}},
            }
        )

        from app_operator.adk.operator import AdkOperator

        filesystem = InMemoryFilesystem()

        operator = AdkOperator(repo_path=temp_repo, config=config, filesystem=filesystem)

        # Mock methods to simulate successful deployment
        operator._run_analysis = AsyncMock()
        operator._generate_scripts = AsyncMock()
        operator._deploy_with_retries = AsyncMock(return_value=True)
        operator._run_monitoring = AsyncMock()

        result = await operator.run_async()

        # Verify successful completion
        assert result == 0
        operator._deploy_with_retries.assert_called_once()
        operator._run_monitoring.assert_called_once()
