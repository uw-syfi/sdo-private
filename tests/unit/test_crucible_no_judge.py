"""Unit tests for the enable_judge=False feature in the Crucible orchestrator."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.config import CrucibleConfig
from sregym_agents.crucible.tools import SharedFile, SRESubmission

# ---------------------------------------------------------------------------
# _run_stage_loop with enable_judge=False
# ---------------------------------------------------------------------------


def _sre_model(answer: str, justification: str) -> TestModel:
    """Return a TestModel that responds with a valid SRESubmission."""
    return TestModel(custom_output_args=SRESubmission(answer=answer, justification=justification).model_dump())


@pytest.fixture
def shared_file(tmp_path: Path) -> SharedFile:
    f = tmp_path / "session.md"
    f.write_text("# Session\n")
    return SharedFile(f)


def test_no_judge_submits_directly_and_returns_approved(shared_file: SharedFile) -> None:
    sre_answer = "CPU throttling on service Z"
    sre_justification = "High CPU usage observed"

    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with (
        patch("sregym_agents.crucible.orchestrator.CrucibleJudgeAgent") as mock_judge_cls,
        patch(
            "sregym_agents.crucible.orchestrator._submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(
                True,
                "Benchmark accepted submission for stage 'Diagnosis'.",
                {"Diagnosis": {"success": True}},
            ),
        ),
    ):
        from sregym_agents.crucible.orchestrator import _run_stage_loop

        result = asyncio.run(
            _run_stage_loop(
                model=_sre_model(sre_answer, sre_justification),
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=3,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                crucible_config=CrucibleConfig(enable_judge=False),
            )
        )

    assert result.approved is True
    mock_judge_cls.assert_not_called()

    content = shared_file.read_text()
    assert "<benchmark_result>" in content
    assert "APPROVED (no-judge mode" in content
    assert "[Submitted — pending judge review]" not in content
    assert sre_answer in content
    assert sre_justification in content


def test_no_judge_writes_benchmark_error_on_exception(shared_file: SharedFile) -> None:
    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with (
        patch("sregym_agents.crucible.orchestrator.CrucibleJudgeAgent"),
        patch(
            "sregym_agents.crucible.orchestrator._submit_to_benchmark",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ),
    ):
        from sregym_agents.crucible.orchestrator import _run_stage_loop

        result = asyncio.run(
            _run_stage_loop(
                model=_sre_model("some answer", "some justification"),
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=1,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                crucible_config=CrucibleConfig(enable_judge=False),
            )
        )

    assert result.approved is True
    content = shared_file.read_text()
    assert "Error submitting to benchmark" in content
    assert "connection refused" in content


def test_with_judge_calls_judge_agent(shared_file: SharedFile) -> None:
    """When enable_judge=True (default), the judge agent runs and its verdict is used."""

    def fake_sre_constructor(model, deps, trajectory_path=None, system_prompt_override=None):
        mock = MagicMock()

        def fake_run(prompt, run_ctx=None):
            deps.state.answer = "an answer"
            return None, {"input_tokens": 5, "output_tokens": 3, "cached_input_tokens": 0}

        mock.arun = AsyncMock(side_effect=fake_run)
        return mock

    def fake_judge_constructor(model, deps, trajectory_path=None):
        mock = MagicMock()

        def fake_run(prompt, run_ctx=None):
            deps.state.verdict = "APPROVED"
            deps.state.submitted = True
            return None, {"input_tokens": 5, "output_tokens": 3, "cached_input_tokens": 0}

        mock.arun = AsyncMock(side_effect=fake_run)
        return mock

    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with (
        patch("sregym_agents.crucible.orchestrator.CrucibleSREAgent", side_effect=fake_sre_constructor),
        patch("sregym_agents.crucible.orchestrator.CrucibleJudgeAgent", side_effect=fake_judge_constructor),
        patch("sregym_agents.crucible.orchestrator._submit_to_benchmark") as mock_submit,
    ):
        from sregym_agents.crucible.orchestrator import _run_stage_loop

        result = asyncio.run(
            _run_stage_loop(
                model=TestModel(),
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=3,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                crucible_config=CrucibleConfig(enable_judge=True),
            )
        )

    assert result.approved is True
    mock_submit.assert_not_called()
