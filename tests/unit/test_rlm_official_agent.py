"""Unit tests for the RLMOfficialAgent (official rlm library wrapper)."""

import unittest.mock as mock

import pytest
from agentshim import CodingAgent

from app_operator.cli_agent.factory import create_agent_from_config
from app_operator.cli_agent.rlm_official_agent import (
    _SDS_ROOT_PROMPT_PREFIX,
    RLMOfficialAgent,
    _build_context_text,
    _build_custom_tools,
)
from app_operator.core import AgentConfig, Config, DSPyConfig
from libs.model_config import ModelConfig


class TestRegistration:
    """The agent must be discoverable via the portable facade."""

    def test_registered_as_rlm_official(self):
        agent = CodingAgent(provider="rlm-official", model="gemini-2.5-pro")
        assert isinstance(agent.backend, RLMOfficialAgent)

    def test_factory_forwards_backend_kwargs(self, tmp_path):
        dspy_cfg = DSPyConfig()
        config = Config(
            agent=AgentConfig(
                backend="rlm-official",
                model_config=ModelConfig.from_string("gemini-2.5-pro", location="us-west1"),
            ),
            dspy=dspy_cfg,
        )

        agent = create_agent_from_config(str(tmp_path), config=config)

        assert isinstance(agent.backend, RLMOfficialAgent)  # type: ignore[attr-defined]
        assert agent.backend.location == "us-west1"  # type: ignore[attr-defined]
        assert agent.backend.dspy_config is dspy_cfg  # type: ignore[attr-defined]


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
        expected = {
            "read_file",
            "write_file",
            "append_file",
            "list_files",
            "run_shell",
            "validate_compose",
            "check_build_paths",
            "REPO_PATH",
        }
        assert set(tools) == expected

    def test_repo_path_value(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        assert tools["REPO_PATH"]["tool"] == str(tmp_path.resolve())

    def test_read_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        tools = _build_custom_tools(tmp_path)
        read_fn = tools["read_file"]["tool"]
        assert read_fn("hello.txt") == "world"

    def test_read_file_prints_output(self, tmp_path, capsys):
        (tmp_path / "hello.txt").write_text("world")
        tools = _build_custom_tools(tmp_path)
        tools["read_file"]["tool"]("hello.txt")
        assert "world" in capsys.readouterr().out

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

    def test_write_file_prints_confirmation(self, tmp_path, capsys):
        tools = _build_custom_tools(tmp_path)
        tools["write_file"]["tool"]("out.txt", "data")
        assert "Wrote" in capsys.readouterr().out

    def test_write_file_creates_dirs(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        write_fn = tools["write_file"]["tool"]
        write_fn("sub/dir/f.txt", "nested")
        assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "nested"

    def test_append_file_prints_confirmation(self, tmp_path, capsys):
        tools = _build_custom_tools(tmp_path)
        tools["append_file"]["tool"]("out.txt", "data")
        assert "Appended" in capsys.readouterr().out

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        tools = _build_custom_tools(tmp_path)
        list_fn = tools["list_files"]["tool"]
        result = list_fn(".")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_list_files_prints_output(self, tmp_path, capsys):
        (tmp_path / "a.txt").write_text("")
        tools = _build_custom_tools(tmp_path)
        tools["list_files"]["tool"](".")
        assert "a.txt" in capsys.readouterr().out

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

    def test_run_shell_prints_output(self, tmp_path, capsys):
        tools = _build_custom_tools(tmp_path)
        tools["run_shell"]["tool"]("echo hello")
        assert "hello" in capsys.readouterr().out

    def test_check_build_paths_missing_file(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        fn = tools["check_build_paths"]["tool"]
        result = fn("docker-compose.yml")
        assert "File not found" in result

    def test_check_build_paths_missing_context(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  app:\n    build:\n      context: ./nonexistent\n")
        tools = _build_custom_tools(tmp_path)
        fn = tools["check_build_paths"]["tool"]
        result = fn("docker-compose.yml")
        assert "Missing" in result

    def test_check_build_paths_all_exist(self, tmp_path):
        (tmp_path / "app").mkdir()
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  app:\n    build:\n      context: ./app\n")
        tools = _build_custom_tools(tmp_path)
        fn = tools["check_build_paths"]["tool"]
        result = fn("docker-compose.yml")
        assert "1 build context paths exist" in result

    def test_validate_compose_missing_file(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        fn = tools["validate_compose"]["tool"]
        result = fn("docker-compose.yml")
        assert "File not found" in result


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

    def test_all_prompts_route_through_rlm(self, tmp_path):
        agent = RLMOfficialAgent(model="gemini-2.5-pro")
        prompt = "Generate .sds/deploy.sh for this repository"

        with mock.patch.object(agent, "_run_rlm", return_value="ok") as m:
            result = agent.generate(prompt, cwd=str(tmp_path))
            m.assert_called_once()
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


class TestPostProcessScript:
    """_post_process_script applies deterministic fixes to shell scripts."""

    def test_fixes_project_name_uppercase(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        script = sds / "deploy.sh"
        script.write_text('#!/bin/bash\nPROJECT_NAME="MyApp"\necho $PROJECT_NAME\n')
        agent = RLMOfficialAgent()
        agent._post_process_script(script)
        content = script.read_text()
        assert "tr '[:upper:]' '[:lower:]'" in content
        assert 'PROJECT_NAME="MyApp"' not in content

    def test_fixes_docker_compose_hyphen(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        script = sds / "deploy.sh"
        script.write_text("#!/bin/bash\ndocker-compose up -d\n")
        agent = RLMOfficialAgent()
        agent._post_process_script(script)
        content = script.read_text()
        assert "docker compose up -d" in content
        assert "docker-compose" not in content

    def test_no_change_when_already_correct(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        script = sds / "deploy.sh"
        original = (
            "#!/bin/bash\nPROJECT_NAME=$(basename \"$APP_DIR\" | tr '[:upper:]' '[:lower:]')\ndocker compose up\n"
        )
        script.write_text(original)
        agent = RLMOfficialAgent()
        agent._post_process_script(script)
        assert script.read_text() == original

    def test_post_process_scripts_processes_both(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text('PROJECT_NAME="App"\ndocker-compose up\n')
        (sds / "health_check.sh").write_text('PROJECT_NAME="App"\ndocker-compose ps\n')
        agent = RLMOfficialAgent()
        agent._post_process_scripts(tmp_path)
        assert "docker compose" in (sds / "deploy.sh").read_text()
        assert "docker compose" in (sds / "health_check.sh").read_text()


class TestRateLimitDetection:
    """_is_rate_limit_error identifies rate-limit exceptions."""

    @pytest.mark.parametrize(
        "msg",
        [
            "429 Too Many Requests",
            "RESOURCE_EXHAUSTED: quota exceeded",
            "Rate limit exceeded",
            "RateLimitError: try again later",
            "Quota exceeded for model",
        ],
    )
    def test_detects_rate_limit(self, msg):
        assert RLMOfficialAgent._is_rate_limit_error(Exception(msg))

    def test_rejects_non_rate_limit(self):
        assert not RLMOfficialAgent._is_rate_limit_error(Exception("Connection refused"))


class TestRootPromptContent:
    """_SDS_ROOT_PROMPT_PREFIX must contain SDS patterns that prevent known failures."""

    def test_health_check_forbids_curling_service_names_from_host(self):
        assert "NEVER" in _SDS_ROOT_PROMPT_PREFIX
        assert "curl http://<docker-service-name>" in _SDS_ROOT_PROMPT_PREFIX
        assert "curl localhost:<EXPOSED-HOST-PORT>" in _SDS_ROOT_PROMPT_PREFIX

    def test_instructs_reading_deploy_logs(self):
        assert "deploy_attempt_N.log" in _SDS_ROOT_PROMPT_PREFIX
        assert "read_file()" in _SDS_ROOT_PROMPT_PREFIX

    def test_forbids_wholesale_rewrites(self):
        assert "NEVER rewrite deploy.sh" in _SDS_ROOT_PROMPT_PREFIX
        assert "TARGETED fixes" in _SDS_ROOT_PROMPT_PREFIX

    def test_dependency_conflict_detection(self):
        assert "opentelemetry-exporter-jaeger" in _SDS_ROOT_PROMPT_PREFIX
        assert "CMD/ENTRYPOINT" in _SDS_ROOT_PROMPT_PREFIX

    def test_deployment_progress_tracking(self):
        assert "deployment_progress.md" in _SDS_ROOT_PROMPT_PREFIX
        assert "Hypothesis" in _SDS_ROOT_PROMPT_PREFIX

    def test_repair_invariants(self):
        assert "NO HOST ARTIFACTS" in _SDS_ROOT_PROMPT_PREFIX
        assert "NO WHOLESALE REWRITES" in _SDS_ROOT_PROMPT_PREFIX
        assert "READ BEFORE EDIT" in _SDS_ROOT_PROMPT_PREFIX


class TestRunShellBlocksDeploy:
    """run_shell must block deployment commands."""

    def test_blocks_deploy_start(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        run_fn = tools["run_shell"]["tool"]
        result = run_fn("bash .sds/deploy.sh start")
        assert "ERROR" in result
        assert "Cannot run deployment commands" in result

    def test_allows_diagnostic_commands(self, tmp_path):
        tools = _build_custom_tools(tmp_path)
        run_fn = tools["run_shell"]["tool"]
        result = run_fn("docker compose ps")
        # Should not be blocked (even if docker isn't running, no ERROR about deployment)
        assert "Cannot run deployment commands" not in result
