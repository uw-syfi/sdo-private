from unittest.mock import MagicMock, patch
from pathlib import Path
from app_operator.langgraph.graph import build_graph
from app_operator.filesystem import InMemoryFilesystem


def test_analyze_code_skips_if_files_exist():
    # Setup
    repo_path = Path("/tmp/repo")
    fs = InMemoryFilesystem()
    fs.mkdir(repo_path / ".sds", parents=True)
    fs.write_text(repo_path / ".sds" / "code_analysis.md", "content")
    fs.write_text(repo_path / ".sds" / "deployment_issues.md", "content")

    config = MagicMock()
    config.agent.model = "gpt-4o"
    config.deployment.platform = "kubernetes"
    config.operator.deploy_timeout = 60

    llm = MagicMock()

    # We need to mock create_react_agent so it returns a mock agent
    with (
        patch("app_operator.langgraph.graph.create_react_agent") as mock_create_agent,
        patch("app_operator.langgraph.graph._run_script") as mock_run_script,
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

        # Assertions
        # The analyze agent should NOT have been called because files exist
        analyze_agent_mock.stream.assert_not_called()

        # The script agent SHOULD have been called (next step)
        # This confirms we didn't just crash before doing anything
        assert script_agent_mock.stream.called


def test_analyze_code_runs_if_files_missing():
    # Setup
    repo_path = Path("/tmp/repo")
    fs = InMemoryFilesystem()
    # No files created

    config = MagicMock()
    config.agent.model = "gpt-4o"
    config.deployment.platform = "kubernetes"

    llm = MagicMock()

    with (
        patch("app_operator.langgraph.graph.create_react_agent") as mock_create_agent,
        patch("app_operator.langgraph.graph._run_script") as mock_run_script,
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

        # Assertions
        # The analyze agent SHOULD have been called because files don't exist
        assert analyze_agent_mock.stream.called
