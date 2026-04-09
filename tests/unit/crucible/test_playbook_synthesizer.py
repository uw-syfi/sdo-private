"""Tests for sregym_agents.crucible.knowledge_base.playbook_synthesizer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    import pytest

from sregym_agents.crucible.knowledge_base.playbook import Playbook
from sregym_agents.crucible.knowledge_base.playbook_synthesizer import PlaybookSynthesizer
from sregym_agents.crucible.recovery_reflection import RecoveryReflection


def _valid_playbook_markdown(
    slug: str = "missing_env_variable",
    class_name: str = "Missing environment variable",
) -> str:
    """Return a minimal fully-valid playbook markdown string."""
    return (
        f"# Playbook: {class_name}\n"
        "\n"
        "<!-- meta -->\n"
        f"slug: {slug}\n"
        f"class_name: {class_name}\n"
        "seen: 3\n"
        "created_from: success\n"
        "last_updated: 2026-04-09T12:00:00Z\n"
        "<!-- /meta -->\n"
        "\n"
        "## Summary\n"
        "Service crashes on startup because a required env var is unset.\n"
        "\n"
        "## Symptoms\n"
        "- Pod restarts with CrashLoopBackOff within 10 seconds of start.\n"
        "\n"
        "## Triage Procedure\n"
        "1. **Action:** kubectl get pods\n"
        "   **Expected shape:** running pods\n"
        "   **If absent:** check namespace\n"
        "\n"
        "## Verification Procedure\n"
        "1. **Action:** kubectl logs deploy/api\n"
        "   **Expected shape:** startup log line printed\n"
        "   **If absent:** rerun kubectl describe\n"
        "\n"
        "## Required Evidence\n"
        "- [ ] Confirmed the env var was missing from deployment spec.\n"
        "\n"
        "## Known Distractors\n"
        "- Network policy errors in unrelated pods.\n"
        "\n"
        "## Failure Patterns\n"
        "- Pod exit code 1 with stderr referencing unset variable.\n"
        "\n"
        "## References\n"
        "- successes: run_42\n"
        "- failures: run_17\n"
    )


def _make_recovery_reflection() -> RecoveryReflection:
    """Return a minimal RecoveryReflection for tests."""
    return RecoveryReflection(
        summary="x",
        investigation_observations=["obs1"],
        stage_failures=[],
    )


def _make_mock_result(text: str) -> MagicMock:
    """Return a mock LLM result whose .output is the given text."""
    result = MagicMock()
    result.output = text
    return result


def _make_synthesizer() -> tuple[PlaybookSynthesizer, MagicMock]:
    """Return a (synthesizer, mock_renderer) pair wired to return "PROMPT"."""
    mock_renderer = MagicMock()
    mock_renderer.render.return_value = "PROMPT"
    synth = PlaybookSynthesizer(model_id="test-model", renderer=mock_renderer)
    return synth, mock_renderer


class TestSynthesizeFromSuccess:
    async def test_valid_playbook_on_first_attempt(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        valid_md = _valid_playbook_markdown(slug="foo", class_name="Foo")
        mock_arun = AsyncMock(return_value=_make_mock_result(valid_md))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
                incident_ref="incident_1",
            )

        assert result is not None
        assert isinstance(result, Playbook)
        assert result.slug == "foo"
        assert result.class_name == "Foo"
        assert mock_arun.await_count == 1
        # Renderer called with the correct template name.
        first_call = mock_renderer.render.call_args_list[0]
        assert first_call.args[0] == "kb/synthesize_playbook_from_success"

    async def test_validation_failure_then_success(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        invalid_md = "not a playbook"
        valid_md = _valid_playbook_markdown(slug="foo", class_name="Foo")
        mock_arun = AsyncMock(
            side_effect=[
                _make_mock_result(invalid_md),
                _make_mock_result(valid_md),
            ]
        )

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
                incident_ref="incident_1",
            )

        assert result is not None
        assert result.slug == "foo"
        assert mock_arun.await_count == 2

        # First render: empty feedback. Second render: non-empty feedback with '- ' lines.
        call_list = mock_renderer.render.call_args_list
        assert len(call_list) == 2
        first_kwargs = call_list[0].kwargs
        assert first_kwargs.get("validation_feedback") == ""
        second_kwargs = call_list[1].kwargs
        feedback = second_kwargs.get("validation_feedback")
        assert isinstance(feedback, str)
        assert feedback != ""
        assert "- " in feedback

    async def test_validation_failure_on_all_attempts_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        synth, _mock_renderer = _make_synthesizer()
        invalid_md = "not a playbook"
        mock_arun = AsyncMock(return_value=_make_mock_result(invalid_md))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
            caplog.at_level(logging.ERROR, logger="sregym_agents.crucible.knowledge_base.playbook_synthesizer"),
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
                incident_ref="incident_1",
            )

        assert result is None
        assert mock_arun.await_count == 3
        # Error log produced after giving up.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("giving up" in r.message for r in error_records)

    async def test_llm_call_raises_returns_none_without_retries(self) -> None:
        synth, _mock_renderer = _make_synthesizer()
        mock_arun = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
                incident_ref="incident_1",
            )

        assert result is None
        assert mock_arun.await_count == 1


class TestSynthesizeFromRecovery:
    async def test_valid_playbook_template_vars(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        valid_md = _valid_playbook_markdown(slug="bar", class_name="Bar")
        mock_arun = AsyncMock(return_value=_make_mock_result(valid_md))
        reflection = _make_recovery_reflection()

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_recovery(
                class_name="Bar",
                slug="bar",
                stage_outputs="stages",
                recovery_reflection=reflection,
                oracle_answer="answer",
                incident_ref="incident_2",
            )

        assert result is not None
        assert isinstance(result, Playbook)
        assert result.slug == "bar"

        call = mock_renderer.render.call_args_list[0]
        assert call.args[0] == "kb/synthesize_playbook_from_recovery"
        kwargs = call.kwargs
        assert kwargs["class_name"] == "Bar"
        assert kwargs["slug"] == "bar"
        assert kwargs["stage_outputs"] == "stages"
        assert kwargs["oracle_answer"] == "answer"
        assert kwargs["incident_ref"] == "incident_2"
        assert kwargs["recovery_summary"] == "x"
        assert kwargs["recovery_observations"] == ["obs1"]
        assert kwargs["recovery_stage_failures"] == []


class TestRefine:
    async def test_refine_passes_existing_markdown(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        existing_md = _valid_playbook_markdown(slug="baz", class_name="Baz")
        existing = Playbook.parse(existing_md)
        new_md = _valid_playbook_markdown(slug="baz", class_name="Baz")
        mock_arun = AsyncMock(return_value=_make_mock_result(new_md))
        reflection = _make_recovery_reflection()

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.refine(
                existing=existing,
                stage_outputs="fail-stages",
                recovery_reflection=reflection,
                oracle_answer="answer",
                incident_ref="incident_3",
            )

        assert result is not None
        assert isinstance(result, Playbook)

        call = mock_renderer.render.call_args_list[0]
        assert call.args[0] == "kb/refine_playbook"
        kwargs = call.kwargs
        assert "existing_playbook" in kwargs
        assert "<!-- meta -->" in kwargs["existing_playbook"]
        assert "slug: baz" in kwargs["existing_playbook"]
        assert kwargs["class_name"] == "Baz"
        assert kwargs["slug"] == "baz"
        assert kwargs["failed_stage_outputs"] == "fail-stages"
        assert kwargs["incident_ref"] == "incident_3"


class TestConsolidate:
    async def test_consolidate_winner_none_uses_placeholder(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        loser_md = _valid_playbook_markdown(slug="loser", class_name="Loser")
        loser = Playbook.parse(loser_md)
        new_md = _valid_playbook_markdown(slug="winner", class_name="Winner")
        mock_arun = AsyncMock(return_value=_make_mock_result(new_md))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.consolidate(
                winner_class_name="Winner",
                winner_slug="winner",
                winner_playbook=None,
                loser_playbooks=[loser],
                combined_seen=5,
                stage_outputs="stages",
                recovery_summary="summary",
                combined_references={},
            )

        assert result is not None
        assert isinstance(result, Playbook)

        call = mock_renderer.render.call_args_list[0]
        assert call.args[0] == "kb/merge_playbooks"
        kwargs = call.kwargs
        assert kwargs["winner_playbook"] == "(none)"
        assert isinstance(kwargs["loser_playbooks"], list)
        assert len(kwargs["loser_playbooks"]) == 1
        assert "slug: loser" in kwargs["loser_playbooks"][0]
        # References default to empty lists for both keys.
        assert kwargs["combined_references"] == {"successes": [], "failures": []}
        assert kwargs["combined_seen"] == 5
        assert kwargs["winner_slug"] == "winner"
        assert kwargs["winner_class_name"] == "Winner"

    async def test_consolidate_with_winner_and_references(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        winner_md = _valid_playbook_markdown(slug="winner", class_name="Winner")
        winner = Playbook.parse(winner_md)
        loser1_md = _valid_playbook_markdown(slug="loser1", class_name="Loser1")
        loser2_md = _valid_playbook_markdown(slug="loser2", class_name="Loser2")
        loser1 = Playbook.parse(loser1_md)
        loser2 = Playbook.parse(loser2_md)
        new_md = _valid_playbook_markdown(slug="winner", class_name="Winner")
        mock_arun = AsyncMock(return_value=_make_mock_result(new_md))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.consolidate(
                winner_class_name="Winner",
                winner_slug="winner",
                winner_playbook=winner,
                loser_playbooks=[loser1, loser2],
                combined_seen=7,
                stage_outputs="stages",
                recovery_summary="summary",
                combined_references={
                    "successes": ["run_1", "run_2"],
                    "failures": ["run_9"],
                },
            )

        assert result is not None

        call = mock_renderer.render.call_args_list[0]
        kwargs = call.kwargs
        assert "slug: winner" in kwargs["winner_playbook"]
        assert len(kwargs["loser_playbooks"]) == 2
        assert "slug: loser1" in kwargs["loser_playbooks"][0]
        assert "slug: loser2" in kwargs["loser_playbooks"][1]
        assert kwargs["combined_references"]["successes"] == ["run_1", "run_2"]
        assert kwargs["combined_references"]["failures"] == ["run_9"]


class TestValidationFeedback:
    async def test_feedback_empty_on_first_call_and_populated_on_retry(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        invalid_md = "not a playbook"
        valid_md = _valid_playbook_markdown(slug="foo", class_name="Foo")
        mock_arun = AsyncMock(
            side_effect=[
                _make_mock_result(invalid_md),
                _make_mock_result(valid_md),
            ]
        )

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.playbook_synthesizer.arun_with_retry",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
                incident_ref="incident_1",
            )

        calls = mock_renderer.render.call_args_list
        assert len(calls) == 2

        # First call: empty feedback.
        assert calls[0].kwargs["validation_feedback"] == ""

        # Second call: feedback is a non-empty string, formatted as "- <violation>"
        # lines joined by newlines.
        feedback = calls[1].kwargs["validation_feedback"]
        assert isinstance(feedback, str)
        assert feedback
        lines = feedback.split("\n")
        assert all(line.startswith("- ") for line in lines)
        assert len(lines) >= 1
