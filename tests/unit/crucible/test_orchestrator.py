"""Unit tests for sregym_agents.crucible.orchestrator helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

if TYPE_CHECKING:
    from pathlib import Path

from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.orchestrator import (
    _add_usage,
    _build_usage_result,
    _replace_hypothesis_placeholder,
    _wait_for_mitigation_stage,
    _zero_usage,
)
from sregym_agents.crucible.tools import SharedFile

# ---------------------------------------------------------------------------
# _zero_usage
# ---------------------------------------------------------------------------


def test_initial_usage_has_all_token_fields_set_to_zero():
    result = _zero_usage()
    assert result == {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}


# ---------------------------------------------------------------------------
# _add_usage
# ---------------------------------------------------------------------------


class TestAddUsage:
    def test_tokens_from_two_usage_dicts_are_summed_per_field(self):
        a = {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2}
        b = {"input_tokens": 3, "output_tokens": 7, "cached_input_tokens": 1}
        result = _add_usage(a, b)
        assert result == {"input_tokens": 13, "output_tokens": 12, "cached_input_tokens": 3}

    def test_missing_fields_in_second_usage_dict_leave_first_values_unchanged(self):
        a = {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2}
        b = {"input_tokens": 3}
        result = _add_usage(a, b)
        assert result == {"input_tokens": 13, "output_tokens": 5, "cached_input_tokens": 2}


# ---------------------------------------------------------------------------
# _build_usage_result
# ---------------------------------------------------------------------------


class TestBuildUsageResult:
    def test_total_sums_tokens_from_sre_agent_and_judge_together(self):
        usage_by_agent = {
            "diagnosis-agent": {
                "iterations": [],
                "total": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 1},
            },
            "diagnosis-judge": {
                "iterations": [],
                "total": {"input_tokens": 20, "output_tokens": 8, "cached_input_tokens": 0},
            },
        }
        result = _build_usage_result(usage_by_agent)
        assert result["total"]["input_tokens"] == 30
        assert result["total"]["output_tokens"] == 13
        assert result["total"]["cached_input_tokens"] == 1

    def test_result_contains_per_agent_breakdown_and_combined_total(self):
        usage_by_agent = {
            "agent": {"iterations": [], "total": _zero_usage()},
        }
        result = _build_usage_result(usage_by_agent)
        assert "by_agent" in result
        assert "total" in result
        assert result["by_agent"] == usage_by_agent


# ---------------------------------------------------------------------------
# SharedFile.init (orchestrator init content)
# ---------------------------------------------------------------------------


class TestSharedFileInit:
    def _init(self, shared_file, app_info: dict) -> None:
        SharedFile(shared_file).init(
            "# SRE Judged Session State\n"
            "## Session\n"
            f"- App: {app_info.get('app_name', 'unknown')} "
            f"/ Namespace: {app_info.get('namespace', 'default')}\n\n"
            "## Diagnosis\n"
        )

    def test_creates_parent_dirs(self, tmp_path: Path):
        shared = tmp_path / "sub" / "dir" / "session.md"
        self._init(shared, {"app_name": "myapp", "namespace": "ns"})
        assert shared.exists()

    def test_writes_header_with_app_and_namespace(self, tmp_path: Path):
        shared = tmp_path / "session.md"
        self._init(shared, {"app_name": "myapp", "namespace": "prod"})
        content = shared.read_text()
        assert "myapp" in content
        assert "prod" in content

    def test_file_readable_after_call(self, tmp_path: Path):
        shared = tmp_path / "session.md"
        self._init(shared, {})
        content = shared.read_text()
        assert len(content) > 0


# ---------------------------------------------------------------------------
# _wait_for_mitigation_stage
# ---------------------------------------------------------------------------


def _mock_httpx_response(stage: str) -> MagicMock:
    """Build a fake httpx-style response with the given stage in the JSON body."""
    resp = MagicMock()
    resp.json.return_value = {"stage": stage}
    resp.raise_for_status = MagicMock()
    return resp


def _make_mock_client(**kwargs) -> AsyncMock:
    """Build a mock httpx.AsyncClient with async context-manager support."""
    client = AsyncMock(spec=httpx.AsyncClient)
    for k, v in kwargs.items():
        setattr(client, k, v)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _counter_clock(step: float = 1.0):
    """Return a callable that increments by *step* on each call (starts at 0)."""
    n = {"v": -step}

    def tick():
        n["v"] += step
        return n["v"]

    return tick


class TestWaitForMitigationStage:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_stage_is_mitigation(self):
        client = _make_mock_client(
            get=AsyncMock(return_value=_mock_httpx_response("mitigation")),
        )

        with (
            patch("sregym_agents.crucible.orchestrator.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible.orchestrator.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("sregym_agents.crucible.orchestrator.time.monotonic", side_effect=_counter_clock()),
        ):
            await _wait_for_mitigation_stage("http://localhost:8000", timeout=300)

        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_polls_until_stage_matches(self):
        call_count = 0

        async def staged_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _mock_httpx_response("diagnosis")
            return _mock_httpx_response("mitigation")

        client = _make_mock_client(get=AsyncMock(side_effect=staged_get))

        with (
            patch("sregym_agents.crucible.orchestrator.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible.orchestrator.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("sregym_agents.crucible.orchestrator.time.monotonic", side_effect=_counter_clock()),
        ):
            await _wait_for_mitigation_stage("http://localhost:8000", timeout=300)

        assert mock_sleep.await_count == 2  # slept after two "diagnosis" responses

    @pytest.mark.asyncio
    async def test_logs_warning_and_returns_on_timeout(self):
        client = _make_mock_client(
            get=AsyncMock(return_value=_mock_httpx_response("diagnosis")),
        )

        # First call returns 0; all subsequent calls return past-timeout.
        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            return 0.0 if call_count == 1 else 400.0

        with (
            patch("sregym_agents.crucible.orchestrator.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible.orchestrator.asyncio.sleep", new_callable=AsyncMock),
            patch("sregym_agents.crucible.orchestrator.time.monotonic", side_effect=fake_monotonic),
        ):
            # Should return without raising
            await _wait_for_mitigation_stage("http://localhost:8000", timeout=300)

    @pytest.mark.asyncio
    async def test_handles_connection_error_gracefully(self):
        call_count = 0

        async def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("refused")
            return _mock_httpx_response("mitigation")

        client = _make_mock_client(get=AsyncMock(side_effect=fake_get))

        with (
            patch("sregym_agents.crucible.orchestrator.httpx.AsyncClient", return_value=client),
            patch("sregym_agents.crucible.orchestrator.asyncio.sleep", new_callable=AsyncMock),
            patch("sregym_agents.crucible.orchestrator.time.monotonic", side_effect=_counter_clock()),
        ):
            await _wait_for_mitigation_stage("http://localhost:8000", timeout=300)


# ---------------------------------------------------------------------------
# _replace_hypothesis_placeholder
# ---------------------------------------------------------------------------


class TestReplaceHypothesisPlaceholder:
    def test_hypothesis_placeholder_replaced_after_judge(self, tmp_path: Path):
        shared = tmp_path / "session.md"
        shared.write_text(
            "# header\n"
            "\n### Iteration 1 — Agent Hypothesis\n"
            "[Submitted — pending judge review]\n"
            "\n### Iteration 1 — Judge Verdict\n"
        )
        _replace_hypothesis_placeholder(shared, 1, "disk full", "saw 100% usage")
        content = shared.read_text()
        assert "[Submitted — pending judge review]" not in content
        assert "**Diagnosis**: disk full" in content
        assert "**Justification**: saw 100% usage" in content

    def test_no_placeholder_is_noop(self, tmp_path: Path):
        shared = tmp_path / "session.md"
        original = "# header\nsome content\n"
        shared.write_text(original)
        _replace_hypothesis_placeholder(shared, 1, "diag", "just")
        assert shared.read_text() == original

    def test_only_matching_iteration_replaced(self, tmp_path: Path):
        shared = tmp_path / "session.md"
        shared.write_text(
            "\n### Iteration 1 — Agent Hypothesis\n"
            "**Diagnosis**: old\n**Justification**: old\n"
            "\n### Iteration 2 — Agent Hypothesis\n"
            "[Submitted — pending judge review]\n"
        )
        _replace_hypothesis_placeholder(shared, 2, "new diag", "new just")
        content = shared.read_text()
        assert "**Diagnosis**: old" in content  # iteration 1 unchanged
        assert "**Diagnosis**: new diag" in content  # iteration 2 replaced


class TestHypothesisTextPassedToJudgeDeps:
    def test_hypothesis_text_formatted_from_sre_state(self, tmp_path: Path):
        shared_path = tmp_path / "session.md"
        shared_path.write_text("# Session\n")
        shared = SharedFile(shared_path)

        def fake_sre_constructor(model, deps, trajectory_path=None, system_prompt_override=None):
            mock = MagicMock()

            def fake_run(prompt, run_ctx=None):
                deps.state.answer = "disk full"
                deps.state.answer_justification = "100% usage"
                return None, {"input_tokens": 5, "output_tokens": 3, "cached_input_tokens": 0}

            mock.arun = AsyncMock(side_effect=fake_run)
            return mock

        captured_judge_deps = []

        def fake_judge_constructor(model, deps, trajectory_path=None):
            captured_judge_deps.append(deps)
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
        ):
            import asyncio

            from pydantic_ai.models import infer_model

            from sregym_agents.crucible.config import CrucibleConfig
            from sregym_agents.crucible.orchestrator import _run_stage_loop

            asyncio.run(
                _run_stage_loop(
                    model=infer_model("test"),
                    app_info={"app_name": "myapp", "namespace": "default"},
                    stage="diagnosis",
                    max_iters=3,
                    shared_file=shared,
                    submit_mcp_url="http://localhost:9954/submit/sse",
                    renderer=mock_renderer,
                    crucible_config=CrucibleConfig(enable_judge=True),
                )
            )

        assert len(captured_judge_deps) == 1
        assert "**Diagnosis**: disk full" in captured_judge_deps[0].hypothesis_text
        assert "**Justification**: 100% usage" in captured_judge_deps[0].hypothesis_text


# ---------------------------------------------------------------------------
# _render lt_summary_file handling
# ---------------------------------------------------------------------------

_BASE_KWARGS = {
    "app_name": "app",
    "namespace": "ns",
    "descriptions": "",
    "iteration": 1,
    "shared_content": "",
    "shared_file": "/shared.md",
    "architecture_content": "",
    "lt_summary_content": "",
    "lessons_content": "",
}


_renderer = PromptRenderer("v1")


class TestRenderLtSummaryContent:
    @staticmethod
    def _render_both(lt_summary_content: str) -> list[str]:
        kwargs = {**_BASE_KWARGS, "lt_summary_content": lt_summary_content}
        return [_renderer.render(tmpl, **kwargs) for tmpl in ("diagnosis_agent_user", "mitigation_agent_user")]

    def test_empty_string_omits_summary_block(self):
        for rendered in self._render_both(""):
            assert "prior incident" not in rendered.lower()

    def test_content_includes_summary_block(self):
        for rendered in self._render_both("disk full on node-3"):
            assert "disk full on node-3" in rendered


# ---------------------------------------------------------------------------
# _run_stage_loop — transient ModelHTTPError handling
# ---------------------------------------------------------------------------


class TestStageLoopModelHTTPError:
    """When an agent raises ModelHTTPError after retries, the iteration should
    be skipped rather than crashing the entire orchestrator."""

    def test_sre_agent_http_error_skips_iteration(self, tmp_path: "Path"):
        from pydantic_ai.exceptions import ModelHTTPError

        shared_path = tmp_path / "session.md"
        shared_path.write_text("# Session\n")
        shared = SharedFile(shared_path)

        call_count = 0

        def fake_sre_constructor(model, deps, trajectory_path=None, system_prompt_override=None):
            mock = MagicMock()

            def fake_run(prompt, run_ctx=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ModelHTTPError(status_code=429, model_name="test", body="rate limited")
                deps.state.answer = "disk full"
                deps.state.answer_justification = "100% usage"
                deps.state.submitted = True
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
        ):
            import asyncio

            from pydantic_ai.models import infer_model

            from sregym_agents.crucible.config import CrucibleConfig
            from sregym_agents.crucible.orchestrator import _run_stage_loop

            result = asyncio.run(
                _run_stage_loop(
                    model=infer_model("test"),
                    app_info={"app_name": "myapp", "namespace": "default"},
                    stage="diagnosis",
                    max_iters=3,
                    shared_file=shared,
                    submit_mcp_url="http://localhost:9954/submit/sse",
                    renderer=mock_renderer,
                    crucible_config=CrucibleConfig(enable_judge=True),
                )
            )

        # First iteration failed (429), second succeeded — should still approve
        assert result.approved
        assert call_count == 2
        content = shared_path.read_text()
        assert "SRE Agent Error" in content
        assert "HTTP 429" in content
