from unittest.mock import MagicMock, patch
from app_operator.adk.operator import AdkOperator
from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem


def test_adk_smoke_run(tmp_path):
    """Minimal smoke test verifying the whole chain runs without crashing."""
    fs = InMemoryFilesystem()
    repo_path = tmp_path
    fs.directories.add(str(repo_path))

    config = Config.from_dict(
        {
            "runtime": {"impl": "adk"},
            "agent": {"model": "gemini-pro", "provider": "gemini"},
            "operator": {"monitoring_max_iters": 1},
        }
    )

    with patch("app_operator.adk.operator.AdkAgentRunner") as MockRunner:
        runner_instance = MockRunner.return_value
        runner_instance.run_async.return_value = "Everything OK"

        with (
            patch("app_operator.adk.operator.build_adk_model"),
            patch("app_operator.adk.operator.build_tools"),
            patch("app_operator.adk.operator.build_adk_agent"),
            patch("app_operator.adk.operator.build_loop_agent") as mock_build_loop,
            patch("app_operator.adk.operator.asyncio.sleep"),
            patch("app_operator.adk.operator.run_health_check") as mock_health,
            patch(
                "app_operator.adk.operator.analyze_repository", return_value="context"
            ),
        ):
            mock_health.return_value = {
                "success": True,
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
            }

            # Setup loop agent mock with name
            mock_loop_agent = MagicMock()
            mock_loop_agent.name = "DeploymentLoop"
            mock_build_loop.return_value = mock_loop_agent

            operator = AdkOperator(
                str(repo_path), filesystem=fs, config=config, health_check_max_count=1
            )

            # Pre-create scripts to skip generation phase (which requires agent to
            # actually write files)
            fs.mkdir(repo_path / ".sds", parents=True, exist_ok=True)
            fs.write_text(repo_path / ".sds" / "deploy.sh", "echo deploy")
            fs.write_text(repo_path / ".sds" / "health_check.sh", "echo health")

            # Need to ensure deployment loop returns True (success)

            # The deployment loop logic checks for "DEPLOYMENT_FINISHED" or "Deployment Successful"
            # Our mock_run_async returns "Everything OK", which might fail the check.
            # Let's adjust mock return value for deployment loop

            async def side_effect_run_async(agent, prompt):
                if "DeploymentLoop" in str(agent.name):
                    return "Deployment Successful"
                return "Everything OK"

            runner_instance.run_async.side_effect = side_effect_run_async

            # Run
            exit_code = operator.run()

            assert exit_code == 0
            # Verify side effects
            assert fs.exists(repo_path / ".sds" / "code_analysis.md")
            assert fs.exists(repo_path / ".sds" / "deploy.sh")
            assert fs.exists(repo_path / ".sds" / "health_check.sh")
            # logs
            monitor_logs = repo_path / ".sds" / "logs" / "monitor"
            assert fs.exists(monitor_logs / "analysis_1.log")
