from unittest.mock import patch

from app_operator.adk.operator import AdkOperator
from app_operator.config import Config
from app_operator.filesystem import InMemoryFilesystem


def test_monitoring_cycle_runs_n_times(tmp_path):
    fs = InMemoryFilesystem()
    repo_path = tmp_path
    fs.directories.add(str(repo_path))

    config = Config.from_dict(
        {
            "runtime": {"impl": "adk"},
            "agent": {"model": "gemini-pro", "backend": "gemini"},
            "operator": {"monitoring_max_iters": 2, "interval": 1},
        }
    )

    with patch("app_operator.adk.operator.AdkAgentRunner") as MockRunner:
        runner_instance = MockRunner.return_value

        async def mock_run_async(*args, **kwargs):
            return "Analysis ok"

        runner_instance.run_async.side_effect = mock_run_async

        with (
            patch("app_operator.adk.operator.build_adk_model"),
            patch("app_operator.adk.operator.build_tools"),
            patch("app_operator.adk.operator.build_adk_agent"),
            patch("app_operator.adk.operator.build_loop_agent"),
            patch("app_operator.adk.operator.asyncio.sleep"),
            patch("app_operator.adk.operator.run_health_check") as mock_health,
        ):
            mock_health.return_value = {
                "success": True,
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
            }

            operator = AdkOperator(str(repo_path), filesystem=fs, config=config, health_check_max_count=2)

            # Setup sds dir
            fs.mkdir(repo_path / ".sds", parents=True, exist_ok=True)
            fs.write_text(repo_path / ".sds" / "deploy.sh", "echo deploy")
            fs.write_text(repo_path / ".sds" / "health_check.sh", "echo health")

            # Mock phases to isolate monitoring
            with (
                patch.object(operator, "_deploy_with_retries", return_value=True),
                patch.object(operator, "_run_analysis"),
                patch.object(operator, "_generate_scripts"),
            ):
                operator.run()

            # Verify logs
            monitor_logs = repo_path / ".sds" / "logs" / "monitor"
            assert fs.exists(monitor_logs / "analysis_1.log")
            assert fs.exists(monitor_logs / "analysis_2.log")

            # check_N.log is created by run_health_check, which is mocked here,
            # so those files won't exist.  analysis_N.log is written directly by
            # the operator via filesystem.write_text, so those SHOULD exist.

            # Verify max iters
            assert mock_health.call_count == 2
