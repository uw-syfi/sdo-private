import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app_operator.commands import run_exp


@pytest.fixture
def mock_exp_structure(tmp_path):
    # Create config structure
    # Config must be in exp_config/something/config.toml
    root = tmp_path / "root"
    root.mkdir()

    config_dir = root / "exp_config" / "test_exp"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.toml"

    # Create dummy app
    app_dir = root / "apps" / "dummy_app"
    app_dir.mkdir(parents=True)
    (app_dir / "README.md").write_text("dummy")

    config_content = f'apps = ["{str(app_dir)}"]'
    config_file.write_text(config_content)

    return root, config_file, app_dir


def test_run_exp_orchestration(mock_exp_structure):
    root, config_file, app_dir = mock_exp_structure

    # Mock subprocess.run
    with patch("app_operator.commands.run_exp.subprocess.run") as mock_run:
        # Configure mock to return success
        mock_run.return_value = MagicMock(returncode=0)

        # Mock sys.executable
        with patch("app_operator.commands.run_exp.sys.executable", "python"):
            # Mock Path.cwd to return our temp root
            with patch("app_operator.commands.run_exp.Path.cwd", return_value=root):
                # Mock shutil.copytree and rmtree since we don't want real file ops for init-exp here
                # actually run_exp calls init-exp via subprocess, so we don't need to mock shutil inside init-exp
                # BUT run_exp does some shutil.rmtree on exp_dir

                args = MagicMock()
                args.experiment = str(config_file)
                args.parallel = 1

                # Run command
                ret = run_exp.run_command(args)

                assert ret == 0

                # Verify subprocess calls
                # Expect 2 calls: init-exp and run
                assert mock_run.call_count == 2

                # Check call arguments
                calls = mock_run.call_args_list

                # Init call
                init_args = calls[0][0][0]
                assert "init-exp" in init_args
                assert str(app_dir) in init_args
                assert "test_exp" in init_args

                # Run call
                run_args = calls[1][0][0]
                assert "run" in run_args
                expected_exp_dir = root / "exp" / "dummy_app" / "test_exp"
                assert str(expected_exp_dir) in run_args


def test_run_exp_parallelism(mock_exp_structure):
    root, config_file, app_dir = mock_exp_structure

    # Add a second app
    app2_dir = root / "apps" / "dummy_app2"
    app2_dir.mkdir(parents=True)

    # Update config
    config_file.write_text(f'apps = ["{str(app_dir)}", "{str(app2_dir)}"]')

    with patch("app_operator.commands.run_exp.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with patch("app_operator.commands.run_exp.sys.executable", "python"):
            with patch("app_operator.commands.run_exp.Path.cwd", return_value=root):

                args = MagicMock()
                args.experiment = str(config_file)
                args.parallel = 2

                ret = run_exp.run_command(args)

                assert ret == 0
                assert mock_run.call_count == 4  # 2 inits + 2 runs
