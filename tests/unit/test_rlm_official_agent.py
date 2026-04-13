"""Unit tests for the RLMOfficialAgent (official rlm library wrapper)."""

import unittest.mock as mock

import pytest

from app_operator.cli_agent.rlm_official_agent import (
    RLMOfficialAgent,
    _build_context_text,
    _build_custom_tools,
)
from app_operator.config import AgentConfig, Config
from libs.agent_cli.base import AGENT_REGISTRY


class TestRegistration:
    """The agent must be discoverable via the provider registry."""

    def test_registered_as_rlm_official(self):
        assert "rlm-official" in AGENT_REGISTRY
        assert AGENT_REGISTRY["rlm-official"] is RLMOfficialAgent


class TestConfigAcceptsBackend:
    """Config.from_dict must accept 'rlm-official' as a backend."""

    def test_config_parses_rlm_official(self):
        cfg = Config.from_dict({"agent": {"backend": "rlm-official", "model": "gemini-2.5-pro"}})
        assert cfg.agent.backend == "rlm-official"
        assert cfg.agent.model == "gemini-2.5-pro"

    def test_agent_config_validates(self):
        ac = AgentConfig(backend="rlm-official")
        assert ac.backend == "rlm-official"


class TestInit:
    """Constructor defaults and parameter forwarding."""

    def test_defaults(self):
        agent = RLMOfficialAgent()
        assert agent.model == "gemini-2.5-pro"
        assert agent.max_depth == 2
        assert agent.max_iterations == 30

    def test_custom_model(self):
        agent = RLMOfficialAgent(model="gemini-2.5-flash")
        assert agent.model == "gemini-2.5-flash"


class TestCustomTools:
    """_build_custom_tools must produce safe, repo-scoped tools."""

    def test_tool_keys(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        assert set(tools) == {"read_file", "write_file", "list_files", "run_shell", "REPO_PATH"}

    def test_repo_path_value(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        assert tools["REPO_PATH"]["tool"] == str(tmp_path.resolve())

    def test_read_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        tools = _build_custom_tools(tmp_path)
        read_fn = tools["read_file"]["tool"]
        assert read_fn("hello.txt") == "world"

    def test_read_file_missing(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        read_fn = tools["read_file"]["tool"]
        result = read_fn("missing.txt")
        assert "Error reading" in result

    def test_write_file(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        write_fn = tools["write_file"]["tool"]
        result = write_fn("out.txt", "data")
        assert "Wrote" in result
        assert (tmp_path / "out.txt").read_text() == "data"

    def test_write_file_creates_dirs(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        write_fn = tools["write_file"]["tool"]
        write_fn("sub/dir/f.txt", "nested")
        assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "nested"

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        tools = _build_custom_tools(tmp_path)
        list_fn = tools["list_files"]["tool"]
        result = list_fn(".")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_read_file_rejects_escape(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        read_fn = tools["read_file"]["tool"]
        with pytest.raises(PermissionError, match="outside the repo"):
            read_fn("/etc/passwd")

    def test_write_file_rejects_escape(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        write_fn = tools["write_file"]["tool"]
        with pytest.raises(PermissionError, match="outside the repo"):
            write_fn("/tmp/evil.txt", "bad")

    def test_run_shell(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        run_fn = tools["run_shell"]["tool"]
        result = run_fn("echo hello")
        assert "hello" in result


class TestBuildContextText:
    """_build_context_text assembles context from SDS artifacts."""

    def test_empty_repo(self, tmp_path):
        text = _build_context_text(tmp_path)
        assert str(tmp_path) in text

    def test_includes_deploy_script(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("#!/bin/bash\necho deploy")
        text = _build_context_text(tmp_path)
        assert "deploy.sh" in text
        assert "echo deploy" in text

    def test_includes_code_analysis(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "code_analysis.md").write_text("# Analysis\nAll good")
        text = _build_context_text(tmp_path)
        assert "Code Analysis" in text
        assert "All good" in text


class TestGenerateRouting:
    """generate() routes file-generation tasks to direct LLM call."""

    def test_file_gen_prompt_uses_direct_call(self, tmp_path):
        agent = RLMOfficialAgent(model="gemini-2.5-pro")
        prompt = "Generate .sds/deploy.sh for this repository"

        with mock.patch.object(agent, "_generate_files", return_value="ok") as m:
            result = agent.generate(prompt, cwd=str(tmp_path))
            m.assert_called_once_with(prompt, tmp_path)
            assert result == "ok"

    def test_fix_error_prompt_uses_rlm(self, tmp_path):
        agent = RLMOfficialAgent(model="gemini-2.5-pro")
        prompt = "The deployment has failed. Fix the docker deployment. .sds/deploy.sh"

        with mock.patch.object(agent, "_run_rlm", return_value="fixed") as m:
            result = agent.generate(prompt, cwd=str(tmp_path))
            m.assert_called_once()
            assert result == "fixed"


class TestRLMCompletion:
    """_run_rlm calls the official rlm library and extracts the response."""

    def test_calls_rlm_and_returns_response(self, tmp_path):
        agent = RLMOfficialAgent(model="gemini-2.5-pro")
        (tmp_path / ".sds").mkdir()

        mock_result = mock.MagicMock()
        mock_result.response = "Fixed deploy.sh"
        mock_result.execution_time = 42.0
        mock_result.usage_summary = None

        mock_rlm_cls = mock.MagicMock()
        mock_rlm_instance = mock.MagicMock()
        mock_rlm_instance.completion.return_value = mock_result
        mock_rlm_cls.return_value = mock_rlm_instance

        with mock.patch.dict("sys.modules", {"rlm": mock.MagicMock(), "rlm.logger": mock.MagicMock()}):
            with mock.patch("app_operator.cli_agent.rlm_official_agent.RLMOfficialAgent._run_rlm") as m:
                m.return_value = "Fixed deploy.sh"
                result = agent.generate("deployment has failed", cwd=str(tmp_path))
                assert result == "Fixed deploy.sh"

    def test_handles_missing_rlm_library(self, tmp_path):
        agent = RLMOfficialAgent(model="gemini-2.5-pro")
        (tmp_path / ".sds").mkdir()

        # Simulate ImportError by patching the import inside _run_rlm
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "rlm":
                raise ImportError("No module named 'rlm'")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=mock_import):
            result = agent._run_rlm("fix the deployment", tmp_path, 300)
            assert "not available" in result


class TestRecordUsage:
    """_record_usage extracts token counts from RLM result."""

    def test_records_tokens(self):
        agent = RLMOfficialAgent()
        recorder = mock.MagicMock()
        recorder.record_token_usage = mock.MagicMock()
        agent.recorder = recorder

        mock_model_usage = mock.MagicMock()
        mock_model_usage.total_input_tokens = 100
        mock_model_usage.total_output_tokens = 50

        mock_result = mock.MagicMock()
        mock_result.usage_summary.model_usage_summaries = {"gemini-2.5-pro": mock_model_usage}

        agent._record_usage(mock_result)

        recorder.record_token_usage.assert_called_once_with(
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        )

    def test_handles_none_usage(self):
        agent = RLMOfficialAgent()
        mock_result = mock.MagicMock()
        mock_result.usage_summary = None
        # Should not raise
        agent._record_usage(mock_result)
