"""Tests for viz_graph command."""
import argparse
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, mock_open

from app_operator.commands.viz_graph import add_arguments, run_command


class TestAddArguments:
    """Test add_arguments function."""

    def test_add_arguments(self):
        """Test that arguments are added to parser."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)

        # Parse with the argument
        args = parser.parse_args(["--output", "graph.png"])
        assert args.output == "graph.png"

    def test_add_arguments_short_flag(self):
        """Test short flag -o."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)

        args = parser.parse_args(["-o", "output.mermaid"])
        assert args.output == "output.mermaid"

    def test_add_arguments_default_none(self):
        """Test that output defaults to None when not provided."""
        parser = argparse.ArgumentParser()
        add_arguments(parser)

        args = parser.parse_args([])
        assert args.output is None


class TestRunCommand:
    """Test run_command function."""

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.print")
    def test_run_command_stdout(self, mock_print, mock_config, mock_build_graph):
        """Test run_command outputs to stdout when no output file specified."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph TD\nA-->B"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output=None)
        exit_code = run_command(args)

        assert exit_code == 0
        mock_print.assert_called_once_with("graph TD\nA-->B")
        mock_build_graph.assert_called_once()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_png_output(self, mock_file, mock_config, mock_build_graph):
        """Test run_command generates PNG file."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid_png.return_value = b"PNG binary data"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output="graph.png")
        exit_code = run_command(args)

        assert exit_code == 0
        mock_file.assert_called_once_with(Path("graph.png"), "wb")
        mock_file().write.assert_called_once_with(b"PNG binary data")
        mock_compiled.draw_mermaid_png.assert_called_once()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_mermaid_output(self, mock_file, mock_config, mock_build_graph):
        """Test run_command generates .mermaid file."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph TD\nA-->B"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output="graph.mermaid")
        exit_code = run_command(args)

        assert exit_code == 0
        mock_file.assert_called_once_with(Path("graph.mermaid"), "w", encoding="utf-8")
        mock_file().write.assert_called_once_with("graph TD\nA-->B")
        mock_compiled.draw_mermaid.assert_called_once()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_txt_output(self, mock_file, mock_config, mock_build_graph):
        """Test run_command with .txt extension (should use mermaid format)."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph TD\nA-->B"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output="graph.txt")
        exit_code = run_command(args)

        assert exit_code == 0
        mock_file.assert_called_once_with(Path("graph.txt"), "w", encoding="utf-8")
        mock_compiled.draw_mermaid.assert_called_once()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_no_extension(self, mock_file, mock_config, mock_build_graph):
        """Test run_command with no extension (should use mermaid format)."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph TD\nA-->B"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output="graph_output")
        exit_code = run_command(args)

        assert exit_code == 0
        mock_file.assert_called_once_with(Path("graph_output"), "w", encoding="utf-8")
        mock_compiled.draw_mermaid.assert_called_once()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    def test_run_command_build_graph_exception(self, mock_config, mock_build_graph):
        """Test run_command handles exception from build_graph."""
        mock_build_graph.side_effect = Exception("Graph build failed")

        args = argparse.Namespace(output=None)
        exit_code = run_command(args)

        assert exit_code == 1

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_file_write_exception(self, mock_file, mock_config, mock_build_graph):
        """Test run_command handles file write exception."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph TD\nA-->B"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        # Make file write raise an exception
        mock_file.side_effect = IOError("Permission denied")

        args = argparse.Namespace(output="graph.mermaid")
        exit_code = run_command(args)

        assert exit_code == 1

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_png_draw_exception(self, mock_file, mock_config, mock_build_graph):
        """Test run_command handles exception from draw_mermaid_png."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid_png.side_effect = Exception("PNG generation failed")
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output="graph.png")
        exit_code = run_command(args)

        assert exit_code == 1
        # File should not be written if PNG generation fails
        mock_file.assert_not_called()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    def test_run_command_uses_default_config(self, mock_config, mock_build_graph):
        """Test that run_command creates a default Config."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output=None)
        run_command(args)

        # Config should be instantiated with defaults
        mock_config.assert_called_once()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    def test_run_command_builds_graph_with_correct_params(self, mock_config, mock_build_graph):
        """Test that build_graph is called with correct parameters."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        config_instance = Mock()
        mock_config.return_value = config_instance

        args = argparse.Namespace(output=None)
        run_command(args)

        # Verify build_graph was called with correct arguments
        call_args = mock_build_graph.call_args
        assert call_args[1]["config"] == config_instance
        assert call_args[1]["health_check_interval"] == 30
        assert "llm" in call_args[1]
        assert "repo_path" in call_args[1]

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    def test_run_command_llm_is_mocked(self, mock_config, mock_build_graph):
        """Test that run_command creates a mock LLM with bind_tools."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output=None)
        run_command(args)

        # Check that the LLM passed to build_graph has bind_tools
        call_args = mock_build_graph.call_args
        llm = call_args[1]["llm"]
        assert hasattr(llm, "bind_tools")

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_png_uppercase_extension(self, mock_file, mock_config, mock_build_graph):
        """Test run_command with uppercase PNG extension."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid_png.return_value = b"PNG data"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output="graph.PNG")
        exit_code = run_command(args)

        assert exit_code == 0
        # Should still recognize .PNG as PNG format
        mock_compiled.draw_mermaid_png.assert_called_once()

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    @patch("builtins.open", new_callable=mock_open)
    def test_run_command_path_with_directories(self, mock_file, mock_config, mock_build_graph):
        """Test run_command with path containing directories."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_compiled = MagicMock()
        mock_compiled.draw_mermaid.return_value = "graph"
        mock_graph.get_graph.return_value = mock_compiled
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output="output/subdir/graph.mermaid")
        exit_code = run_command(args)

        assert exit_code == 0
        mock_file.assert_called_once_with(
            Path("output/subdir/graph.mermaid"),
            "w",
            encoding="utf-8"
        )

    @patch("app_operator.commands.viz_graph.build_graph")
    @patch("app_operator.commands.viz_graph.Config")
    def test_run_command_get_graph_exception(self, mock_config, mock_build_graph):
        """Test run_command handles exception from get_graph."""
        # Setup mocks
        mock_graph = MagicMock()
        mock_graph.get_graph.side_effect = Exception("Get graph failed")
        mock_build_graph.return_value = mock_graph

        args = argparse.Namespace(output=None)
        exit_code = run_command(args)

        assert exit_code == 1
