"""Unit tests for sregym_agents.pydantic_agent.tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sregym_agents.pydantic_agent.tools import SREGymDeps, submit_solution


def _make_ctx(submit_url: str = "http://localhost:9954/submit/sse") -> MagicMock:
    ctx = MagicMock()
    ctx.deps = SREGymDeps(
        namespace="ns",
        submit_mcp_url=submit_url,
    )
    return ctx


# ---------------------------------------------------------------------------
# submit_solution
# ---------------------------------------------------------------------------


class TestSubmitSolution:
    def test_calls_run_async_on_success(self):
        ctx = _make_ctx()
        with patch(
            "sregym_agents.pydantic_agent.tools._run_async",
            return_value="ok from benchmark",
        ):
            result = submit_solution(ctx, "my diagnosis answer")

        assert "ok from benchmark" in result
        assert result.startswith("Submission result:")

    def test_exception_returns_error_string(self):
        ctx = _make_ctx()
        with patch(
            "sregym_agents.pydantic_agent.tools._run_async",
            side_effect=RuntimeError("asyncio.run() cannot be called from a running event loop"),
        ):
            result = submit_solution(ctx, "my answer")

        assert "Submission error" in result
        assert "asyncio.run()" in result
