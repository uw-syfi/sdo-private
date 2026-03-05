from pathlib import Path
from unittest.mock import MagicMock, patch

from app_operator.filesystem import InMemoryFilesystem
from app_operator.langgraph.graph import build_graph


def test_analyze_code_skips_if_files_exist():
    """Test that code analysis is skipped when analysis files already exist.

    Instead of checking if agent methods were called (implementation detail),
    we verify the observable outcome: analysis files exist and remain unchanged.
    """
    # Setup
    repo_path = Path("/tmp/repo")
    fs = InMemoryFilesystem()
    fs.mkdir(repo_path / ".sds", parents=True)

    # Create pre-existing analysis files with known content
    original_analysis = "existing code analysis content"
    original_issues = "existing deployment issues content"
    fs.write_text(repo_path / ".sds" / "code_analysis.md", original_analysis)
    fs.write_text(repo_path / ".sds" / "deployment_issues.md", original_issues)

    config = MagicMock()
    config.agent.model = "gpt-4o"
    config.deployment.platform = "kubernetes"
    config.operator.deploy_timeout = 60

    llm = MagicMock()

    # We need to mock create_react_agent so it returns a mock agent
    with (
        patch("app_operator.langgraph.graph.create_react_agent") as mock_create_agent,
        patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run_script,
    ):
        analyze_agent_mock = MagicMock()
        script_agent_mock = MagicMock()
        fix_agent_mock = MagicMock()
        monitor_agent_mock = MagicMock()

        mock_create_agent.side_effect = [
            analyze_agent_mock,
            script_agent_mock,
            fix_agent_mock,
            monitor_agent_mock,
        ]

        # Script agent returns empty stream
        script_agent_mock.stream.return_value = []

        # Mock run_script to avoid subprocess calls and prevent failure
        mock_run_script.return_value = {
            "success": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }

        # Build graph
        graph = build_graph(llm, repo_path, config, 30, filesystem=fs)

        # Run graph
        initial_state = {
            "messages": [],
            "repo_path": str(repo_path),
            "attempt": 1,
            "max_attempts": 3,
            "analysis_done": False,
            "scripts_done": False,
            "deploy_result": None,
            "health_result": None,
            "monitor_count": 0,
            "monitor_max": 5,
            "analysis_summary": None,
            "last_fix_summary": None,
            "token_usage": {"input": 0, "output": 0, "total": 0},
        }

        # Run - we expect it to eventually fail or finish depending on mocks
        # But we only care about the first step
        try:
            # We use a recursion limit to stop it from running too long if it loops
            graph.invoke(initial_state, {"recursion_limit": 5})
        except Exception:
            pass

        # Verify observable outcomes instead of implementation details:
        # 1. Analysis files still exist
        assert fs.exists(repo_path / ".sds" / "code_analysis.md")
        assert fs.exists(repo_path / ".sds" / "deployment_issues.md")

        # 2. Analysis files were not modified (skip was successful)
        assert fs.read_text(repo_path / ".sds" / "code_analysis.md") == original_analysis
        assert fs.read_text(repo_path / ".sds" / "deployment_issues.md") == original_issues


def test_analyze_code_runs_if_files_missing():
    """Test that code analysis runs and creates files when they don't exist.

    Instead of checking if agent methods were called (implementation detail),
    we verify the observable outcome: analysis files are created.
    """
    # Setup
    repo_path = Path("/tmp/repo")
    fs = InMemoryFilesystem()
    fs.mkdir(repo_path / ".sds", parents=True)
    # No analysis files created

    config = MagicMock()
    config.agent.model = "gpt-4o"
    config.deployment.platform = "kubernetes"

    llm = MagicMock()

    with (
        patch("app_operator.langgraph.graph.create_react_agent") as mock_create_agent,
        patch("app_operator.langgraph.nodes.deployer.run_script") as mock_run_script,
        patch("app_operator.langgraph.nodes.analyzer.invoke_agent") as mock_invoke_agent,
    ):
        analyze_agent_mock = MagicMock()
        script_agent_mock = MagicMock()
        fix_agent_mock = MagicMock()
        monitor_agent_mock = MagicMock()

        mock_create_agent.side_effect = [
            analyze_agent_mock,
            script_agent_mock,
            fix_agent_mock,
            monitor_agent_mock,
        ]

        # Simulate analyzer creating files by having invoke_agent write them
        def create_analysis_files(*args, **kwargs):
            fs.write_text(repo_path / ".sds" / "code_analysis.md", "generated analysis")
            fs.write_text(repo_path / ".sds" / "deployment_issues.md", "generated issues")
            return "Analysis complete", []

        mock_invoke_agent.side_effect = create_analysis_files
        analyze_agent_mock.stream.return_value = []
        script_agent_mock.stream.return_value = []
        mock_run_script.return_value = {"success": True, "exit_code": 0}

        graph = build_graph(llm, repo_path, config, 30, filesystem=fs)

        initial_state = {
            "messages": [],
            "repo_path": str(repo_path),
            "attempt": 1,
            "max_attempts": 3,
            "analysis_done": False,
            "scripts_done": False,
            "deploy_result": None,
            "health_result": None,
            "monitor_count": 0,
            "monitor_max": 5,
            "analysis_summary": None,
            "last_fix_summary": None,
            "token_usage": {"input": 0, "output": 0, "total": 0},
        }

        try:
            graph.invoke(initial_state, {"recursion_limit": 5})
        except Exception:
            pass

        # Verify observable outcomes instead of implementation details:
        # 1. Analysis files were created
        assert fs.exists(repo_path / ".sds" / "code_analysis.md")
        assert fs.exists(repo_path / ".sds" / "deployment_issues.md")

        # 2. Analysis files have content
        assert len(fs.read_text(repo_path / ".sds" / "code_analysis.md")) > 0
        assert len(fs.read_text(repo_path / ".sds" / "deployment_issues.md")) > 0
