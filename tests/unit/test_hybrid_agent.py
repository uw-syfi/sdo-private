"""Unit tests for the HybridCodingAgent and updated RLMContext summary fields."""

import unittest.mock as mock

import pytest

from app_operator.cli_agent.hybrid_agent import HybridCodingAgent
from app_operator.cli_agent.rlm.environment import RLMContext, RLMEnvironment
from libs.model_config import ModelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_litellm_response(content: str, prompt_tokens=10, completion_tokens=5):
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = content
    usage = mock.MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = prompt_tokens + completion_tokens
    resp.usage = usage
    return resp


def _setup_repo(tmp_path):
    sds = tmp_path / ".sds"
    sds.mkdir()
    (sds / "deploy.sh").write_text("#!/bin/bash\ndocker compose up -d\n")
    logs = sds / "logs"
    logs.mkdir()
    (logs / "deploy.log").write_text("Error: port 8080 in use\n")
    (logs / "health_check.log").write_text("FAIL: /health returned 503\n")
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n")
    return sds


# ---------------------------------------------------------------------------
# RLMContext summary fields
# ---------------------------------------------------------------------------


class TestRLMContextSummaryFields:
    """Tests for the new pre-computed summary fields on RLMContext."""

    def test_summary_fields_default_empty(self):
        ctx = RLMContext()
        assert ctx.trajectory_summary == ""
        assert ctx.error_summary == ""
        assert ctx.script_summary == ""
        assert ctx.repo_summary == ""

    def test_summary_fields_in_to_dict(self):
        ctx = RLMContext(
            trajectory_summary="traj",
            error_summary="err",
            script_summary="script",
            repo_summary="repo",
        )
        d = ctx.to_dict()
        assert d["trajectory_summary"] == "traj"
        assert d["error_summary"] == "err"
        assert d["script_summary"] == "script"
        assert d["repo_summary"] == "repo"

    def test_get_summary_mentions_summaries_when_present(self):
        ctx = RLMContext(
            trajectory_summary="x" * 50,
            error_summary="y" * 80,
            script_summary="z" * 30,
            repo_summary="w" * 20,
        )
        summary = ctx.get_summary()
        assert "trajectory_summary" in summary
        assert "error_summary" in summary
        assert "script_summary" in summary
        assert "repo_summary" in summary
        assert "Pre-computed subagent summaries" in summary

    def test_get_summary_omits_summaries_section_when_all_empty(self):
        ctx = RLMContext()
        summary = ctx.get_summary()
        assert "Pre-computed subagent summaries" not in summary

    def test_namespace_exposes_summary_variables(self):
        ctx = RLMContext(
            trajectory_summary="trajectory insight",
            error_summary="error insight",
        )
        env = RLMEnvironment(ctx)
        assert env._namespace["trajectory_summary"] == "trajectory insight"
        assert env._namespace["error_summary"] == "error insight"
        assert env._namespace["script_summary"] == ""
        assert env._namespace["repo_summary"] == ""

    def test_execute_code_can_read_summaries(self):
        ctx = RLMContext(trajectory_summary="Port 8080 was tried before.")
        env = RLMEnvironment(ctx)

        result = env.execute_code(
            "result = 'Port' in trajectory_summary",
            "Check trajectory summary",
        )
        assert "True" in result


# ---------------------------------------------------------------------------
# HybridCodingAgent routing
# ---------------------------------------------------------------------------


class TestHybridRouting:
    def test_file_gen_prompt(self, tmp_path):
        agent = HybridCodingAgent(model="test-model")
        prompt = "Generate .sds/deploy.sh for the project."
        response = "FILE: .sds/deploy.sh\n```\n#!/bin/bash\ndocker compose up -d\n```"
        with mock.patch("litellm.completion", return_value=_make_litellm_response(response)):
            result = agent.generate(prompt, cwd=str(tmp_path))
        assert "deploy.sh" in result

    def test_direct_text_prompt(self, tmp_path):
        agent = HybridCodingAgent(model="test-model")
        prompt = "Provide fix_summary for the deployment."
        with mock.patch("litellm.completion", return_value=_make_litellm_response("summary text")):
            result = agent.generate(prompt, cwd=str(tmp_path))
        assert result == "summary text"


# ---------------------------------------------------------------------------
# HybridCodingAgent fix path
# ---------------------------------------------------------------------------


class TestHybridFixPath:
    """Tests that the fix path pre-runs 4 subagents then enters the RLM loop."""

    def test_pre_runs_4_subagents_before_rlm_loop(self, tmp_path):
        """The first 4 litellm calls are subagent analyses; subsequent calls are the RLM loop."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            messages = kwargs["messages"]

            if call_count <= 4:
                # Subagent calls: each has exactly 2 messages (system + user)
                assert len(messages) == 2, f"Subagent call {call_count} should have 2 messages"
                return _make_litellm_response(f"Subagent summary {call_count}")

            # RLM loop starts here — first iteration returns final_answer
            return _make_litellm_response("ACTION: final_answer\nANSWER: fixed")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            result = agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert call_count >= 5
        assert "fixed" in result

    def test_summaries_injected_into_rlm_namespace(self, tmp_path):
        """The RLM loop's first call should see summary variables in the system prompt."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        call_count = 0
        rlm_system_prompt = None

        def mock_completion(**kwargs):
            nonlocal call_count, rlm_system_prompt
            call_count += 1
            messages = kwargs["messages"]

            if call_count <= 4:
                return _make_litellm_response(f"Summary {call_count}")

            # First RLM call: capture the system prompt
            if rlm_system_prompt is None:
                rlm_system_prompt = messages[0]["content"]

            return _make_litellm_response("ACTION: final_answer\nANSWER: done")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert rlm_system_prompt is not None
        assert "trajectory_summary" in rlm_system_prompt
        assert "error_summary" in rlm_system_prompt
        assert "script_summary" in rlm_system_prompt
        assert "repo_summary" in rlm_system_prompt
        assert "Pre-computed subagent summaries" in rlm_system_prompt

    def test_rlm_loop_can_access_summaries_via_execute_code(self, tmp_path):
        """The LLM can read summary content via execute_code in the RLM loop."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count <= 4:
                return _make_litellm_response("Summary insight")

            # RLM loop: LLM reads the error_summary variable
            if call_count == 5:
                return _make_litellm_response(
                    "ACTION: execute_code\nDESCRIPTION: Read pre-computed error summary\nCODE:\nresult = error_summary"
                )

            # After seeing the result, return final answer
            return _make_litellm_response("ACTION: final_answer\nANSWER: done")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            result = agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert "done" in result

    def test_token_accumulation_includes_subagents_and_rlm(self, tmp_path):
        """Tokens from all subagent + RLM calls accumulate into _total_token_usage."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return _make_litellm_response("summary", 20, 5)
            return _make_litellm_response("ACTION: final_answer\nANSWER: done", 30, 10)

        with mock.patch("litellm.completion", side_effect=mock_completion):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        # At least 4 subagent calls × 25 tokens + 1 RLM call × 40 tokens
        assert agent._total_token_usage["total_tokens"] >= 4 * 25 + 40


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestHybridRegistration:
    def test_hybrid_in_agent_registry(self):
        from libs.agent_cli.base import AGENT_REGISTRY

        assert "hybrid" in AGENT_REGISTRY
        assert AGENT_REGISTRY["hybrid"] is HybridCodingAgent

    def test_hybrid_valid_provider(self):
        from app_operator.config import AgentConfig

        config = AgentConfig(backend="hybrid")
        assert config.backend == "hybrid"

    def test_factory_creates_hybrid(self):
        from app_operator.cli_agent.factory import create_agent_from_config
        from app_operator.config import AgentConfig, Config

        config = Config(agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model")))
        agent = create_agent_from_config("/tmp", config=config)
        assert isinstance(agent, HybridCodingAgent)

    def test_factory_forwards_location(self):
        from app_operator.cli_agent.factory import create_agent_from_config
        from app_operator.config import AgentConfig, Config

        config = Config(
            agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model", location="us-west1"))
        )
        agent = create_agent_from_config("/tmp", config=config)
        assert agent.location == "us-west1"  # type: ignore[attr-defined]

    def test_factory_forwards_dspy_config(self):
        from app_operator.cli_agent.factory import create_agent_from_config
        from app_operator.config import AgentConfig, Config, DSPyConfig

        dspy_cfg = DSPyConfig()
        config = Config(
            agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model")),
            dspy=dspy_cfg,
        )
        agent = create_agent_from_config("/tmp", config=config)
        assert isinstance(agent, HybridCodingAgent)
        assert agent.dspy_config is dspy_cfg  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# dspy_config storage
# ---------------------------------------------------------------------------


class TestHybridDspyConfig:
    """Tests that HybridCodingAgent stores dspy_config."""

    def test_stores_dspy_config(self):
        cfg = mock.MagicMock()
        agent = HybridCodingAgent(model="test-model", dspy_config=cfg)
        assert agent.dspy_config is cfg

    def test_default_dspy_config_is_none(self):
        agent = HybridCodingAgent(model="test-model")
        assert agent.dspy_config is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
