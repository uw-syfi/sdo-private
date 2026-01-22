import pytest
from unittest.mock import patch, MagicMock
from app_operator.__main__ import check_dependencies, REQUIRED_DEPENDENCIES


def test_check_dependencies_success():
    """Test that check_dependencies passes when all dependencies are present."""
    with patch("shutil.which") as mock_which:
        # Mock shutil.which to always return a path
        mock_which.return_value = "/usr/bin/some-tool"

        # Should not raise SystemExit
        try:
            check_dependencies()
        except SystemExit:
            pytest.fail("check_dependencies raised SystemExit unexpectedly!")


def test_check_dependencies_missing_tool(capsys):
    """Test that check_dependencies exits when a dependency is missing."""
    # We'll use one of the actual required dependencies to test failure
    tool_to_fail = REQUIRED_DEPENDENCIES[0]

    with patch("shutil.which") as mock_which:
        # custom side effect to fail for a specific tool
        def side_effect(cmd):
            if cmd == tool_to_fail:
                return None
            return "/usr/bin/some-tool"

        mock_which.side_effect = side_effect

        # Should raise SystemExit(1)
        with pytest.raises(SystemExit) as excinfo:
            check_dependencies()

        assert excinfo.value.code == 1

        # Verify output
        captured = capsys.readouterr()
        assert f"Error: Missing required system dependencies: {tool_to_fail}" in captured.out


def test_main_calls_check_dependencies():
    """Test that the main entry point calls check_dependencies."""
    with patch("app_operator.__main__.check_dependencies") as mock_check:
        with patch("argparse.ArgumentParser.parse_args") as mock_parse:
            from app_operator.__main__ import main

            # Mock parse_args to avoid actual argument parsing
            mock_parse.return_value = MagicMock(command="run")

            # We don't want to actually run the command
            with patch("app_operator.commands.run.run_command") as mock_run:
                mock_run.return_value = 0
                main()

    mock_check.assert_called_once()


def test_check_dependencies_checks_docker(capsys):
    """Test that check_dependencies specifically checks for docker."""
    with patch("shutil.which") as mock_which:
        # custom side effect to fail for docker
        def side_effect(cmd):
            if cmd == "docker":
                return None
            return "/usr/bin/some-tool"

        mock_which.side_effect = side_effect

        # Should raise SystemExit(1)
        with pytest.raises(SystemExit) as excinfo:
            check_dependencies()

        assert excinfo.value.code == 1

        # Verify output
        captured = capsys.readouterr()
        assert "Error: Missing required system dependencies: docker" in captured.out


def test_check_dependencies_checks_kubectl(capsys):
    """Test that check_dependencies specifically checks for kubectl."""
    with patch("shutil.which") as mock_which:
        # custom side effect to fail for kubectl
        def side_effect(cmd):
            if cmd == "kubectl":
                return None
            return "/usr/bin/some-tool"

        mock_which.side_effect = side_effect

        # Should raise SystemExit(1)
        with pytest.raises(SystemExit) as excinfo:
            check_dependencies()

        assert excinfo.value.code == 1

        # Verify output
        captured = capsys.readouterr()
        assert "Error: Missing required system dependencies: kubectl" in captured.out
