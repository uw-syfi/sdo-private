# pyright: reportPrivateUsage=false
"""Unit tests for the enable_judge=False feature in the Crucible orchestrator."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from benchmarks.sregym.agents.crucible._prompts import PromptRenderer
from benchmarks.sregym.agents.crucible.agents.base import AgentResult
from benchmarks.sregym.agents.crucible.config import CrucibleConfig
from benchmarks.sregym.agents.crucible.tools import SharedFile, SharedState, SRESubmission
from libs.pydantic_agent import UsageCollector

# ---------------------------------------------------------------------------
# _run_stage_loop with enable_judge=False
# ---------------------------------------------------------------------------


def _make_mock_sre(answer: str, justification: str) -> MagicMock:
    """Return a mock SREAgent whose run() returns a completed AgentResult.

    Also writes the hypothesis placeholder to the shared file (as the real
    SREAgent.run() does) so that _replace_hypothesis_placeholder can reveal it.
    """
    mock = MagicMock()
    mock._config = MagicMock()
    mock._config.stage_outputs_file = None

    async def fake_run(**kwargs: Any):
        state = SharedState()
        state.answer = answer
        state.answer_justification = justification
        # Simulate what SREAgent.run() does: write the hypothesis placeholder
        shared_file = kwargs.get("shared_file")
        iteration = kwargs.get("iteration", 1)
        stage = kwargs.get("stage", "diagnosis")
        if shared_file is not None:
            if stage == "diagnosis":
                entry = (
                    f"\n### Iteration {iteration} \u2014 Agent Hypothesis\n[Submitted \u2014 pending judge review]\n"
                )
            else:
                entry = (
                    f"\n### Iteration {iteration} \u2014 Agent Strategy\n"
                    f"**Mitigation**: {answer}\n"
                    f"**Justification**: {justification}\n"
                )
            shared_file.append(entry)
        result = AgentResult(
            output=SRESubmission(answer=answer, justification=justification),
            completed=True,
        )
        result.state = state  # type: ignore[attr-defined]
        return result

    mock.run = AsyncMock(side_effect=fake_run)
    return mock


@pytest.fixture
def shared_file(tmp_path: Path) -> SharedFile:
    f = tmp_path / "session.md"
    f.write_text("# Session\n")
    return SharedFile(f)


def test_no_judge_submits_directly_and_returns_approved(shared_file: SharedFile) -> None:
    sre_answer = "CPU throttling on service Z"
    sre_justification = "High CPU usage observed"

    mock_sre = _make_mock_sre(sre_answer, sre_justification)
    mock_judge = MagicMock()
    mock_judge.run = AsyncMock()

    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with patch(
        "benchmarks.sregym.agents.crucible.orchestrator.submit_to_benchmark",
        new_callable=AsyncMock,
        return_value=(
            True,
            "Benchmark accepted submission for stage 'Diagnosis'.",
            {"Diagnosis": {"success": True}},
        ),
    ):
        from benchmarks.sregym.agents.crucible.orchestrator import _run_stage_loop

        result = asyncio.run(
            _run_stage_loop(
                sre_agent=mock_sre,
                judge_agent=mock_judge,
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=3,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                usage_collector=UsageCollector(),
                crucible_config=CrucibleConfig(enable_judge=False),
            )
        )

    assert result.approved is True
    mock_judge.run.assert_not_called()

    content = shared_file.read_text()
    assert "<benchmark_result>" in content
    assert "APPROVED (no-judge mode" in content
    assert "[Submitted \u2014 pending judge review]" not in content
    assert sre_answer in content
    assert sre_justification in content


def test_no_judge_writes_benchmark_error_on_exception(shared_file: SharedFile) -> None:
    mock_sre = _make_mock_sre("some answer", "some justification")
    mock_judge = MagicMock()

    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with patch(
        "benchmarks.sregym.agents.crucible.orchestrator.submit_to_benchmark",
        new_callable=AsyncMock,
        side_effect=RuntimeError("connection refused"),
    ):
        from benchmarks.sregym.agents.crucible.orchestrator import _run_stage_loop

        result = asyncio.run(
            _run_stage_loop(
                sre_agent=mock_sre,
                judge_agent=mock_judge,
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=1,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                usage_collector=UsageCollector(),
                crucible_config=CrucibleConfig(enable_judge=False),
            )
        )

    assert result.approved is True
    content = shared_file.read_text()
    assert "Error submitting to benchmark" in content
    assert "connection refused" in content


def test_with_judge_calls_judge_agent(shared_file: SharedFile) -> None:
    """When enable_judge=True (default), the judge agent runs and its verdict is used."""

    mock_sre = MagicMock()
    mock_sre._config = MagicMock()
    mock_sre._config.stage_outputs_file = None

    async def fake_sre_run(**kwargs: Any):
        state = SharedState()
        state.answer = "an answer"
        result = AgentResult(
            output=SRESubmission(answer="an answer", justification=""),
            completed=True,
        )
        result.state = state  # type: ignore[attr-defined]
        return result

    mock_sre.run = AsyncMock(side_effect=fake_sre_run)

    async def fake_judge_run(**kwargs: Any):
        state = SharedState()
        state.verdict = "APPROVED"
        state.submitted = True
        result = AgentResult(output="approved", completed=True)
        result.state = state  # type: ignore[attr-defined]
        return result

    mock_judge = MagicMock()
    mock_judge.run = AsyncMock(side_effect=fake_judge_run)

    mock_renderer = MagicMock(spec=PromptRenderer)
    mock_renderer.render.return_value = "rendered"

    with patch("benchmarks.sregym.agents.crucible.orchestrator.submit_to_benchmark") as mock_submit:
        from benchmarks.sregym.agents.crucible.orchestrator import _run_stage_loop

        result = asyncio.run(
            _run_stage_loop(
                sre_agent=mock_sre,
                judge_agent=mock_judge,
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=3,
                shared_file=shared_file,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                usage_collector=UsageCollector(),
                crucible_config=CrucibleConfig(enable_judge=True),
            )
        )

    assert result.approved is True
    mock_submit.assert_not_called()
