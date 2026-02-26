"""Unit tests for RLM (Recursive Language Model) components."""

import pytest
from app_operator.rlm.environment import (
    RLMEnvironment,
    RLMContext,
    RLMCall,
    ActionType,
    _validate_file_refs,
    _estimate_tokens,
)
from app_operator.rlm.recursive_agent import RecursiveDeploymentAgent
from app_operator.rlm.metrics import (
    RLMEfficiencyMetric,
    RLMContextUtilizationMetric,
    RLMCompositeMetric,
    extract_rlm_statistics_from_trajectory,
)


class TestRLMContext:
    """Tests for RLMContext dataclass."""

    def test_create_empty_context(self):
        """Test creating an empty context."""
        context = RLMContext()

        assert context.error_log == ""
        assert context.deployment_script == ""
        assert len(context.previous_attempts) == 0
        assert context.attempt_number == 0

    def test_create_context_with_data(self):
        """Test creating context with data."""
        context = RLMContext(
            error_log="Error: deployment failed",
            deployment_script="docker-compose up",
            previous_attempts=[{"attempt": 1, "error": "port conflict"}],
            attempt_number=2,
        )

        assert "deployment failed" in context.error_log
        assert "docker-compose" in context.deployment_script
        assert len(context.previous_attempts) == 1
        assert context.attempt_number == 2

    def test_to_dict(self):
        """Test converting context to dictionary."""
        context = RLMContext(error_log="test error", attempt_number=5)

        data = context.to_dict()

        assert data["error_log"] == "test error"
        assert data["attempt_number"] == 5
        assert isinstance(data, dict)

    def test_get_summary(self):
        """Test generating context summary."""
        context = RLMContext(
            error_log="x" * 10000, deployment_script="y" * 500, attempt_number=3
        )

        summary = context.get_summary()

        assert "10000 chars" in summary
        assert "500 chars" in summary
        assert "attempt_number: 3" in summary
        assert "Available context variables" in summary


class TestRLMEnvironment:
    """Tests for RLMEnvironment."""

    def test_create_environment(self):
        """Test creating RLM environment."""
        context = RLMContext(error_log="test log")
        env = RLMEnvironment(context)

        assert env.context == context
        assert env.max_recursion_depth == 5
        assert env.current_depth == 0
        assert len(env.call_history) == 0

    def test_execute_code_simple(self):
        """Test executing simple code."""
        context = RLMContext(error_log="Error: port 8080\nError: port 8081")
        env = RLMEnvironment(context)

        code = """
import re
errors = re.findall(r'Error: (.*)', error_log)
result = errors
"""

        result = env.execute_code(code, "Extract errors")

        assert "port 8080" in result
        assert "port 8081" in result
        assert len(env.call_history) == 1
        assert env.call_history[0].action_type == ActionType.EXECUTE_CODE

    def test_execute_code_with_utilities(self):
        """Test code execution has access to utilities."""
        context = RLMContext(error_log="line1\nline2\nline3")
        env = RLMEnvironment(context)

        code = """
lines = error_log.split('\\n')
result = len(lines)
"""

        result = env.execute_code(code, "Count lines")

        assert "3" in result

    def test_execute_code_failure(self):
        """Test code execution with invalid code."""
        context = RLMContext(error_log="test")
        env = RLMEnvironment(context)

        invalid_code = "result = undefined_variable"

        with pytest.raises(RuntimeError, match="Code execution failed"):
            env.execute_code(invalid_code, "This should fail")

        # Failed call still recorded
        assert len(env.call_history) == 1
        assert "ERROR" in env.call_history[0].output

    def test_recursive_call(self):
        """Test making recursive LLM calls."""
        context = RLMContext(error_log="test log")
        env = RLMEnvironment(context, max_recursion_depth=3)

        def mock_llm(prompt: str) -> str:
            return f"Response to: {prompt[:20]}"

        result = env.recursive_call(
            "Analyze this error", filtered_context={"snippet": "Error: test"}, llm_function=mock_llm
        )

        assert "Response to:" in result
        assert len(env.call_history) == 1
        assert env.call_history[0].action_type == ActionType.RECURSIVE_CALL
        # Depth incremented and decremented
        assert env.current_depth == 0

    def test_recursive_call_max_depth(self):
        """Test recursion depth limit."""
        context = RLMContext()
        env = RLMEnvironment(context, max_recursion_depth=2)

        def recursive_llm(prompt: str) -> str:
            if env.current_depth < 5:  # Try to exceed limit
                return env.recursive_call("sub-task", llm_function=recursive_llm)
            return "done"

        result = env.recursive_call("initial", llm_function=recursive_llm)

        # Should hit limit and return warning
        assert "limit" in result.lower() or "done" in result.lower()

    def test_get_statistics(self):
        """Test getting RLM statistics."""
        context = RLMContext(error_log="test" * 1000)
        env = RLMEnvironment(context)

        # Make some calls
        env.execute_code("result = len(error_log)", "Count chars")
        env.execute_code("result = 'test'", "Simple test")

        def mock_llm(p):
            return "response"

        env.recursive_call("subtask", llm_function=mock_llm)

        stats = env.get_statistics()

        assert stats["total_calls"] == 3
        assert stats["code_executions"] == 2
        assert stats["recursive_calls"] == 1
        assert stats["max_depth_reached"] == 1
        assert stats["baseline_context_tokens"] > 0

    def test_record_callback(self):
        """Test that record callback is called."""
        context = RLMContext(error_log="test")
        recorded_calls = []

        def callback(call: RLMCall):
            recorded_calls.append(call)

        env = RLMEnvironment(context, record_callback=callback)

        env.execute_code("result = 42", "Test")

        assert len(recorded_calls) == 1
        assert recorded_calls[0].action_type == ActionType.EXECUTE_CODE

    def test_get_system_prompt(self):
        """Test RLM system prompt generation."""
        context = RLMContext(error_log="test log", attempt_number=5)
        env = RLMEnvironment(context)

        prompt = env.get_system_prompt()

        assert "Recursive Language Model" in prompt
        assert "execute_code" in prompt.lower()
        assert "recursive_call" in prompt.lower()
        assert "final_answer" in prompt.lower()
        assert "recursion depth: 0/5" in prompt.lower()


class TestValidateFileRefs:
    """Tests for the _validate_file_refs helper."""

    def test_all_refs_exist(self, tmp_path):
        (tmp_path / "entrypoint.sh").write_text("#!/bin/bash")
        script = "chmod +x entrypoint.sh\n"
        result = _validate_file_refs(script, str(tmp_path))
        assert result == "All referenced file paths exist."

    def test_missing_chmod_target(self, tmp_path):
        script = "chmod +x nginx-web-server/lua-scripts/docker-entrypoint.sh\n"
        result = _validate_file_refs(script, str(tmp_path))
        assert "MISSING" in result
        assert "docker-entrypoint.sh" in result

    def test_skips_variables(self, tmp_path):
        script = "chmod +x $SCRIPT_PATH\n"
        result = _validate_file_refs(script, str(tmp_path))
        assert result == "All referenced file paths exist."

    def test_skips_flags(self, tmp_path):
        script = "cp -r src/ dst/\n"
        (tmp_path / "src").mkdir()
        (tmp_path / "dst").mkdir()
        result = _validate_file_refs(script, str(tmp_path))
        assert result == "All referenced file paths exist."

    def test_skips_comment_lines(self, tmp_path):
        script = "# chmod +x nonexistent.sh\n"
        result = _validate_file_refs(script, str(tmp_path))
        assert result == "All referenced file paths exist."

    def test_multiple_missing_refs(self, tmp_path):
        script = (
            "chmod +x missing_a.sh\n"
            "chmod +x missing_b.sh\n"
        )
        result = _validate_file_refs(script, str(tmp_path))
        assert "missing_a.sh" in result
        assert "missing_b.sh" in result

    def test_missing_cp_source(self, tmp_path):
        script = "cp ghost.conf /etc/nginx/nginx.conf\n"
        result = _validate_file_refs(script, str(tmp_path))
        assert "MISSING" in result
        assert "ghost.conf" in result

    def test_absolute_path_checked(self, tmp_path):
        script = f"chmod +x {tmp_path}/nonexistent.sh\n"
        result = _validate_file_refs(script, str(tmp_path))
        assert "MISSING" in result


class TestRLMContextOriginalScript:
    """Tests for the original_script backtracking field."""

    def test_default_is_empty(self):
        context = RLMContext()
        assert context.original_script == ""

    def test_set_original_script(self):
        context = RLMContext(original_script="#!/bin/bash\ndocker compose up -d\n")
        assert "docker compose" in context.original_script

    def test_to_dict_includes_original_script(self):
        context = RLMContext(original_script="original content")
        data = context.to_dict()
        assert data["original_script"] == "original content"

    def test_get_summary_mentions_original_script(self):
        context = RLMContext(original_script="#!/bin/bash\n")
        summary = context.get_summary()
        assert "original_script" in summary
        assert "backtracking" in summary

    def test_get_summary_notes_unavailable_when_empty(self):
        context = RLMContext(original_script="")
        summary = context.get_summary()
        assert "original_script" in summary
        assert "not available" in summary


class TestRLMEnvironmentValidation:
    """Tests for file validation and backtracking in RLMEnvironment."""

    def test_namespace_exposes_validate_file_refs(self):
        context = RLMContext()
        env = RLMEnvironment(context)
        assert "validate_file_refs" in env._namespace
        assert callable(env._namespace["validate_file_refs"])

    def test_namespace_exposes_original_script(self):
        context = RLMContext(original_script="#!/bin/bash\n")
        env = RLMEnvironment(context)
        assert env._namespace["original_script"] == "#!/bin/bash\n"

    def test_execute_code_can_call_validate_file_refs(self, tmp_path):
        context = RLMContext()
        env = RLMEnvironment(context, cwd=str(tmp_path))

        code = (
            "result = validate_file_refs("
            "'chmod +x missing.sh\\n', cwd)"
        )
        result = env.execute_code(code, "Validate file refs")
        assert "MISSING" in result
        assert "missing.sh" in result

    def test_execute_code_validate_passes_for_existing_file(self, tmp_path):
        (tmp_path / "run.sh").write_text("#!/bin/bash")
        context = RLMContext()
        env = RLMEnvironment(context, cwd=str(tmp_path))

        code = "result = validate_file_refs('chmod +x run.sh\\n', cwd)"
        result = env.execute_code(code, "Validate existing file")
        assert "All referenced file paths exist." in result

    def test_system_prompt_mentions_validate_file_refs(self):
        context = RLMContext()
        env = RLMEnvironment(context)
        prompt = env.get_system_prompt()
        assert "validate_file_refs" in prompt

    def test_system_prompt_mentions_backtracking(self):
        context = RLMContext()
        env = RLMEnvironment(context)
        prompt = env.get_system_prompt()
        assert "MISSING" in prompt
        assert "original_script" in prompt


class TestRLMAgentBackup:
    """Tests for the deploy.sh backup logic in RLMCodingAgent._build_context."""

    def test_backup_created_on_first_build(self, tmp_path):
        from libs.agent_cli.rlm_agent import RLMCodingAgent

        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("#!/bin/bash\ndocker compose up -d\n")

        agent = RLMCodingAgent()
        ctx = agent._build_context(tmp_path)

        assert (sds / "deploy.sh.bak").exists()
        assert ctx.original_script == "#!/bin/bash\ndocker compose up -d\n"

    def test_backup_not_overwritten_on_subsequent_builds(self, tmp_path):
        from libs.agent_cli.rlm_agent import RLMCodingAgent

        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("original content\n")

        agent = RLMCodingAgent()
        agent._build_context(tmp_path)  # creates backup

        # Simulate agent modifying deploy.sh
        (sds / "deploy.sh").write_text("modified content\n")

        ctx = agent._build_context(tmp_path)
        # original_script still reflects the first backup
        assert ctx.original_script == "original content\n"
        assert ctx.deployment_script == "modified content\n"

    def test_no_backup_when_deploy_sh_missing(self, tmp_path):
        from libs.agent_cli.rlm_agent import RLMCodingAgent

        sds = tmp_path / ".sds"
        sds.mkdir()  # no deploy.sh

        agent = RLMCodingAgent()
        ctx = agent._build_context(tmp_path)

        assert not (sds / "deploy.sh.bak").exists()
        assert ctx.original_script == ""


class TestRLMMetrics:
    """Tests for RLM metrics."""

    def test_rlm_efficiency_metric_good(self):
        """Test RLM efficiency metric with good example."""
        metric = RLMEfficiencyMetric(
            max_expected_calls=10, max_expected_depth=3, code_to_recursive_ratio_target=2.0
        )

        class MockExample:
            rlm_statistics = {
                "total_calls": 5,
                "code_executions": 4,
                "recursive_calls": 2,
                "max_depth_reached": 2,
            }
            success = True

        score = metric(MockExample(), None)

        # Should score well - reasonable calls, good ratio, shallow depth
        assert score > 0.7

    def test_rlm_efficiency_metric_poor(self):
        """Test RLM efficiency metric with poor example."""
        metric = RLMEfficiencyMetric()

        class MockExample:
            rlm_statistics = {
                "total_calls": 50,  # Too many calls
                "code_executions": 2,  # Not enough code
                "recursive_calls": 48,  # Too many recursive calls
                "max_depth_reached": 10,  # Too deep
            }
            success = False  # Also failed

        score = metric(MockExample(), None)

        # Should score poorly
        assert score < 0.3

    def test_rlm_efficiency_metric_no_rlm(self):
        """Test metric with non-RLM example."""
        metric = RLMEfficiencyMetric()

        class MockExample:
            pass  # No rlm_statistics

        score = metric(MockExample(), None)

        # Should return neutral score
        assert score == 0.5

    def test_rlm_context_utilization_metric_good(self):
        """Test context utilization metric with good example."""
        metric = RLMContextUtilizationMetric(target_savings_ratio=0.5)

        class MockExample:
            # baseline=10000, saved=8000 → 80% savings, exceeds 50% target
            rlm_statistics = {"total_tokens_saved": 8000, "baseline_context_tokens": 10000}

        score = metric(MockExample(), None)

        # Exceeded target (50%), should score 1.0
        assert score >= 1.0

    def test_rlm_context_utilization_metric_poor(self):
        """Test context utilization metric with poor example."""
        metric = RLMContextUtilizationMetric(target_savings_ratio=0.5)

        class MockExample:
            # baseline=10000, saved=1000 → 10% savings (below 50% target)
            rlm_statistics = {"total_tokens_saved": 1000, "baseline_context_tokens": 10000}

        score = metric(MockExample(), None)

        # Below target, should score low
        assert score < 0.5

    def test_rlm_context_utilization_metric_negative_savings(self):
        """RLM cost more than single-call baseline — score should be 0.0."""
        metric = RLMContextUtilizationMetric(target_savings_ratio=0.5)

        class MockExample:
            # baseline=5000, actual=8000 → saved=-3000 (RLM cost more)
            rlm_statistics = {"total_tokens_saved": -3000, "baseline_context_tokens": 5000}

        score = metric(MockExample(), None)

        assert score == 0.0

    def test_rlm_composite_metric(self):
        """Test composite RLM metric."""
        metric = RLMCompositeMetric(
            success_weight=0.35,
            efficiency_weight=0.20,
            token_weight=0.15,
            rlm_efficiency_weight=0.15,
            rlm_context_weight=0.15,
        )

        class MockExample:
            success = True
            iterations = 3
            token_usage = {"input": 500, "output": 200}
            rlm_statistics = {
                "total_calls": 5,
                "code_executions": 3,
                "recursive_calls": 2,
                "total_tokens_saved": 8000,
                "baseline_context_tokens": 10000,
                "max_depth_reached": 1,
            }

        class MockPrediction:
            rendered_prompt = "test prompt"

        score = metric(MockExample(), MockPrediction())

        # Should score well - successful, efficient, good RLM usage
        assert score > 0.7
        assert score <= 1.0

    def test_rlm_composite_metric_weights_validation(self):
        """Test that composite metric validates weights sum to 1.0."""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            RLMCompositeMetric(
                success_weight=0.5,
                efficiency_weight=0.5,  # Total > 1.0
                token_weight=0.2,
                rlm_efficiency_weight=0.1,
                rlm_context_weight=0.1,
            )

    def test_rlm_composite_metric_none_prediction(self):
        """Test composite metric with None prediction."""
        metric = RLMCompositeMetric()

        class MockExample:
            pass

        score = metric(MockExample(), None)

        # Should return 0.0 for None prediction
        assert score == 0.0


class TestRLMCall:
    """Tests for RLMCall dataclass."""

    def test_create_rlm_call(self):
        """Test creating an RLM call record."""
        call = RLMCall(
            action_type=ActionType.EXECUTE_CODE,
            depth=1,
            input_prompt="Extract errors",
            code_or_subtask="result = re.findall(...)",
            output="['Error 1', 'Error 2']",
            tokens_saved=5000,
            timestamp="2026-02-13 10:00:00",
        )

        assert call.action_type == ActionType.EXECUTE_CODE
        assert call.depth == 1
        assert call.tokens_saved == 5000

    def test_rlm_call_to_dict(self):
        """Test converting RLM call to dictionary."""
        call = RLMCall(
            action_type=ActionType.RECURSIVE_CALL,
            depth=2,
            input_prompt="Analyze ports",
            code_or_subtask='{"ports": [8080, 8081]}',
            output="Use ports 9080-9081",
        )

        data = call.to_dict()

        assert data["action_type"] == "recursive_call"
        assert data["depth"] == 2
        assert "ports" in data["code_or_subtask"]
        assert isinstance(data, dict)


class TestEstimateTokens:
    """Tests for the _estimate_tokens heuristic."""

    def test_empty_string_returns_zero(self):
        assert _estimate_tokens("") == 0

    def test_natural_language(self):
        """Natural language should use ~4.5 chars/token."""
        text = "The quick brown fox jumps over the lazy dog " * 100
        tokens = _estimate_tokens(text)
        # ~4400 chars / 4.5 ≈ 978
        assert 800 < tokens < 1200

    def test_code_like_text(self):
        """Code with many symbols should use ~3.5 chars/token."""
        text = 'result = re.findall(r"Error: (.*)", log)\n' * 100
        tokens = _estimate_tokens(text)
        # ~4100 chars / 3.5 ≈ 1171
        assert 900 < tokens < 1400

    def test_larger_text_scales_linearly(self):
        small = _estimate_tokens("hello world " * 10)
        large = _estimate_tokens("hello world " * 100)
        # Should scale roughly 10x
        assert 8 * small < large < 12 * small


class TestFilteredContextValidation:
    """Tests for the fix where invalid JSON CONTEXT falls back to None."""

    def test_parse_valid_context_json(self):
        agent = RecursiveDeploymentAgent()
        response = (
            "ACTION: recursive_call\n"
            'SUBTASK: Analyze ports\n'
            'CONTEXT: {"ports": [8080, 8081]}'
        )
        parsed = agent._parse_rlm_response(response)
        assert parsed["action"] == ActionType.RECURSIVE_CALL
        assert parsed["context"] == {"ports": [8080, 8081]}

    def test_parse_invalid_context_json_returns_none(self):
        agent = RecursiveDeploymentAgent()
        response = (
            "ACTION: recursive_call\n"
            "SUBTASK: Analyze ports\n"
            "CONTEXT: not valid json at all"
        )
        parsed = agent._parse_rlm_response(response)
        assert parsed["action"] == ActionType.RECURSIVE_CALL
        assert parsed["context"] is None

    def test_parse_empty_context_dict_returns_none(self):
        agent = RecursiveDeploymentAgent()
        response = (
            "ACTION: recursive_call\n"
            "SUBTASK: Analyze ports\n"
            "CONTEXT: {}"
        )
        parsed = agent._parse_rlm_response(response)
        assert parsed["context"] is None

    def test_recursive_call_with_none_context_reports_zero_savings(self):
        """When filtered_context is None, tokens_saved should be 0."""
        context = RLMContext(error_log="x" * 10000)
        env = RLMEnvironment(context)

        def mock_llm(prompt):
            return "response"

        env.recursive_call("subtask", filtered_context=None, llm_function=mock_llm)
        assert env.call_history[0].tokens_saved == 0


class TestConsecutiveExploreCounter:
    """Tests for the consecutive non-answer forcing logic."""

    def test_consecutive_explore_triggers_nudge(self):
        """After 5 consecutive explore steps, the prompt should include a nudge."""
        call_count = 0
        prompts_received = []

        def mock_llm(prompt):
            nonlocal call_count
            prompts_received.append(prompt)
            call_count += 1
            if call_count <= 6:
                return "ACTION: execute_code\nDESCRIPTION: test\nCODE:\nresult = 'ok'"
            return "ACTION: final_answer\nANSWER: done"

        agent = RecursiveDeploymentAgent()
        agent._system_prompt = "test system"
        agent._call_llm = mock_llm

        context = RLMContext()
        agent.run_task("test task", context, "/tmp")

        # The 6th prompt (after 5 consecutive explores) should contain the nudge
        nudge_found = any(
            "consecutive steps" in p for p in prompts_received
        )
        assert nudge_found

    def test_final_answer_resets_counter(self):
        """A final_answer resets the consecutive counter (no nudge on next explore)."""
        agent = RecursiveDeploymentAgent()

        # Simulate: parse response resets counter when action is FINAL_ANSWER
        response = "ACTION: final_answer\nANSWER: done"
        parsed = agent._parse_rlm_response(response)
        assert parsed["action"] == ActionType.FINAL_ANSWER


class TestConversationHistory:
    """Tests for stateful conversation history in the RLM loop."""

    def test_run_task_accumulates_history(self):
        """Each iteration's messages include all prior user/assistant turns."""
        import unittest.mock as mock

        call_count = 0
        messages_snapshots = []

        def fake_completion(**kwargs):
            nonlocal call_count
            # Snapshot the messages list *before* _call_llm appends assistant
            messages_snapshots.append(
                [m.copy() for m in kwargs["messages"]]
            )
            call_count += 1
            if call_count < 3:
                content = "ACTION: execute_code\nDESCRIPTION: test\nCODE:\nresult = 'ok'"
            else:
                content = "ACTION: final_answer\nANSWER: done"
            resp = mock.MagicMock()
            resp.choices = [mock.MagicMock()]
            resp.choices[0].message.content = content
            resp.usage = None
            return resp

        agent = RecursiveDeploymentAgent()
        context = RLMContext()

        with mock.patch("litellm.completion", side_effect=fake_completion):
            agent.run_task("test task", context, "/tmp")

        assert len(messages_snapshots) >= 3

        # First call: system + user (the initial task prompt)
        first = messages_snapshots[0]
        assert first[0]["role"] == "system"
        assert len([m for m in first if m["role"] == "user"]) == 1

        # Second call: system + user1 + assistant1 + user2
        second = messages_snapshots[1]
        assert second[0]["role"] == "system"
        roles = [m["role"] for m in second]
        assert roles.count("system") == 1
        assert roles.count("user") == 2
        assert roles.count("assistant") == 1

        # Third call: system + user1 + assistant1 + user2 + assistant2 + user3
        third = messages_snapshots[2]
        roles = [m["role"] for m in third]
        assert roles.count("system") == 1
        assert roles.count("user") == 3
        assert roles.count("assistant") == 2

    def test_call_llm_appends_to_messages(self):
        """_call_llm appends both the user prompt and the assistant response."""
        import unittest.mock as mock

        agent = RecursiveDeploymentAgent()
        agent._messages = [{"role": "system", "content": "sys"}]

        mock_response = mock.MagicMock()
        mock_response.choices = [mock.MagicMock()]
        mock_response.choices[0].message.content = "assistant reply"
        mock_response.usage = None

        with mock.patch("litellm.completion", return_value=mock_response):
            result = agent._call_llm("hello user")

        assert result == "assistant reply"
        assert len(agent._messages) == 3
        assert agent._messages[1] == {"role": "user", "content": "hello user"}
        assert agent._messages[2] == {"role": "assistant", "content": "assistant reply"}

    def test_call_llm_appends_on_failure(self):
        """On LLM failure, both user and fallback assistant messages are appended."""
        import unittest.mock as mock

        agent = RecursiveDeploymentAgent()
        agent._messages = [{"role": "system", "content": "sys"}]

        with mock.patch("litellm.completion", side_effect=RuntimeError("boom")):
            result = agent._call_llm("hello")

        assert "LLM call failed" in result
        assert len(agent._messages) == 3
        assert agent._messages[1]["role"] == "user"
        assert agent._messages[2]["role"] == "assistant"
        assert "boom" in agent._messages[2]["content"]


class TestAutoValidateDeploySh:
    """Tests for automatic deploy.sh validation before final_answer."""

    def test_auto_validate_catches_missing_refs(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("#!/bin/bash\nchmod +x nonexistent.sh\n")

        agent = RecursiveDeploymentAgent()
        result = agent._auto_validate_deploy_sh(str(tmp_path), "Fix applied.")

        assert "AUTO-VALIDATION WARNING" in result
        assert "MISSING" in result
        assert "nonexistent.sh" in result

    def test_auto_validate_passes_when_valid(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("#!/bin/bash\necho hello\n")

        agent = RecursiveDeploymentAgent()
        result = agent._auto_validate_deploy_sh(str(tmp_path), "Fix applied.")

        assert result == "Fix applied."
        assert "WARNING" not in result

    def test_auto_validate_no_deploy_sh(self, tmp_path):
        """When deploy.sh doesn't exist, answer passes through unchanged."""
        agent = RecursiveDeploymentAgent()
        result = agent._auto_validate_deploy_sh(str(tmp_path), "No script needed.")

        assert result == "No script needed."


class TestRLMDeployerFixErrorSignature:
    """Tests for the RLMDeployerFixError DSPy signature."""

    def test_signature_registered(self):
        from app_operator.dspy_integration.signatures import SIGNATURES

        assert "rlm_deployer_fix_error" in SIGNATURES

    def test_signature_has_required_fields(self):
        from app_operator.dspy_integration.signatures import (
            RLMDeployerFixErrorSignature,
        )

        sig = RLMDeployerFixErrorSignature
        # DSPy stores fields in model_fields
        field_names = set(sig.model_fields.keys())
        assert "repo_path" in field_names
        assert "available_variables" in field_names
        assert "error_log_size" in field_names
        assert "attempt" in field_names
        assert "max_attempts" in field_names
        assert "has_original_script" in field_names
        assert "rendered_prompt" in field_names


class TestExtractRLMStatisticsFromTrajectory:
    """Tests for extracting RLM stats from trajectory dicts."""

    def test_extract_from_rlm_trajectory(self):
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {"content": "[RLM execute_code at depth 0]\nInput: test"},
                        {"content": "[RLM execute_code at depth 0]\nInput: test2"},
                        {"content": "[RLM recursive_call at depth 1]\nInput: sub"},
                        {
                            "content": 'RLM Statistics: {"total_calls": 3, '
                            '"code_executions": 2, "recursive_calls": 1, '
                            '"total_tokens_saved": 5000, "max_depth_reached": 1}'
                        },
                    ]
                }
            ]
        }

        stats = extract_rlm_statistics_from_trajectory(trajectory)

        assert stats["total_calls"] == 3
        assert stats["code_executions"] == 2
        assert stats["recursive_calls"] == 1
        assert stats["total_tokens_saved"] == 5000

    def test_extract_from_non_rlm_trajectory(self):
        trajectory = {
            "deployment": [
                {
                    "messages": [
                        {"content": "regular deployment message"},
                    ]
                }
            ]
        }

        stats = extract_rlm_statistics_from_trajectory(trajectory)

        assert stats["total_calls"] == 0
        assert stats["code_executions"] == 0

    def test_extract_from_empty_trajectory(self):
        stats = extract_rlm_statistics_from_trajectory({})
        assert stats["total_calls"] == 0


class TestIsolatedRecursiveCall:
    """Tests for _call_llm_isolated and recursive call isolation."""

    def test_isolated_call_uses_fresh_messages(self):
        """_call_llm_isolated builds a fresh messages list, not self._messages."""
        import unittest.mock as mock

        captured_kwargs = []

        def capture(**kwargs):
            captured_kwargs.append(kwargs)
            return mock.MagicMock(
                choices=[mock.MagicMock(message=mock.MagicMock(content="sub-response"))],
                usage=None,
            )

        agent = RecursiveDeploymentAgent()
        # Simulate existing conversation history
        agent._messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first prompt"},
            {"role": "assistant", "content": "first response"},
        ]
        agent._token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        with mock.patch("litellm.completion", side_effect=capture):
            agent._call_llm_isolated(
                "Analyse the error", {"error_log": "port 8080 in use"}
            )

        # The isolated call should NOT use self._messages
        assert len(captured_kwargs) == 1
        messages = captured_kwargs[0]["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Analyse the error" in messages[1]["content"]
        assert "port 8080" in messages[1]["content"]

        # self._messages should not have been modified
        assert len(agent._messages) == 3

    def test_isolated_call_without_context(self):
        """_call_llm_isolated works when filtered_context is None."""
        import unittest.mock as mock

        captured_kwargs = []

        def capture(**kwargs):
            captured_kwargs.append(kwargs)
            return mock.MagicMock(
                choices=[mock.MagicMock(message=mock.MagicMock(content="response"))],
                usage=None,
            )

        agent = RecursiveDeploymentAgent()
        agent._messages = [{"role": "system", "content": "sys"}]
        agent._token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        with mock.patch("litellm.completion", side_effect=capture):
            agent._call_llm_isolated("Just a question", None)

        messages = captured_kwargs[0]["messages"]
        assert len(messages) == 2
        # Without context, user prompt is just the sub_prompt
        assert messages[1]["content"] == "Just a question"

    def test_recursive_call_in_run_task_does_not_grow_history(self):
        """recursive_call inside run_task uses isolated calls, not _call_llm."""
        import unittest.mock as mock

        main_call_count = 0
        main_messages_lengths = []

        def fake_completion(**kwargs):
            nonlocal main_call_count
            messages = kwargs["messages"]
            main_messages_lengths.append(len(messages))
            main_call_count += 1

            if main_call_count == 1:
                # First call: request a recursive call
                content = (
                    "ACTION: recursive_call\n"
                    "SUBTASK: Analyse the error\n"
                    'CONTEXT: {"error_log": "test error"}'
                )
            elif main_call_count == 2:
                # This is the isolated subagent call (2 messages: system + user)
                content = "Sub-analysis result"
            elif main_call_count == 3:
                # Back to main loop — receives recursive result
                content = "ACTION: final_answer\nANSWER: done"
            else:
                content = "ACTION: final_answer\nANSWER: done"

            resp = mock.MagicMock()
            resp.choices = [mock.MagicMock()]
            resp.choices[0].message.content = content
            resp.usage = None
            return resp

        agent = RecursiveDeploymentAgent()
        context = RLMContext()

        with mock.patch("litellm.completion", side_effect=fake_completion):
            agent.run_task("test task", context, "/tmp")

        # The isolated subagent call (call #2) should have only 2 messages
        # (system + user), not the growing main conversation.
        assert main_messages_lengths[1] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
