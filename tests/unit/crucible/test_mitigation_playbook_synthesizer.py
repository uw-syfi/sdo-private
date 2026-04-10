"""Tests for sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    import pytest

from sregym_agents.crucible.knowledge_base.mitigation_playbook import MitigationPlaybook
from sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer import (
    MitigationPlaybookSynthesizer,
)
from sregym_agents.crucible.recovery_reflection import RecoveryReflection


def _valid_mitigation_playbook_markdown(
    slug: str = "missing_env_variable",
    class_name: str = "Missing environment variable",
) -> str:
    return (
        f"# Mitigation Playbook: {class_name}\n"
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
        "Restart the failing Deployment after adding the missing env var.\n"
        "\n"
        "## Applicability\n"
        "- A required environment variable is absent from the failing Deployment's container env spec.\n"
        "\n"
        "## Mitigation Procedure\n"
        "1. **Action:** kubectl set env deploy/<DEPLOYMENT> -n <NAMESPACE> <ENV_VAR>=<VALUE>\n"
        "   **Expected outcome:** rollout starts.\n"
        "\n"
        "## Post-Mitigation Verification\n"
        "1. **Action:** kubectl get pods -n <NAMESPACE> -l app=<APP>\n"
        "   **Expected outcome:** all pods running.\n"
        "\n"
        "## Required Evidence\n"
        "- [ ] All target Deployment pods Running with restartCount=0.\n"
        "\n"
        "## Known Pitfalls\n"
        "- Patching the wrong namespace.\n"
        "\n"
        "## Failure Patterns\n"
        "- Reporting success before the rollout completes.\n"
    )


def _make_recovery_reflection() -> RecoveryReflection:
    return RecoveryReflection(
        summary="x",
        investigation_observations=["obs1"],
        stage_failures=[],
    )


def _make_mock_result(text: str) -> MagicMock:
    result = MagicMock()
    result.output = text
    return result


def _make_synthesizer() -> tuple[MitigationPlaybookSynthesizer, MagicMock]:
    mock_renderer = MagicMock()
    mock_renderer.render.return_value = "PROMPT"
    synth = MitigationPlaybookSynthesizer(model_id="test-model", renderer=mock_renderer)
    return synth, mock_renderer


class TestSynthesizeFromSuccess:
    async def test_valid_playbook_on_first_attempt(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        valid_md = _valid_mitigation_playbook_markdown(slug="foo", class_name="Foo")
        mock_arun = AsyncMock(return_value=_make_mock_result(valid_md))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.arun_with_retry_tracked",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
            )

        assert result is not None
        assert isinstance(result, MitigationPlaybook)
        assert result.slug == "foo"
        assert result.class_name == "Foo"
        assert mock_arun.await_count == 1
        first_call = mock_renderer.render.call_args_list[0]
        assert first_call.args[0] == "kb/synthesize_mitigation_playbook_from_success"

    async def test_validation_failure_then_success(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        invalid_md = "not a playbook"
        valid_md = _valid_mitigation_playbook_markdown(slug="foo", class_name="Foo")
        mock_arun = AsyncMock(
            side_effect=[
                _make_mock_result(invalid_md),
                _make_mock_result(valid_md),
            ]
        )

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.arun_with_retry_tracked",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
            )

        assert result is not None
        assert result.slug == "foo"
        assert mock_arun.await_count == 2

        call_list = mock_renderer.render.call_args_list
        assert len(call_list) == 2
        assert call_list[0].kwargs.get("validation_feedback") == ""
        feedback = call_list[1].kwargs.get("validation_feedback")
        assert isinstance(feedback, str)
        assert feedback != ""
        assert "- " in feedback

    async def test_validation_failure_on_all_attempts_returns_none(self, caplog: pytest.LogCaptureFixture) -> None:
        synth, _mock_renderer = _make_synthesizer()
        invalid_md = "not a playbook"
        mock_arun = AsyncMock(return_value=_make_mock_result(invalid_md))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.arun_with_retry_tracked",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.Agent") as MockAgent,
            caplog.at_level(
                logging.ERROR,
                logger="sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer",
            ),
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
            )

        assert result is None
        assert mock_arun.await_count == 3
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("giving up" in r.message for r in error_records)

    async def test_llm_call_raises_returns_none_without_retries(self) -> None:
        synth, _mock_renderer = _make_synthesizer()
        mock_arun = AsyncMock(side_effect=RuntimeError("boom"))

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.arun_with_retry_tracked",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_success(
                class_name="Foo",
                slug="foo",
                stage_outputs="stages",
                oracle_answer="answer",
            )

        assert result is None
        assert mock_arun.await_count == 1


class TestSynthesizeFromRecovery:
    async def test_valid_playbook_template_vars(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        valid_md = _valid_mitigation_playbook_markdown(slug="bar", class_name="Bar")
        mock_arun = AsyncMock(return_value=_make_mock_result(valid_md))
        reflection = _make_recovery_reflection()

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.arun_with_retry_tracked",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.synthesize_from_recovery(
                class_name="Bar",
                slug="bar",
                stage_outputs="stages",
                recovery_reflection=reflection,
                oracle_answer="answer",
            )

        assert result is not None
        assert isinstance(result, MitigationPlaybook)
        assert result.slug == "bar"

        call = mock_renderer.render.call_args_list[0]
        assert call.args[0] == "kb/synthesize_mitigation_playbook_from_recovery"
        kwargs = call.kwargs
        assert kwargs["class_name"] == "Bar"
        assert kwargs["slug"] == "bar"
        assert kwargs["stage_outputs"] == "stages"
        assert kwargs["oracle_answer"] == "answer"
        assert kwargs["recovery_summary"] == "x"
        assert kwargs["recovery_observations"] == ["obs1"]
        assert kwargs["recovery_stage_failures"] == []


class TestRefine:
    async def test_refine_passes_existing_markdown(self) -> None:
        synth, mock_renderer = _make_synthesizer()
        existing_md = _valid_mitigation_playbook_markdown(slug="baz", class_name="Baz")
        existing = MitigationPlaybook.parse(existing_md)
        new_md = _valid_mitigation_playbook_markdown(slug="baz", class_name="Baz")
        mock_arun = AsyncMock(return_value=_make_mock_result(new_md))
        reflection = _make_recovery_reflection()

        with (
            patch(
                "sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.arun_with_retry_tracked",
                mock_arun,
            ),
            patch("sregym_agents.crucible.knowledge_base.mitigation_playbook_synthesizer.Agent") as MockAgent,
        ):
            MockAgent.return_value = MagicMock()
            result = await synth.refine(
                existing=existing,
                stage_outputs="fail-stages",
                recovery_reflection=reflection,
                oracle_answer="answer",
            )

        assert result is not None
        assert isinstance(result, MitigationPlaybook)

        call = mock_renderer.render.call_args_list[0]
        assert call.args[0] == "kb/refine_mitigation_playbook"
        kwargs = call.kwargs
        assert "existing_playbook" in kwargs
        assert "<!-- meta -->" in kwargs["existing_playbook"]
        assert "slug: baz" in kwargs["existing_playbook"]
        assert kwargs["class_name"] == "Baz"
        assert kwargs["slug"] == "baz"
        assert kwargs["failed_stage_outputs"] == "fail-stages"
