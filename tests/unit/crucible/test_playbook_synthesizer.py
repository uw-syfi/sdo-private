"""Tests for sregym_agents.crucible.knowledge_base.playbook_synthesizer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    import pytest

from sregym_agents.crucible.agents.base import AgentResult
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
    )


def _make_recovery_reflection() -> RecoveryReflection:
    """Return a minimal RecoveryReflection for tests."""
    return RecoveryReflection(
        summary="x",
        investigation_observations=["obs1"],
        stage_failures=[],
    )


def _make_agent_result(text: str) -> AgentResult[str]:
    """Return an AgentResult whose .output is the given text."""
    return AgentResult(output=text, completed=True, messages=[])


def _make_synthesizer(
    side_effect: list[str] | None = None,
    return_text: str = "",
) -> tuple[PlaybookSynthesizer, MagicMock, AsyncMock]:
    """Return a (synthesizer, mock_renderer, mock_driver_run) triple.

    If *side_effect* is given, ``driver.run`` cycles through those strings.
    Otherwise it always returns *return_text*.
    """
    mock_renderer = MagicMock()
    mock_renderer.render.return_value = "PROMPT"
    mock_driver = MagicMock()
    if side_effect is not None:
        mock_driver.run = AsyncMock(side_effect=[_make_agent_result(t) for t in side_effect])
    else:
        mock_driver.run = AsyncMock(return_value=_make_agent_result(return_text))
    synth = PlaybookSynthesizer(renderer=mock_renderer, driver=mock_driver)
    return synth, mock_renderer, mock_driver.run


class TestSynthesizeFromSuccess:
    async def test_valid_playbook_on_first_attempt(self) -> None:
        valid_md = _valid_playbook_markdown(slug="foo", class_name="Foo")
        synth, mock_renderer, mock_run = _make_synthesizer(return_text=valid_md)
        result = await synth.synthesize_from_success(
            class_name="Foo",
            slug="foo",
            stage_outputs="stages",
            oracle_answer="answer",
        )

        assert result is not None
        assert isinstance(result, Playbook)
        assert result.slug == "foo"
        assert result.class_name == "Foo"
        assert mock_run.await_count == 1
        first_call = mock_renderer.render.call_args_list[0]
        assert first_call.args[0] == "kb/synthesize_playbook_from_success"

    async def test_validation_failure_then_success(self) -> None:
        invalid_md = "not a playbook"
        valid_md = _valid_playbook_markdown(slug="foo", class_name="Foo")
        synth, mock_renderer, mock_run = _make_synthesizer(side_effect=[invalid_md, valid_md])
        result = await synth.synthesize_from_success(
            class_name="Foo",
            slug="foo",
            stage_outputs="stages",
            oracle_answer="answer",
        )

        assert result is not None
        assert result.slug == "foo"
        assert mock_run.await_count == 2

        call_list = mock_renderer.render.call_args_list
        assert len(call_list) == 2
        assert call_list[0].kwargs.get("validation_feedback") == ""
        feedback = call_list[1].kwargs.get("validation_feedback")
        assert isinstance(feedback, str)
        assert feedback != ""
        assert "- " in feedback

    async def test_validation_failure_on_all_attempts_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        invalid_md = "not a playbook"
        synth, _, mock_run = _make_synthesizer(return_text=invalid_md)
        with caplog.at_level(logging.ERROR, logger="sregym_agents.crucible.knowledge_base.playbook_synthesizer"):
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
            )

        assert result is None
        assert mock_run.await_count == 3
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("giving up" in r.message for r in error_records)

    async def test_llm_call_raises_returns_none_without_retries(self) -> None:
        synth, _, mock_run = _make_synthesizer()
        mock_run.side_effect = RuntimeError("boom")
        result = await synth.synthesize_from_success(
            class_name="Foo",
            slug="foo",
            stage_outputs="stages",
            oracle_answer="answer",
        )

        assert result is None
        assert mock_run.await_count == 1


class TestSynthesizeFromRecovery:
    async def test_valid_playbook_template_vars(self) -> None:
        valid_md = _valid_playbook_markdown(slug="bar", class_name="Bar")
        synth, mock_renderer, _ = _make_synthesizer(return_text=valid_md)
        reflection = _make_recovery_reflection()
        result = await synth.synthesize_from_recovery(
            class_name="Bar",
            slug="bar",
            stage_outputs="stages",
            recovery_reflection=reflection,
            oracle_answer="answer",
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
        assert "incident_ref" not in kwargs
        assert kwargs["recovery_summary"] == "x"
        assert kwargs["recovery_observations"] == ["obs1"]
        assert kwargs["recovery_stage_failures"] == []


class TestSlugOverride:
    """LLM-corrupted slugs are silently corrected to the known template slug."""

    async def test_synthesize_corrects_corrupted_slug(self) -> None:
        corrupted_md = _valid_playbook_markdown(
            slug="coredns_nxdomain_responses_for a_service",
            class_name="CoreDNS NXDOMAIN for a service",
        )
        expected_slug = "coredns_nxdomain_responses_for_a_service"
        synth, _, _ = _make_synthesizer(return_text=corrupted_md)
        result = await synth.synthesize_from_success(
            class_name="CoreDNS NXDOMAIN for a service",
            slug=expected_slug,
            stage_outputs="stages",
            oracle_answer="answer",
        )

        assert result is not None
        assert result.slug == expected_slug

    async def test_refine_corrects_corrupted_slug(self) -> None:
        existing_md = _valid_playbook_markdown(
            slug="coredns_nxdomain_responses_for_a_service",
            class_name="CoreDNS NXDOMAIN for a service",
        )
        existing = Playbook.parse(existing_md)
        corrupted_md = _valid_playbook_markdown(
            slug="coredns_nxdomain_responses_for a_service",
            class_name="CoreDNS NXDOMAIN for a service",
        )
        synth, _, _ = _make_synthesizer(return_text=corrupted_md)
        reflection = _make_recovery_reflection()
        result = await synth.refine(
            existing=existing,
            stage_outputs="fail-stages",
            recovery_reflection=reflection,
            oracle_answer="answer",
        )

        assert result is not None
        assert result.slug == "coredns_nxdomain_responses_for_a_service"


class TestRefine:
    async def test_refine_passes_existing_markdown(self) -> None:
        existing_md = _valid_playbook_markdown(slug="baz", class_name="Baz")
        existing = Playbook.parse(existing_md)
        new_md = _valid_playbook_markdown(slug="baz", class_name="Baz")
        synth, mock_renderer, _ = _make_synthesizer(return_text=new_md)
        reflection = _make_recovery_reflection()
        result = await synth.refine(
            existing=existing,
            stage_outputs="fail-stages",
            recovery_reflection=reflection,
            oracle_answer="answer",
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
        assert "incident_ref" not in kwargs


class TestConsolidate:
    async def test_consolidate_winner_none_uses_placeholder(self) -> None:
        loser_md = _valid_playbook_markdown(slug="loser", class_name="Loser")
        loser = Playbook.parse(loser_md)
        new_md = _valid_playbook_markdown(slug="winner", class_name="Winner")
        synth, mock_renderer, _ = _make_synthesizer(return_text=new_md)
        result = await synth.consolidate(
            winner_class_name="Winner",
            winner_slug="winner",
            winner_playbook=None,
            loser_playbooks=[loser],
            combined_seen=5,
            stage_outputs="stages",
            recovery_summary="summary",
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
        assert "combined_references" not in kwargs
        assert kwargs["combined_seen"] == 5
        assert kwargs["winner_slug"] == "winner"
        assert kwargs["winner_class_name"] == "Winner"

    async def test_consolidate_with_winner(self) -> None:
        winner_md = _valid_playbook_markdown(slug="winner", class_name="Winner")
        winner = Playbook.parse(winner_md)
        loser1_md = _valid_playbook_markdown(slug="loser1", class_name="Loser1")
        loser2_md = _valid_playbook_markdown(slug="loser2", class_name="Loser2")
        loser1 = Playbook.parse(loser1_md)
        loser2 = Playbook.parse(loser2_md)
        new_md = _valid_playbook_markdown(slug="winner", class_name="Winner")
        synth, mock_renderer, _ = _make_synthesizer(return_text=new_md)
        result = await synth.consolidate(
            winner_class_name="Winner",
            winner_slug="winner",
            winner_playbook=winner,
            loser_playbooks=[loser1, loser2],
            combined_seen=7,
            stage_outputs="stages",
            recovery_summary="summary",
        )

        assert result is not None

        call = mock_renderer.render.call_args_list[0]
        kwargs = call.kwargs
        assert "slug: winner" in kwargs["winner_playbook"]
        assert len(kwargs["loser_playbooks"]) == 2
        assert "slug: loser1" in kwargs["loser_playbooks"][0]
        assert "slug: loser2" in kwargs["loser_playbooks"][1]
        assert "combined_references" not in kwargs


class TestValidationFeedback:
    async def test_feedback_empty_on_first_call_and_populated_on_retry(self) -> None:
        invalid_md = "not a playbook"
        valid_md = _valid_playbook_markdown(slug="foo", class_name="Foo")
        synth, mock_renderer, _ = _make_synthesizer(side_effect=[invalid_md, valid_md])
        await synth.synthesize_from_success(
            class_name="Foo",
            slug="foo",
            stage_outputs="stages",
            oracle_answer="answer",
        )

        calls = mock_renderer.render.call_args_list
        assert len(calls) == 2

        assert calls[0].kwargs["validation_feedback"] == ""

        feedback = calls[1].kwargs["validation_feedback"]
        assert isinstance(feedback, str)
        assert feedback
        lines = feedback.split("\n")
        assert all(line.startswith("- ") for line in lines)
        assert len(lines) >= 1
