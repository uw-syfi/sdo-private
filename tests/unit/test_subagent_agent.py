"""Unit tests for the SubagentCodingAgent and call_subagent primitive."""

import unittest.mock as mock

import pytest

from libs.agent_cli.subagent import call_subagent
from libs.agent_cli.subagent_agent import SubagentCodingAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_litellm_response(
        content: str, prompt_tokens=10, completion_tokens=5):
    """Create a minimal mock litellm response."""
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = content
    usage = mock.MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens
    resp.usage = usage
    return resp


# ---------------------------------------------------------------------------
# call_subagent tests
# ---------------------------------------------------------------------------

class TestCallSubagent:
    """Tests for the shared call_subagent() primitive."""

    def test_builds_fresh_messages(self):
        """Each call builds a new messages list (system + user), not shared history."""
        captured_kwargs = []

        def capture(**kwargs):
            captured_kwargs.append(kwargs)
            return _make_litellm_response("response")

        with mock.patch("litellm.completion", side_effect=capture):
            call_subagent(
                model="test-model",
                system_prompt="You are a test agent.",
                user_prompt="Analyse this.",
            )

        assert len(captured_kwargs) == 1
        messages = captured_kwargs[0]["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a test agent."
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "Analyse this."

    def test_accumulates_tokens(self):
        """Token usage is accumulated into the provided dict."""
        acc = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        with mock.patch("litellm.completion", return_value=_make_litellm_response("ok", 100, 50)):
            call_subagent(
                model="m", system_prompt="s", user_prompt="u", token_acc=acc,
            )

        assert acc["prompt_tokens"] == 100
        assert acc["completion_tokens"] == 50
        assert acc["total_tokens"] == 150

    def test_forwards_vertex_location(self):
        captured_kwargs = []

        def capture(**kwargs):
            captured_kwargs.append(kwargs)
            return _make_litellm_response("ok")

        with mock.patch("litellm.completion", side_effect=capture):
            call_subagent(
                model="m", system_prompt="s", user_prompt="u", location="us-east1",
            )

        assert captured_kwargs[0].get("vertex_location") == "us-east1"

    def test_returns_error_string_on_failure(self):
        with mock.patch("litellm.completion", side_effect=ConnectionError("boom")):
            result = call_subagent(
                model="m", system_prompt="s", user_prompt="u")

        assert "Subagent call failed" in result
        assert "boom" in result

    def test_keyboard_interrupt_propagates(self):
        """KeyboardInterrupt must not be swallowed."""
        with mock.patch("litellm.completion", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                call_subagent(model="m", system_prompt="s", user_prompt="u")

    @pytest.mark.parametrize("exc_class", [
        TimeoutError, ConnectionError, OSError,
    ])
    def test_specific_errors_return_error_string(self, exc_class):
        """Specific exception types are caught and return error strings."""
        with mock.patch("litellm.completion", side_effect=exc_class("test error")):
            result = call_subagent(
                model="m", system_prompt="s", user_prompt="u")

        assert "Subagent call failed" in result
        assert exc_class.__name__ in result
        assert "test error" in result

    def test_unexpected_exception_propagates(self):
        """Exceptions not in the handled set must propagate."""
        with mock.patch("litellm.completion", side_effect=ValueError("unexpected")):
            with pytest.raises(ValueError, match="unexpected"):
                call_subagent(model="m", system_prompt="s", user_prompt="u")


# ---------------------------------------------------------------------------
# SubagentCodingAgent exception handling tests
# ---------------------------------------------------------------------------

class TestSubagentExceptionHandling:
    """Tests for narrowed exception handling in SubagentCodingAgent."""

    def test_generate_direct_keyboard_interrupt_propagates(self, tmp_path):
        """KeyboardInterrupt in _generate_direct must propagate."""
        agent = SubagentCodingAgent(model="test-model")
        prompt = "Provide fix_summary for the deployment."

        with mock.patch("litellm.completion", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                agent.generate(prompt, cwd=str(tmp_path))

    def test_generate_files_keyboard_interrupt_propagates(self, tmp_path):
        """KeyboardInterrupt in _generate_files must propagate."""
        agent = SubagentCodingAgent(model="test-model")
        prompt = "Generate .sds/deploy.sh for the project."

        with mock.patch("litellm.completion", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                agent.generate(prompt, cwd=str(tmp_path))

    def test_generate_direct_handles_timeout(self, tmp_path):
        """TimeoutError in _generate_direct returns error string."""
        agent = SubagentCodingAgent(model="test-model")
        prompt = "Provide fix_summary for the deployment."

        with mock.patch("litellm.completion", side_effect=TimeoutError("timed out")):
            result = agent.generate(prompt, cwd=str(tmp_path))

        assert "LLM call failed" in result
        assert "TimeoutError" in result

    def test_generate_files_handles_connection_error(self, tmp_path):
        """ConnectionError in _generate_files returns error string."""
        agent = SubagentCodingAgent(model="test-model")
        prompt = "Generate .sds/deploy.sh for the project."

        with mock.patch("litellm.completion", side_effect=ConnectionError("refused")):
            result = agent.generate(prompt, cwd=str(tmp_path))

        assert "LLM call failed" in result
        assert "ConnectionError" in result

    def test_generate_direct_unexpected_error_propagates(self, tmp_path):
        """ValueError (not in handled set) must propagate from _generate_direct."""
        agent = SubagentCodingAgent(model="test-model")
        prompt = "Provide fix_summary for the deployment."

        with mock.patch("litellm.completion", side_effect=ValueError("bad")):
            with pytest.raises(ValueError, match="bad"):
                agent.generate(prompt, cwd=str(tmp_path))

    def test_root_synthesis_keyboard_interrupt_propagates(self, tmp_path):
        """KeyboardInterrupt in root synthesis must propagate."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        agent = SubagentCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return _make_litellm_response("analysis")
            raise KeyboardInterrupt

        with mock.patch("litellm.completion", side_effect=mock_completion):
            with pytest.raises(KeyboardInterrupt):
                agent.generate("Fix the deployment error", cwd=str(tmp_path))

    def test_error_string_includes_error_category(self, tmp_path):
        """Error messages should include the exception type name."""
        agent = SubagentCodingAgent(model="test-model")
        prompt = "Provide fix_summary for the deployment."

        with mock.patch("litellm.completion", side_effect=OSError("disk full")):
            result = agent.generate(prompt, cwd=str(tmp_path))

        assert "OSError" in result
        assert "disk full" in result


# ---------------------------------------------------------------------------
# SubagentCodingAgent routing tests
# ---------------------------------------------------------------------------

class TestSubagentRoutingFileGen:
    """File-generation prompts route to _generate_files."""

    def test_file_gen_prompt(self, tmp_path):
        agent = SubagentCodingAgent(model="test-model")

        prompt = "Generate .sds/deploy.sh for the project."
        response_text = (
            "FILE: .sds/deploy.sh\n"
            "```\n#!/bin/bash\ndocker compose up -d\n```"
        )

        with mock.patch("litellm.completion", return_value=_make_litellm_response(response_text)):
            result = agent.generate(prompt, cwd=str(tmp_path))

        assert "deploy.sh" in result
        assert (tmp_path / ".sds" / "deploy.sh").exists()


class TestSubagentRoutingDirectText:
    """Direct-text prompts route to _generate_direct."""

    def test_direct_text_prompt(self, tmp_path):
        agent = SubagentCodingAgent(model="test-model")
        prompt = "Provide fix_summary for the deployment."

        with mock.patch("litellm.completion", return_value=_make_litellm_response("summary text")):
            result = agent.generate(prompt, cwd=str(tmp_path))

        assert result == "summary text"


# ---------------------------------------------------------------------------
# SubagentCodingAgent fix path tests
# ---------------------------------------------------------------------------

class TestSubagentFixPath:
    """Tests for the fan-out + root synthesis fix path."""

    def _setup_repo(self, tmp_path):
        """Create a minimal repo with .sds artifacts."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("#!/bin/bash\ndocker compose up -d\n")
        (sds / "deploy.sh.bak").write_text("#!/bin/bash\ndocker compose up -d\n")
        logs = sds / "logs"
        logs.mkdir()
        (logs / "deploy.log").write_text("Error: port 8080 in use\n")
        (logs / "health_check.log").write_text("FAIL: /health returned 503\n")
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n")
        return sds

    def test_fix_calls_4_subagents_plus_root(self, tmp_path):
        """The fix path should make 5 litellm calls: 4 subagents + 1 root."""
        self._setup_repo(tmp_path)
        agent = SubagentCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return _make_litellm_response(f"Analysis {call_count}")
            # Root synthesis
            return _make_litellm_response(
                "FILE: .sds/deploy.sh\n```\n#!/bin/bash\ndocker compose up -d\n```"
            )

        with mock.patch("litellm.completion", side_effect=mock_completion):
            result = agent.generate(
                "Fix the deployment error",
                cwd=str(tmp_path))

        assert call_count == 5
        assert "deploy.sh" in result

    def test_root_receives_all_4_summaries(self, tmp_path):
        """The root synthesis call should contain all 4 subagent summaries."""
        self._setup_repo(tmp_path)
        agent = SubagentCodingAgent(model="test-model")

        captured_messages = []
        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return _make_litellm_response(
                    f"Summary from analyst {call_count}")
            # Root call — capture the messages
            captured_messages.extend(kwargs["messages"])
            return _make_litellm_response("Fixed deploy.sh")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        # Root call should have system + user messages
        assert len(captured_messages) == 2
        user_msg = captured_messages[1]["content"]
        assert "Trajectory Analysis" in user_msg
        assert "Error Log Analysis" in user_msg
        assert "Script Analysis" in user_msg
        assert "Repository Analysis" in user_msg

    def test_token_accumulation(self, tmp_path):
        """Tokens from all 5 calls should accumulate."""
        self._setup_repo(tmp_path)
        agent = SubagentCodingAgent(model="test-model")

        with mock.patch("litellm.completion", return_value=_make_litellm_response("ok", 20, 10)):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        # 5 calls × 20 prompt tokens each
        assert agent._total_token_usage["prompt_tokens"] == 100
        assert agent._total_token_usage["completion_tokens"] == 50
        assert agent._total_token_usage["total_tokens"] == 150

    def test_deploy_sh_written_from_root_response(self, tmp_path):
        """If root synthesis contains a FILE: .sds/deploy.sh section, it's written."""
        self._setup_repo(tmp_path)
        agent = SubagentCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return _make_litellm_response("analysis")
            return _make_litellm_response(
                "FILE: .sds/deploy.sh\n```\n#!/bin/bash\nnew content\n```"
            )

        with mock.patch("litellm.completion", side_effect=mock_completion):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert (
            tmp_path /
            ".sds" /
            "deploy.sh").read_text() == "#!/bin/bash\nnew content\n"

    def test_fix_path_with_no_artifacts(self, tmp_path):
        """Fix path should not crash when .sds directory is empty."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        agent = SubagentCodingAgent(model="test-model")

        with mock.patch("litellm.completion", return_value=_make_litellm_response("no fix needed")):
            result = agent.generate(
                "Fix the deployment error",
                cwd=str(tmp_path))

        assert "no fix needed" in result


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------

class TestSubagentRegistration:
    """Tests for provider registration."""

    def test_subagent_in_agent_registry(self):
        from libs.agent_cli.base import AGENT_REGISTRY

        assert "subagent" in AGENT_REGISTRY
        assert AGENT_REGISTRY["subagent"] is SubagentCodingAgent

    def test_subagent_valid_provider(self):
        from app_operator.config import AgentConfig

        config = AgentConfig(provider="subagent")
        assert config.provider == "subagent"

    def test_factory_creates_subagent(self):
        from app_operator.config import Config, AgentConfig
        from libs.agent_cli.factory import create_agent_from_config

        config = Config(agent=AgentConfig(provider="subagent"))
        agent = create_agent_from_config("/tmp", config=config)
        assert isinstance(agent, SubagentCodingAgent)

    def test_factory_forwards_location(self):
        from app_operator.config import Config, AgentConfig
        from libs.agent_cli.factory import create_agent_from_config

        config = Config(
            agent=AgentConfig(
                provider="subagent",
                location="us-west1"))
        agent = create_agent_from_config("/tmp", config=config)
        assert isinstance(agent, SubagentCodingAgent)
        assert agent.location == "us-west1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
