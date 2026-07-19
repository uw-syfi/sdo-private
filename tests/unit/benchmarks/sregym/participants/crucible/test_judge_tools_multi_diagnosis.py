"""Tests for the multi-diagnosis extension to crucible judge tools.

The judge can now forward either a single string or a list of candidate
diagnoses to ``submit_to_benchmark`` via ``submit_verdict``. The tool
enforces a candidate cap so the agent can't blow up LLM-judge cost.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from benchmarks.sregym.participants.crucible.tools import (
    JudgeDeps,
    SharedFile,
    submit_to_benchmark,
    submit_verdict,
)
from benchmarks.sregym.participants.crucible.tools._judge_tools import MAX_DIAGNOSIS_CANDIDATES


def _make_judge_ctx(tmp_path: Path) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = JudgeDeps(
        namespace="ns",
        shared_file=SharedFile(tmp_path / "shared.md"),
        iteration=1,
        stage="diagnosis",
        submit_mcp_url="http://localhost:9000/sse",
        hypothesis_text="**Diagnosis**: test\n**Justification**: test\n",
    )
    ctx.deps.state.hypothesis_revealed = True
    return ctx


# ---------------------------------------------------------------------------
# submit_verdict accepts list-typed submission_ans
# ---------------------------------------------------------------------------


class TestSubmitVerdictMultiDiagnosis:
    def test_string_submission_still_works(self, tmp_path: Path):
        (tmp_path / "shared.md").write_text("")
        ctx = _make_judge_ctx(tmp_path)

        with patch(
            "benchmarks.sregym.participants.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "ok", {"Diagnosis": {"success": True}}),
        ) as mock_submit:
            asyncio.run(submit_verdict(ctx, True, "looks good", "single answer"))

        mock_submit.assert_awaited_once()
        # Second positional arg is submission_ans
        assert mock_submit.call_args.args[1] == "single answer"

    def test_list_submission_passed_through(self, tmp_path: Path):
        (tmp_path / "shared.md").write_text("")
        ctx = _make_judge_ctx(tmp_path)

        with patch(
            "benchmarks.sregym.participants.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "ok", {"Diagnosis": {"success": True}}),
        ) as mock_submit:
            asyncio.run(submit_verdict(ctx, True, "ambiguous", ["cand A", "cand B"]))

        mock_submit.assert_awaited_once()
        assert mock_submit.call_args.args[1] == ["cand A", "cand B"]

    def test_oversize_list_rejected_without_calling_benchmark(self, tmp_path: Path):
        (tmp_path / "shared.md").write_text("")
        ctx = _make_judge_ctx(tmp_path)

        too_many = [f"cand{i}" for i in range(MAX_DIAGNOSIS_CANDIDATES + 1)]
        with patch(
            "benchmarks.sregym.participants.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
        ) as mock_submit:
            result = asyncio.run(submit_verdict(ctx, True, "many", too_many))

        mock_submit.assert_not_awaited()
        assert "Error" in result
        # Agent should not be marked as having submitted on a rejected call.
        assert ctx.deps.state.submitted is False

    def test_empty_list_rejected(self, tmp_path: Path):
        (tmp_path / "shared.md").write_text("")
        ctx = _make_judge_ctx(tmp_path)

        with patch(
            "benchmarks.sregym.participants.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
        ) as mock_submit:
            result = asyncio.run(submit_verdict(ctx, True, "nothing", []))

        mock_submit.assert_not_awaited()
        assert "Error" in result
        assert ctx.deps.state.submitted is False

    def test_max_size_list_accepted(self, tmp_path: Path):
        (tmp_path / "shared.md").write_text("")
        ctx = _make_judge_ctx(tmp_path)

        exact = [f"cand{i}" for i in range(MAX_DIAGNOSIS_CANDIDATES)]
        with patch(
            "benchmarks.sregym.participants.crucible.tools._judge_tools.submit_to_benchmark",
            new_callable=AsyncMock,
            return_value=(True, "ok", {"Diagnosis": {"success": True}}),
        ) as mock_submit:
            asyncio.run(submit_verdict(ctx, True, "five total", exact))

        mock_submit.assert_awaited_once()
        assert mock_submit.call_args.args[1] == exact


# ---------------------------------------------------------------------------
# submit_to_benchmark forwards list payload to MCP unchanged
# ---------------------------------------------------------------------------


class TestSubmitToBenchmarkListPayload:
    def _run(self, coro):
        return asyncio.run(coro)

    def _make_mock_session(self, raw_response: str):
        mock_result = MagicMock()
        mock_result.content = [MagicMock(text=raw_response)]
        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        mock_session.initialize = AsyncMock()
        return mock_session

    def test_list_passed_to_mcp_call_tool(self):
        import json

        oracle = {"Diagnosis": {"success": True}}
        raw = repr({"status": "200", "text": json.dumps(oracle)})
        mock_session = self._make_mock_session(raw)

        with (
            patch("mcp.ClientSession") as mock_cs,
            patch("mcp.client.sse.sse_client") as mock_sse,
        ):
            mock_sse.return_value.__aenter__ = AsyncMock(return_value=("r", "w"))
            mock_sse.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

            success, msg, result_oracle = self._run(
                submit_to_benchmark("http://x/sse", ["cand A", "cand B"], "diagnosis")
            )

        assert success is True
        # call_tool was invoked with the list as `ans`, not stringified.
        call_kwargs = mock_session.call_tool.call_args
        assert call_kwargs.kwargs["arguments"] == {"ans": ["cand A", "cand B"]}
