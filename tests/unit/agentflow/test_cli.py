import sys
from unittest.mock import MagicMock, patch
from agentflow.cli import main
from agentflow.models import AgentflowResult


def test_cli_no_run(tmp_path):
    with patch("agentflow.cli.load_config") as mock_load_config, \
            patch("agentflow.cli.AgentflowEngine") as mock_engine_cls, \
            patch("agentflow.cli.ConsoleIO"), \
            patch("subprocess.run") as mock_subprocess_run:

        # Setup mocks
        mock_config = MagicMock()
        mock_config.operator.agent_timeout = 300
        mock_load_config.return_value = mock_config

        mock_engine_instance = mock_engine_cls.return_value
        result = AgentflowResult(
            script_path=tmp_path / "generated_script.py",
            script_text="print('hello')",
            clarifications=[]
        )

        # Helper to make async return
        async def async_return(*args, **kwargs):
            return result

        mock_engine_instance.run_async.side_effect = async_return

        # Mock sys.executable for subprocess.run call
        with patch("sys.executable", "python3"):
            # Test with --no-run
            with patch.object(sys, 'argv', ["agentflow", "--prompt", "test", "--no-run", "--no-tui"]):
                exit_code = main()

            assert exit_code == 0
            # subprocess.run should NOT be called
            mock_subprocess_run.assert_not_called()

            # Reset mocks
            mock_subprocess_run.reset_mock()

            # Test WITHOUT --no-run
            with patch.object(sys, 'argv', ["agentflow", "--prompt", "test", "--no-tui"]):
                exit_code = main()

            assert exit_code == 0
            # subprocess.run SHOULD be called
            mock_subprocess_run.assert_called()
