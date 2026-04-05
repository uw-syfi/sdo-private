"""Unit tests for causal chain support in the crucible agent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from sregym_agents.crucible.orchestrator import (
    StageLoopResult,
    _extract_benchmark_reasoning,
    _init_mitigation_file,
    _replace_hypothesis_placeholder,
    _run_recovery_diagnosis,
    _run_recovery_mitigation,
)
from sregym_agents.crucible.tools import SharedFile, SharedState, SRESubmission

# ---------------------------------------------------------------------------
# SRESubmission.causal_chain field
# ---------------------------------------------------------------------------


class TestSRESubmissionCausalChain:
    def test_defaults_to_empty_string(self):
        sub = SRESubmission(answer="x", justification="y")
        assert sub.causal_chain == ""

    def test_accepts_causal_chain(self):
        sub = SRESubmission(
            answer="x",
            justification="y",
            causal_chain="A → B → C",
        )
        assert sub.causal_chain == "A → B → C"


# ---------------------------------------------------------------------------
# SRESubmission.reflection field
# ---------------------------------------------------------------------------


class TestSRESubmissionReflection:
    def test_defaults_to_empty_string(self):
        sub = SRESubmission(answer="x", justification="y")
        assert sub.reflection == ""

    def test_accepts_reflection(self):
        sub = SRESubmission(
            answer="x",
            justification="y",
            reflection="Agent missed the downstream service logs.",
        )
        assert sub.reflection == "Agent missed the downstream service logs."


# ---------------------------------------------------------------------------
# SharedState.answer_causal_chain field
# ---------------------------------------------------------------------------


class TestSharedStateCausalChain:
    def test_defaults_to_none(self):
        state = SharedState()
        assert state.answer_causal_chain is None

    def test_can_be_set(self):
        state = SharedState()
        state.answer_causal_chain = "field → mechanism → symptom"
        assert state.answer_causal_chain == "field → mechanism → symptom"


# ---------------------------------------------------------------------------
# SharedState.answer_reflection field
# ---------------------------------------------------------------------------


class TestSharedStateReflection:
    def test_defaults_to_none(self):
        state = SharedState()
        assert state.answer_reflection is None

    def test_can_be_set(self):
        state = SharedState()
        state.answer_reflection = "Agent focused on wrong service"
        assert state.answer_reflection == "Agent focused on wrong service"


# ---------------------------------------------------------------------------
# _replace_hypothesis_placeholder — causal chain handling
# ---------------------------------------------------------------------------


class TestReplaceHypothesisPlaceholderCausalChain:
    def test_includes_causal_chain_when_provided(self, tmp_path: Path):
        f = tmp_path / "shared.md"
        f.write_text("# Header\n\n### Iteration 1 — Agent Hypothesis\n[Submitted — pending judge review]\n")
        _replace_hypothesis_placeholder(f, 1, "diag", "just", causal_chain="A → B → C")
        content = f.read_text()
        assert "**Causal Chain**: A → B → C" in content
        assert "[Submitted — pending judge review]" not in content

    def test_omits_causal_chain_when_empty(self, tmp_path: Path):
        f = tmp_path / "shared.md"
        f.write_text("# Header\n\n### Iteration 1 — Agent Hypothesis\n[Submitted — pending judge review]\n")
        _replace_hypothesis_placeholder(f, 1, "diag", "just", causal_chain="")
        content = f.read_text()
        assert "**Causal Chain**" not in content
        assert "**Diagnosis**: diag" in content

    def test_backward_compatible_without_kwarg(self, tmp_path: Path):
        f = tmp_path / "shared.md"
        f.write_text("# Header\n\n### Iteration 1 — Agent Hypothesis\n[Submitted — pending judge review]\n")
        _replace_hypothesis_placeholder(f, 1, "diag", "just")
        content = f.read_text()
        assert "**Causal Chain**" not in content
        assert "**Diagnosis**: diag" in content


# ---------------------------------------------------------------------------
# StageLoopResult.agent_causal_chain field
# ---------------------------------------------------------------------------


class TestStageLoopResultCausalChain:
    def test_defaults_to_empty(self):
        r = StageLoopResult(approved=True, usage_by_role={})
        assert r.agent_causal_chain == ""

    def test_carries_causal_chain(self):
        r = StageLoopResult(
            approved=True,
            usage_by_role={},
            agent_causal_chain="X → Y → Z",
        )
        assert r.agent_causal_chain == "X → Y → Z"


# ---------------------------------------------------------------------------
# StageLoopResult.agent_reflection field
# ---------------------------------------------------------------------------


class TestStageLoopResultReflection:
    def test_defaults_to_empty(self):
        r = StageLoopResult(approved=True, usage_by_role={})
        assert r.agent_reflection == ""

    def test_carries_reflection(self):
        r = StageLoopResult(
            approved=True,
            usage_by_role={},
            agent_reflection="Agent missed downstream logs",
        )
        assert r.agent_reflection == "Agent missed downstream logs"


# ---------------------------------------------------------------------------
# _init_mitigation_file — causal chain handling
# ---------------------------------------------------------------------------


class TestInitMitigationFileCausalChain:
    def test_includes_causal_chain_on_success(self, tmp_path: Path):
        f = tmp_path / "mit.md"
        _init_mitigation_file(
            f,
            {"app_name": "app", "namespace": "ns"},
            "success: True",
            "diagnosis",
            "justification",
            diagnosis_causal_chain="A → B → C",
        )
        content = f.read_text()
        assert "**Causal Chain**: A → B → C" in content

    def test_omits_causal_chain_when_empty(self, tmp_path: Path):
        f = tmp_path / "mit.md"
        _init_mitigation_file(
            f,
            {"app_name": "app", "namespace": "ns"},
            "success: True",
            "diagnosis",
            "justification",
            diagnosis_causal_chain="",
        )
        content = f.read_text()
        assert "**Causal Chain**" not in content

    def test_no_diagnosis_section_when_benchmark_failed(self, tmp_path: Path):
        f = tmp_path / "mit.md"
        _init_mitigation_file(
            f,
            {"app_name": "app", "namespace": "ns"},
            "success: False",
            "diagnosis",
            "justification",
            diagnosis_causal_chain="A → B → C",
        )
        content = f.read_text()
        assert "**Causal Chain**" not in content
        assert "Agent Diagnosis" not in content


# ---------------------------------------------------------------------------
# _extract_benchmark_reasoning
# ---------------------------------------------------------------------------


class TestExtractBenchmarkReasoning:
    def test_extracts_reasoning_from_oracle_tags(self):
        block = (
            "\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            '<oracle>\n{"Diagnosis": {"reasoning": "The real root cause is X", '
            '"success": false}}\n</oracle>\n</benchmark_result>\n'
        )
        assert _extract_benchmark_reasoning(block) == "The real root cause is X"

    def test_returns_empty_when_no_oracle_tags(self):
        block = "\n<benchmark_result>\nsuccess: False\nmessage: error\n</benchmark_result>\n"
        assert _extract_benchmark_reasoning(block) == ""

    def test_returns_empty_when_no_reasoning_field(self):
        block = (
            "\n<benchmark_result>\nsuccess: False\n"
            '<oracle>\n{"Diagnosis": {"success": false}}\n</oracle>\n</benchmark_result>\n'
        )
        assert _extract_benchmark_reasoning(block) == ""

    def test_handles_real_benchmark_output(self):
        oracle = {
            "Diagnosis": {
                "judgment": "False",
                "reasoning": (
                    "The expected root cause is a service-level issue where the reservation service has been deleted."
                ),
                "success": False,
                "accuracy": 0.0,
            },
            "TTL": 210.0,
        }
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: Benchmark rejected.\n"
            f"<oracle>\n{json.dumps(oracle, indent=2)}\n</oracle>\n</benchmark_result>\n"
        )
        result = _extract_benchmark_reasoning(block)
        assert "reservation service has been deleted" in result

    def test_ignores_braces_in_message_line(self):
        """The old bug: Python dict repr in message caused find('{') to match wrong spot."""
        oracle = {
            "Diagnosis": {
                "reasoning": "DNS failure caused by NXDOMAIN template",
                "success": False,
            },
        }
        block = (
            "\n<benchmark_result>\nsuccess: False\n"
            "message: Benchmark rejected submission for stage 'Diagnosis'.\n"
            f"<oracle>\n{json.dumps(oracle, indent=2)}\n</oracle>\n</benchmark_result>\n"
        )
        assert _extract_benchmark_reasoning(block) == "DNS failure caused by NXDOMAIN template"

    def test_returns_empty_when_malformed_json_in_oracle(self):
        block = (
            "\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            "<oracle>\nnot valid json\n</oracle>\n</benchmark_result>\n"
        )
        assert _extract_benchmark_reasoning(block) == ""


# ---------------------------------------------------------------------------
# _run_recovery_diagnosis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunRecoveryDiagnosis:
    async def test_skips_when_no_reasoning(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")
        result = await _run_recovery_diagnosis(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong answer",
            benchmark_block="<benchmark_result>\nsuccess: False\nmessage: error\n</benchmark_result>",
        )
        assert result is None

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_appends_recovery_to_shared_file(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        async def fake_arun(prompt, run_ctx=None):
            # Simulate agent setting state
            deps = mock_cls.call_args[0][1]  # deps is 2nd positional arg
            deps.state.submitted = True
            deps.state.answer = "correct root cause"
            deps.state.answer_justification = "evidence"
            deps.state.answer_causal_chain = "field → mechanism → symptom"
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Diagnosis": {"reasoning": "The real root cause is X", "success": False}}
        oracle_text = json.dumps(oracle)
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{oracle_text}\n</oracle>\n</benchmark_result>\n"
        )

        result = await _run_recovery_diagnosis(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong answer",
            benchmark_block=block,
        )

        assert result is not None
        assert result.answer == "correct root cause"
        assert result.causal_chain == "field → mechanism → symptom"

        content = shared.read_text()
        assert "### Recovery Diagnosis" in content
        assert "**Causal Chain**: field → mechanism → symptom" in content

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_appends_reflection_to_shared_file(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        async def fake_arun(prompt, run_ctx=None):
            deps = mock_cls.call_args[0][1]
            deps.state.submitted = True
            deps.state.answer = "correct root cause"
            deps.state.answer_justification = "evidence"
            deps.state.answer_causal_chain = "field → mechanism → symptom"
            deps.state.answer_reflection = "Agent focused on nginx logs instead of tracing downstream."
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Diagnosis": {"reasoning": "The real root cause is X", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )

        result = await _run_recovery_diagnosis(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong answer",
            benchmark_block=block,
            original_justification="nginx logs showed connection refused",
            original_causal_chain="nginx → compose.lua → localhost:8080",
        )

        assert result is not None
        assert result.reflection == "Agent focused on nginx logs instead of tracing downstream."

        content = shared.read_text()
        assert "**Agent Reflection**: Agent focused on nginx logs instead of tracing downstream." in content

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_omits_reflection_when_empty(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        async def fake_arun(prompt, run_ctx=None):
            deps = mock_cls.call_args[0][1]
            deps.state.submitted = True
            deps.state.answer = "correct root cause"
            deps.state.answer_justification = "evidence"
            deps.state.answer_causal_chain = "field → mechanism → symptom"
            # No reflection set
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Diagnosis": {"reasoning": "The real root cause is X", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )

        result = await _run_recovery_diagnosis(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong answer",
            benchmark_block=block,
        )

        assert result is not None
        assert result.reflection == ""
        content = shared.read_text()
        assert "**Agent Reflection**" not in content

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_passes_original_context_to_prompt(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        captured_prompts = {}

        async def fake_arun(prompt, run_ctx=None):
            captured_prompts["user"] = prompt
            deps = mock_cls.call_args[0][1]
            deps.state.submitted = True
            deps.state.answer = "root cause"
            deps.state.answer_justification = "evidence"
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Diagnosis": {"reasoning": "The real root cause is X", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )

        await _run_recovery_diagnosis(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong answer",
            benchmark_block=block,
            original_justification="nginx logs showed errors",
            original_causal_chain="nginx → compose.lua → localhost",
        )

        user_prompt = captured_prompts["user"]
        assert "nginx logs showed errors" in user_prompt
        assert "nginx → compose.lua → localhost" in user_prompt

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_returns_none_when_agent_does_not_submit(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        async def fake_arun(prompt, run_ctx=None):
            # Agent doesn't set submitted
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Diagnosis": {"reasoning": "The real root cause is X", "success": False}}
        oracle_text = json.dumps(oracle)
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{oracle_text}\n</oracle>\n</benchmark_result>\n"
        )

        result = await _run_recovery_diagnosis(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong answer",
            benchmark_block=block,
        )

        assert result is None


# ---------------------------------------------------------------------------
# _extract_benchmark_reasoning — mitigation stage
# ---------------------------------------------------------------------------


class TestExtractBenchmarkMitigationReasoning:
    def test_extracts_mitigation_reasoning(self):
        oracle = {"Mitigation": {"reasoning": "Patch the ConfigMap value", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )
        assert _extract_benchmark_reasoning(block, stage="mitigation") == "Patch the ConfigMap value"

    def test_returns_empty_when_no_mitigation_key(self):
        oracle = {"Diagnosis": {"reasoning": "root cause X", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )
        assert _extract_benchmark_reasoning(block, stage="mitigation") == ""

    def test_diagnosis_stage_still_works(self):
        oracle = {"Diagnosis": {"reasoning": "root cause X", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )
        assert _extract_benchmark_reasoning(block, stage="diagnosis") == "root cause X"

    def test_default_stage_is_diagnosis(self):
        oracle = {"Diagnosis": {"reasoning": "root cause X", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )
        assert _extract_benchmark_reasoning(block) == "root cause X"


# ---------------------------------------------------------------------------
# _run_recovery_mitigation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunRecoveryMitigation:
    async def test_skips_when_no_reasoning(self, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")
        result = await _run_recovery_mitigation(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong fix",
            benchmark_block="<benchmark_result>\nsuccess: False\nmessage: error\n</benchmark_result>",
        )
        assert result is None

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_appends_recovery_to_shared_file(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        async def fake_arun(prompt, run_ctx=None):
            deps = mock_cls.call_args[0][1]
            deps.state.submitted = True
            deps.state.answer = "patch ConfigMap X"
            deps.state.answer_justification = "correct value restores service"
            deps.state.answer_reflection = "Agent fixed the wrong field."
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Mitigation": {"reasoning": "Patch ConfigMap X field Y", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )

        result = await _run_recovery_mitigation(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong fix",
            benchmark_block=block,
            diagnosis_answer="ConfigMap X has wrong value",
        )

        assert result is not None
        assert result.answer == "patch ConfigMap X"
        assert result.reflection == "Agent fixed the wrong field."

        content = shared.read_text()
        assert "### Recovery Mitigation" in content
        assert "**Mitigation**: patch ConfigMap X" in content
        assert "**Agent Reflection**: Agent fixed the wrong field." in content

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_omits_reflection_when_empty(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        async def fake_arun(prompt, run_ctx=None):
            deps = mock_cls.call_args[0][1]
            deps.state.submitted = True
            deps.state.answer = "correct fix"
            deps.state.answer_justification = "evidence"
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Mitigation": {"reasoning": "The correct fix is Y", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )

        result = await _run_recovery_mitigation(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong fix",
            benchmark_block=block,
        )

        assert result is not None
        assert result.reflection == ""
        content = shared.read_text()
        assert "**Agent Reflection**" not in content

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_passes_context_to_prompt(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        captured_prompts = {}

        async def fake_arun(prompt, run_ctx=None):
            captured_prompts["user"] = prompt
            deps = mock_cls.call_args[0][1]
            deps.state.submitted = True
            deps.state.answer = "correct fix"
            deps.state.answer_justification = "evidence"
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Mitigation": {"reasoning": "Correct approach is Z", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )

        await _run_recovery_mitigation(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong fix",
            benchmark_block=block,
            original_justification="applied kubectl patch",
            diagnosis_answer="ConfigMap X has wrong value",
        )

        user_prompt = captured_prompts["user"]
        assert "applied kubectl patch" in user_prompt
        assert "ConfigMap X has wrong value" in user_prompt

    @patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent")
    async def test_returns_none_when_agent_does_not_submit(self, mock_cls, tmp_path: Path):
        shared = tmp_path / "shared.md"
        shared.write_text("# Header\n")

        async def fake_arun(prompt, run_ctx=None):
            return "", {}

        mock_instance = mock_cls.return_value
        mock_instance.arun = AsyncMock(side_effect=fake_arun)

        oracle = {"Mitigation": {"reasoning": "The correct fix is Y", "success": False}}
        block = (
            f"\n<benchmark_result>\nsuccess: False\nmessage: rejected\n"
            f"<oracle>\n{json.dumps(oracle)}\n</oracle>\n</benchmark_result>\n"
        )

        result = await _run_recovery_mitigation(
            model="test-model",
            app_info={"app_name": "app", "namespace": "ns"},
            shared_file=SharedFile(shared),
            original_answer="wrong fix",
            benchmark_block=block,
        )

        assert result is None
