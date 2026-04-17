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
    """Tests that the fix path uses lazy specialist calls inside the RLM loop."""

    def test_rlm_loop_starts_before_any_specialist_calls(self, tmp_path):
        """Hybrid should start the RLM loop immediately and defer specialist work until requested."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        first_messages = None

        def mock_completion(**kwargs):
            nonlocal first_messages
            if first_messages is None:
                first_messages = kwargs["messages"]
            return _make_litellm_response("ACTION: final_answer\nANSWER: fixed")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            result = agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert first_messages is not None
        assert first_messages[0]["role"] == "system"
        assert "Recursive Language Model" in first_messages[0]["content"]
        assert "fixed" in result

    def test_system_prompt_advertises_lazy_specialists(self, tmp_path):
        """Hybrid should advertise specialist delegation instead of pre-computed summaries."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        rlm_system_prompt = None

        def mock_completion(**kwargs):
            nonlocal rlm_system_prompt
            messages = kwargs["messages"]
            if rlm_system_prompt is None:
                rlm_system_prompt = messages[0]["content"]
            return _make_litellm_response("ACTION: final_answer\nANSWER: done")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert rlm_system_prompt is not None
        assert "specialist_call" in rlm_system_prompt
        assert "error_log" in rlm_system_prompt
        assert "trajectory" in rlm_system_prompt
        assert "script" in rlm_system_prompt
        assert "repo" in rlm_system_prompt

    def test_rlm_loop_can_access_lazy_specialist_summary_via_execute_code(self, tmp_path):
        """A specialist result should be cached into the RLM namespace for later execute_code steps."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return _make_litellm_response(
                    "ACTION: specialist_call\nSPECIALIST: error_log\nTASK: Summarize the current deployment failures"
                )
            if call_count == 2:
                return _make_litellm_response("Port conflict summary")
            if call_count == 3:
                return _make_litellm_response(
                    "ACTION: execute_code\nDESCRIPTION: Read cached error summary\nCODE:\nresult = error_summary"
                )

            return _make_litellm_response("ACTION: final_answer\nANSWER: done")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            result = agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert "done" in result

    def test_repeated_specialist_call_uses_cache(self, tmp_path):
        """Repeated requests for the same specialist should reuse the cached summary."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        call_count = 0
        subagent_call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count, subagent_call_count
            call_count += 1
            messages = kwargs["messages"]

            if len(messages) == 2 and "Recursive Language Model" not in messages[0]["content"]:
                subagent_call_count += 1
                return _make_litellm_response("Repo summary")

            if call_count == 1:
                return _make_litellm_response(
                    "ACTION: specialist_call\nSPECIALIST: repo\nTASK: Summarize deployment-relevant repo constraints"
                )
            if call_count == 3:
                return _make_litellm_response(
                    "ACTION: specialist_call\nSPECIALIST: repo\nTASK: Re-check the same repo constraints"
                )
            return _make_litellm_response("ACTION: final_answer\nANSWER: done")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            result = agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert "done" in result
        assert subagent_call_count == 1

    def test_token_accumulation_includes_lazy_specialists_and_rlm(self, tmp_path):
        """Tokens from both lazy specialist calls and the main RLM loop should accumulate."""
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model")

        call_count = 0

        def mock_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_litellm_response(
                    "ACTION: specialist_call\nSPECIALIST: script\nTASK: Summarize likely deploy.sh issues",
                    20,
                    5,
                )
            if call_count == 2:
                return _make_litellm_response("summary", 20, 5)
            return _make_litellm_response("ACTION: final_answer\nANSWER: done", 30, 10)

        with mock.patch("litellm.completion", side_effect=mock_completion):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert agent._total_token_usage["total_tokens"] >= 25 + 25 + 40


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestHybridRegistration:
    def test_hybrid_in_agent_registry(self):
        from libs.agent_cli.base import AGENT_REGISTRY

        assert "hybrid" in AGENT_REGISTRY
        assert AGENT_REGISTRY["hybrid"] is HybridCodingAgent

    def test_legacy_architecture_backends_not_in_registry(self):
        from libs.agent_cli.base import AGENT_REGISTRY

        assert "rlm" not in AGENT_REGISTRY

    def test_hybrid_valid_provider(self):
        from app_operator.core import AgentConfig

        config = AgentConfig(backend="hybrid")
        assert config.backend == "hybrid"

    def test_factory_creates_hybrid(self):
        from app_operator.cli_agent.factory import create_agent_from_config
        from app_operator.core import AgentConfig, Config

        config = Config(agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model")))
        agent = create_agent_from_config("/tmp", config=config)
        assert isinstance(agent, HybridCodingAgent)

    def test_factory_forwards_location(self):
        from app_operator.cli_agent.factory import create_agent_from_config
        from app_operator.core import AgentConfig, Config

        config = Config(
            agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model", location="us-west1"))
        )
        agent = create_agent_from_config("/tmp", config=config)
        assert agent.location == "us-west1"  # type: ignore[attr-defined]

    def test_factory_forwards_dspy_config(self):
        from app_operator.cli_agent.factory import create_agent_from_config
        from app_operator.core import AgentConfig, Config, DSPyConfig

        dspy_cfg = DSPyConfig()
        config = Config(
            agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model")),
            dspy=dspy_cfg,
        )
        agent = create_agent_from_config("/tmp", config=config)
        assert isinstance(agent, HybridCodingAgent)
        assert agent.dspy_config is dspy_cfg  # type: ignore[attr-defined]

    def test_factory_forwards_rlm_mode(self):
        from app_operator.cli_agent.factory import create_agent_from_config
        from app_operator.core import AgentConfig, Config, RLMConfig

        config = Config(
            agent=AgentConfig(backend="hybrid", model_config=ModelConfig.from_string("test-model")),
            rlm=RLMConfig(mode="paper_faithful"),
        )
        agent = create_agent_from_config("/tmp", config=config)
        assert isinstance(agent, HybridCodingAgent)
        assert agent.rlm_mode == "paper_faithful"  # type: ignore[attr-defined]


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


class TestHybridRLMMode:
    def test_stores_rlm_mode(self):
        agent = HybridCodingAgent(model="test-model", rlm_mode="paper_faithful")
        assert agent.rlm_mode == "paper_faithful"

    def test_paper_faithful_mode_omits_lazy_specialists(self, tmp_path):
        _setup_repo(tmp_path)
        agent = HybridCodingAgent(model="test-model", rlm_mode="paper_faithful")

        first_messages = None

        def mock_completion(**kwargs):
            nonlocal first_messages
            if first_messages is None:
                first_messages = kwargs["messages"]
            return _make_litellm_response("ACTION: final_answer\nANSWER: done")

        with mock.patch("litellm.completion", side_effect=mock_completion):
            agent.generate("Fix the deployment error", cwd=str(tmp_path))

        assert first_messages is not None
        system_prompt = first_messages[0]["content"]
        assert "specialist_call" not in system_prompt
        assert "Lazy specialists are available" not in system_prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
