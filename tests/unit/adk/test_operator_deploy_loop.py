from unittest.mock import MagicMock, patch

from app_operator.adk.operator import AdkOperator
from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem


def test_deploy_loop_success_first_try(tmp_path):
    fs = InMemoryFilesystem()
    repo_path = tmp_path

    # AdkOperator uses 'filesystem' for its operations (deploy scripts etc)
    # but TrajectoryRecorder uses real filesystem.
    # So we need to ensure InMemoryFilesystem knows about tmp_path if we want to check it there.
    # We add repo_path to InMemoryFilesystem directories so checks pass if any.
    fs.directories.add(str(repo_path))

    # Mock Config
    config = Config.from_dict(
        {
            "runtime": {"impl": "adk"},
            "agent": {"model": "gemini-pro", "backend": "gemini"},
            "deployment": {"platform": "docker"},
        }
    )

    # Mock AdkAgentRunner
    with patch("app_operator.adk.operator.AdkAgentRunner") as MockRunner:
        runner_instance = MockRunner.return_value

        # Mock run_async to simulate agent behavior
        async def mock_run_async(agent, prompt):
            prompt_str = str(prompt)

            # Check for CodeAnalyzer first
            if "CodeAnalyzer" in str(agent.name):
                return "Analysis done"

            if "comprehensive deploy.sh" in prompt_str:
                # AdkOperator uses 'ScriptGenerator' (no space)
                if "ScriptGenerator" not in str(agent.name):
                    raise AssertionError(f"Expected ScriptGenerator for deploy.sh, got {agent.name}")
                fs.write_text(repo_path / ".sds" / "deploy.sh", "echo 'starting'")
                return "Generated deploy.sh"

            if "comprehensive health_check.sh" in prompt_str:
                if "ScriptGenerator" not in str(agent.name):
                    raise AssertionError(f"Expected ScriptGenerator for health_check.sh, got {agent.name}")
                fs.write_text(repo_path / ".sds" / "health_check.sh", "echo 'healthy'")
                return "Generated health_check.sh"

            # For monitor
            if "HealthMonitor" in str(agent.name):
                return "Health OK"

            if "DeploymentLoop" in str(agent.name):
                return "Deployment Successful"

            return "Ok"

        runner_instance.run_async.side_effect = mock_run_async

        # Need to patch build_adk_model and tools as well since they might fail if
        # dependencies missing
        with (
            patch("app_operator.adk.operator.build_adk_model"),
            patch("app_operator.adk.operator.build_tools"),
            patch("app_operator.adk.operator.build_adk_agent") as mock_build_agent,
            patch("app_operator.adk.operator.build_loop_agent") as mock_build_loop_agent,
            patch("app_operator.adk.operator.asyncio.sleep"),  # Patch sleep to avoid waiting
        ):
            # Setup build_adk_agent to return a mock with name
            def side_effect_build_agent(name, instruction, model, tools):
                m = MagicMock()
                m.name = name
                return m

            mock_build_agent.side_effect = side_effect_build_agent

            # Setup build_loop_agent
            def side_effect_build_loop_agent(name, sub_agents, max_iterations, tools):
                m = MagicMock()
                m.name = name
                return m

            mock_build_loop_agent.side_effect = side_effect_build_loop_agent

            operator = AdkOperator(str(repo_path), filesystem=fs, config=config)

            # Mock _run_deploy_command
            with patch.object(operator, "_run_deploy_command") as mock_deploy:
                mock_deploy.return_value = {
                    "success": True,
                    "exit_code": 0,
                    "stdout": "started",
                    "stderr": "",
                }

                # Mock run_health_check
                with patch("app_operator.adk.operator.run_health_check") as mock_health:
                    mock_health.return_value = {
                        "success": True,
                        "exit_code": 0,
                        "stdout": "ok",
                        "stderr": "",
                    }

                    # Mock analyze_repository
                    with patch("app_operator.adk.operator.analyze_repository") as mock_analyze:
                        mock_analyze.return_value = "repo context"

                        # Run operator
                        exit_code = operator.run()

                        assert exit_code == 0
                        assert fs.exists(repo_path / ".sds" / "deploy.sh")
                        assert fs.exists(repo_path / ".sds" / "health_check.sh")


def test_deploy_loop_handles_failure(tmp_path):
    """Test that operator returns failure if LoopAgent fails."""
    fs = InMemoryFilesystem()
    repo_path = tmp_path
    fs.directories.add(str(repo_path))

    config = Config.from_dict(
        {
            "runtime": {"impl": "adk"},
            "agent": {"model": "gemini-pro", "backend": "gemini"},
        }
    )

    with patch("app_operator.adk.operator.AdkAgentRunner") as MockRunner:
        runner_instance = MockRunner.return_value

        async def mock_run_async(agent, prompt):
            if "DeploymentLoop" in str(agent.name):
                return "Deployment failed"
            return "Ok"

        runner_instance.run_async.side_effect = mock_run_async

        with (
            patch("app_operator.adk.operator.build_adk_model"),
            patch("app_operator.adk.operator.build_tools"),
            patch("app_operator.adk.operator.build_adk_agent"),
            patch("app_operator.adk.operator.build_loop_agent"),
            patch("app_operator.adk.operator.asyncio.sleep"),
            patch("app_operator.adk.operator.analyze_repository", return_value="context"),
        ):
            operator = AdkOperator(str(repo_path), filesystem=fs, config=config)

            # Ensure scripts exist so we skip generation (focus on deploy loop)
            # We need to manually write them or let generate run.
            # Let's let generate run, mock_run_async returns "Ok" which is interpreted?
            # Script generation checks if file exists. If mock returns "Ok", file won't be created by agent.
            # So we manually create them to skip generation errors.
            fs.mkdir(repo_path / ".sds", parents=True, exist_ok=True)
            fs.write_text(repo_path / ".sds" / "deploy.sh", "echo deploy")
            fs.write_text(repo_path / ".sds" / "health_check.sh", "echo health")

            exit_code = operator.run()

            assert exit_code == 1
