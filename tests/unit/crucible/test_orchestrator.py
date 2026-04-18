"""Unit tests for sregym_agents.crucible.orchestrator helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from libs.pydantic_agent import TokenUsage, UsageCollector
from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.config import CrucibleConfig
from sregym_agents.crucible.knowledge_base.base import InjectedKB
from sregym_agents.crucible.knowledge_base.incident_review import DiagnosisPlaybookDraft, MitigationPlaybookDraft
from sregym_agents.crucible.orchestrator import (
    _agent_phase,
    _build_sre_agent_config,
    _build_usage_metrics,
    _replace_hypothesis_placeholder,
)
from sregym_agents.crucible.tools import SharedFile

# ---------------------------------------------------------------------------
# _build_usage_metrics
# ---------------------------------------------------------------------------


class TestBuildUsageMetrics:
    def test_grand_total_sums_primary_and_recovery(self):
        primary = UsageCollector()
        primary.add("sre-diagnosis", TokenUsage(input_tokens=10, output_tokens=5, cached_input_tokens=1))
        primary.add("judge-diagnosis", TokenUsage(input_tokens=20, output_tokens=8, cached_input_tokens=0))

        recovery = UsageCollector()
        recovery.add("sre-diagnosis", TokenUsage(input_tokens=7, output_tokens=2, cached_input_tokens=3))

        result = _build_usage_metrics(primary, recovery)

        assert result["primary"]["total"] == {
            "input_tokens": 30,
            "output_tokens": 13,
            "cached_input_tokens": 1,
            "turns": 0,
        }
        assert result["recovery"]["total"] == {
            "input_tokens": 7,
            "output_tokens": 2,
            "cached_input_tokens": 3,
            "turns": 0,
        }
        assert result["total"] == {
            "input_tokens": 37,
            "output_tokens": 15,
            "cached_input_tokens": 4,
            "turns": 0,
        }

    def test_keys_kept_separate_between_primary_and_recovery(self):
        primary = UsageCollector()
        primary.add("sre-diagnosis", TokenUsage(input_tokens=10))
        recovery = UsageCollector()
        recovery.add("sre-diagnosis", TokenUsage(input_tokens=99))

        result = _build_usage_metrics(primary, recovery)

        assert result["primary"]["by_agent"]["sre-diagnosis"]["total"]["input_tokens"] == 10
        assert result["recovery"]["by_agent"]["sre-diagnosis"]["total"]["input_tokens"] == 99

    def test_empty_collectors_yield_zero_total(self):
        result = _build_usage_metrics(UsageCollector(), UsageCollector())
        assert result["primary"]["by_agent"] == {}
        assert result["recovery"]["by_agent"] == {}
        assert result["total"] == {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "turns": 0}
        assert result["diagnosis"] == {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "turns": 0}
        assert result["mitigation"] == {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0, "turns": 0}

    def test_phase_totals_split_diagnosis_and_mitigation(self):
        primary = UsageCollector()
        primary.add("sre-diagnosis", TokenUsage(input_tokens=10, output_tokens=1, turns=3))
        primary.add("hypothesis-verifier", TokenUsage(input_tokens=4, turns=2))
        primary.add("sre-mitigation", TokenUsage(input_tokens=20, output_tokens=2, turns=5))
        primary.add("ltm-mitigation-search", TokenUsage(input_tokens=1, turns=1))

        result = _build_usage_metrics(primary, UsageCollector())

        assert result["diagnosis"] == {
            "input_tokens": 14,
            "output_tokens": 1,
            "cached_input_tokens": 0,
            "turns": 5,
        }
        assert result["mitigation"] == {
            "input_tokens": 21,
            "output_tokens": 2,
            "cached_input_tokens": 0,
            "turns": 6,
        }

    def test_phase_totals_include_recovery_collector(self):
        primary = UsageCollector()
        primary.add("sre-diagnosis", TokenUsage(turns=3))
        primary.add("sre-mitigation", TokenUsage(turns=2))

        recovery = UsageCollector()
        recovery.add("recovery-diagnosis", TokenUsage(turns=4))
        recovery.add("recovery-mitigation", TokenUsage(turns=1))

        result = _build_usage_metrics(primary, recovery)

        assert result["diagnosis"]["turns"] == 7
        assert result["mitigation"]["turns"] == 3
        assert result["total"]["turns"] == 10

    def test_phase_totals_handle_dynamic_prefixes(self):
        primary = UsageCollector()
        primary.add("triage-kubernetes-service-discovery", TokenUsage(turns=2))
        primary.add("triage-coordinator", TokenUsage(turns=1))
        primary.add("ltm-verify-0", TokenUsage(turns=3))
        primary.add("ltm-verify-1", TokenUsage(turns=4))

        result = _build_usage_metrics(primary, UsageCollector())

        # All four dynamic-prefix agents are diagnosis phase.
        assert result["diagnosis"]["turns"] == 10
        assert result["mitigation"]["turns"] == 0

    def test_unknown_agent_excluded_from_phases_but_in_total(self, caplog):
        primary = UsageCollector()
        primary.add("sre-diagnosis", TokenUsage(input_tokens=5, turns=1))
        primary.add("some-new-agent", TokenUsage(input_tokens=7, turns=2))

        with caplog.at_level("WARNING"):
            result = _build_usage_metrics(primary, UsageCollector())

        assert result["diagnosis"] == {
            "input_tokens": 5,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "turns": 1,
        }
        assert result["mitigation"]["turns"] == 0
        # Unknown agent still contributes to the grand total.
        assert result["total"]["input_tokens"] == 12
        assert result["total"]["turns"] == 3
        assert any("some-new-agent" in rec.message for rec in caplog.records)


class TestAgentPhase:
    @pytest.mark.parametrize(
        "name",
        [
            "sre-diagnosis",
            "hypothesis-verifier",
            "ltm-search",
            "playbook-shortcut",
            "triage-coordinator",
            "triage-container-health-check-configuration",
            "ltm-verify-0",
            "ltm-verify-12",
            "recovery-diagnosis",
            "recovery-diagnosis-playbook",
            "success-diagnosis-playbook",
            "recovery-triage-area-candidate",
            "judge-diagnosis",
        ],
    )
    def test_diagnosis_phase(self, name):
        assert _agent_phase(name) == "diagnosis"

    @pytest.mark.parametrize(
        "name",
        [
            "sre-mitigation",
            "ltm-mitigation-search",
            "ltm-mitigate-0",
            "recovery-mitigation",
            "recovery-mitigation-playbook",
            "success-mitigation-playbook",
            "judge-mitigation",
        ],
    )
    def test_mitigation_phase(self, name):
        assert _agent_phase(name) == "mitigation"

    @pytest.mark.parametrize("name", ["kb-review-classifier", "unknown-agent", ""])
    def test_unknown_returns_none(self, name):
        assert _agent_phase(name) is None


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
# _build_sre_agent_config (triage-priors loading)
# ---------------------------------------------------------------------------


class TestBuildSREAgentConfigTriagePriors:
    """Guardrails for how _build_sre_agent_config consumes injected triage priors.

    The KB write path (kb_worker._refine_triage_priors) only emits YAML, and
    StructuredKnowledgeBase.inject only sets ``InjectedKB.triage_priors`` to a
    ``triage_priors.yaml`` path. No production code writes a legacy
    ``triage_priors.md``; this test locks in the YAML-only contract.
    """

    def test_yaml_priors_loaded_when_present(self, tmp_path: Path):
        yaml_path = tmp_path / "triage_priors.yaml"
        yaml_path.write_text(
            "areas:\n  - name: database\n    hints:\n      - check slow queries\n      - inspect connection pool\n"
        )

        injected = InjectedKB(triage_priors=yaml_path)
        config = CrucibleConfig(prompt_version="v3")

        result = _build_sre_agent_config(injected, config)

        assert result.triage_priors is not None
        assert [a.name for a in result.triage_priors.areas] == ["database"]
        assert result.triage_priors.areas[0].hints == [
            "check slow queries",
            "inspect connection pool",
        ]

    def test_md_injected_path_is_not_parsed_as_legacy_format(self, tmp_path: Path):
        """Legacy ``triage_priors.md`` must NOT be parsed — only YAML is honored.

        Simulates a stale legacy on-disk KB where ``InjectedKB.triage_priors``
        points directly at a ``.md`` file (the old pre-YAML format). The helper
        must ignore the markdown content and return a None triage_priors —
        neither silently converting it nor raising.
        """
        md_path = tmp_path / "triage_priors.md"
        md_path.write_text("## database\n- check slow queries\n- inspect connection pool\n")

        injected = InjectedKB(triage_priors=md_path)
        config = CrucibleConfig(prompt_version="v3")

        result = _build_sre_agent_config(injected, config)

        assert result.triage_priors is None

    def test_no_injected_kb_yields_none(self):
        config = CrucibleConfig(prompt_version="v3")
        result = _build_sre_agent_config(None, config)
        assert result.triage_priors is None


# ---------------------------------------------------------------------------
# _replace_hypothesis_placeholder
# ---------------------------------------------------------------------------


class TestReplaceHypothesisPlaceholder:
    def test_hypothesis_placeholder_replaced_after_judge(self, tmp_path: Path):
        shared_path = tmp_path / "session.md"
        shared_path.write_text(
            "# header\n"
            "\n### Iteration 1 — Agent Hypothesis\n"
            "[Submitted — pending judge review]\n"
            "\n### Iteration 1 — Judge Verdict\n"
        )
        shared = SharedFile(shared_path)
        _replace_hypothesis_placeholder(shared, 1, "disk full", "saw 100% usage")
        content = shared_path.read_text()
        assert "[Submitted — pending judge review]" not in content
        assert "**Diagnosis**: disk full" in content
        assert "**Justification**: saw 100% usage" in content

    def test_no_placeholder_is_noop(self, tmp_path: Path):
        shared_path = tmp_path / "session.md"
        original = "# header\nsome content\n"
        shared_path.write_text(original)
        shared = SharedFile(shared_path)
        _replace_hypothesis_placeholder(shared, 1, "diag", "just")
        assert shared_path.read_text() == original

    def test_only_matching_iteration_replaced(self, tmp_path: Path):
        shared_path = tmp_path / "session.md"
        shared_path.write_text(
            "\n### Iteration 1 — Agent Hypothesis\n"
            "**Diagnosis**: old\n**Justification**: old\n"
            "\n### Iteration 2 — Agent Hypothesis\n"
            "[Submitted — pending judge review]\n"
        )
        shared = SharedFile(shared_path)
        _replace_hypothesis_placeholder(shared, 2, "new diag", "new just")
        content = shared_path.read_text()
        assert "**Diagnosis**: old" in content  # iteration 1 unchanged
        assert "**Diagnosis**: new diag" in content  # iteration 2 replaced


class TestHypothesisTextPassedToJudge:
    def test_hypothesis_text_formatted_from_sre_state(self, tmp_path: Path):
        import asyncio

        from sregym_agents.crucible.agents.base import AgentResult
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import _run_stage_loop
        from sregym_agents.crucible.tools import SharedState, SRESubmission

        shared_path = tmp_path / "session.md"
        shared_path.write_text("# Session\n")
        shared = SharedFile(shared_path)

        # Mock SRE agent
        mock_sre = MagicMock()
        mock_sre._config = MagicMock()
        mock_sre._config.stage_outputs_file = None

        async def fake_sre_run(**kwargs):
            state = SharedState()
            state.answer = "disk full"
            state.answer_justification = "100% usage"
            result = AgentResult(
                output=SRESubmission(answer="disk full", justification="100% usage"),
                completed=True,
            )
            result.state = state  # type: ignore[attr-defined]
            return result

        mock_sre.run = AsyncMock(side_effect=fake_sre_run)

        # Mock judge agent — capture the hypothesis_text kwarg
        captured_judge_kwargs = []

        async def fake_judge_run(**kwargs):
            captured_judge_kwargs.append(kwargs)
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

        asyncio.run(
            _run_stage_loop(
                sre_agent=mock_sre,
                judge_agent=mock_judge,
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=3,
                shared_file=shared,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                usage_collector=UsageCollector(),
                crucible_config=CrucibleConfig(enable_judge=True),
            )
        )

        assert len(captured_judge_kwargs) == 1
        assert "**Diagnosis**: disk full" in captured_judge_kwargs[0]["hypothesis_text"]
        assert "**Justification**: 100% usage" in captured_judge_kwargs[0]["hypothesis_text"]


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
    """When an agent returns completed=False, the iteration should
    be skipped rather than crashing the entire orchestrator."""

    def test_sre_agent_error_skips_iteration(self, tmp_path: Path):
        import asyncio

        from sregym_agents.crucible.agents.base import AgentResult
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import _run_stage_loop
        from sregym_agents.crucible.tools import SharedState, SRESubmission

        shared_path = tmp_path / "session.md"
        shared_path.write_text("# Session\n")
        shared = SharedFile(shared_path)

        call_count = 0

        # Mock SRE agent: first call returns failed, second succeeds
        mock_sre = MagicMock()
        mock_sre._config = MagicMock()
        mock_sre._config.stage_outputs_file = None

        async def fake_sre_run(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate a transient failure
                state = SharedState()
                result = AgentResult(output=None, completed=False)
                result.state = state  # type: ignore[attr-defined]
                return result
            state = SharedState()
            state.answer = "disk full"
            state.answer_justification = "100% usage"
            state.submitted = True
            result = AgentResult(
                output=SRESubmission(answer="disk full", justification="100% usage"),
                completed=True,
            )
            result.state = state  # type: ignore[attr-defined]
            return result

        mock_sre.run = AsyncMock(side_effect=fake_sre_run)

        # Mock judge
        async def fake_judge_run(**kwargs):
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

        result = asyncio.run(
            _run_stage_loop(
                sre_agent=mock_sre,
                judge_agent=mock_judge,
                app_info={"app_name": "myapp", "namespace": "default"},
                stage="diagnosis",
                max_iters=3,
                shared_file=shared,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=mock_renderer,
                usage_collector=UsageCollector(),
                crucible_config=CrucibleConfig(enable_judge=True),
            )
        )

        # First iteration failed, second succeeded — should still approve
        assert result.approved
        assert call_count == 2
        content = shared_path.read_text()
        assert "SRE Agent Error" in content

    @pytest.mark.asyncio
    async def test_mitigation_stage_passes_diagnosis_shared_file_to_sre_agent(self, tmp_path: Path):
        from sregym_agents.crucible.agents.base import AgentResult
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import _run_stage_loop
        from sregym_agents.crucible.tools import SharedState, SRESubmission

        mitigation_path = tmp_path / "mitigation_session_state.md"
        mitigation_path.write_text("# mitigation state\n")
        mitigation_shared = SharedFile(mitigation_path)

        diagnosis_path = tmp_path / "diagnosis_session_state.md"
        diagnosis_path.write_text("# diagnosis state\n")
        diagnosis_shared = SharedFile(diagnosis_path)

        mock_sre = MagicMock()
        mock_sre._config = MagicMock()
        mock_sre._config.stage_outputs_file = None

        async def fake_sre_run(**kwargs):
            assert kwargs["diagnosis_shared_file"] is diagnosis_shared
            state = SharedState()
            state.answer = "Patched ConfigMap/coredns."
            state.answer_justification = "DNS recovered."
            state.submitted = True
            result = AgentResult(
                output=SRESubmission(answer="Patched ConfigMap/coredns.", justification="DNS recovered."),
                completed=True,
            )
            result.state = state  # type: ignore[attr-defined]
            return result

        mock_sre.run = AsyncMock(side_effect=fake_sre_run)

        async def fake_judge_run(**kwargs):
            state = SharedState()
            state.verdict = "APPROVED"
            state.submitted = True
            state.benchmark_block = "<benchmark_result>\nsuccess: True\n</benchmark_result>\n"
            result = AgentResult(output="approved", completed=True)
            result.state = state  # type: ignore[attr-defined]
            return result

        mock_judge = MagicMock()
        mock_judge.run = AsyncMock(side_effect=fake_judge_run)

        await _run_stage_loop(
            sre_agent=mock_sre,
            judge_agent=mock_judge,
            app_info={"app_name": "myapp", "namespace": "default"},
            stage="mitigation",
            max_iters=1,
            shared_file=mitigation_shared,
            diagnosis_shared_file=diagnosis_shared,
            submit_mcp_url="http://localhost:9954/submit/sse",
            renderer=PromptRenderer("v3"),
            usage_collector=UsageCollector(),
            crucible_config=CrucibleConfig(enable_judge=True),
        )


class TestOrchestratorRun:
    @pytest.mark.asyncio
    async def test_run_reinitializes_existing_diagnosis_shared_file(self, tmp_path: Path):
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import StageLoopResult, run

        diagnosis_shared = tmp_path / "diagnosis_session_state.md"
        diagnosis_shared.write_text(
            "# SRE Judged Session State\n"
            "## Session\n"
            "- App: Blueprint Hotel Reservation / Namespace: blueprint-hotel-reservation\n\n"
            "## Diagnosis\n"
            "\n### Iteration 1 — LTM Direct Submission (diagnosis)\n"
            "stale state from previous run\n"
        )
        mitigation_shared = tmp_path / "mitigation_session_state.md"
        driver = MagicMock()
        observed_shared_content: dict[str, str] = {}

        async def fake_run_stage_loop(*args, **kwargs):
            observed_shared_content["diagnosis"] = diagnosis_shared.read_text()
            return StageLoopResult(
                approved=True,
                benchmark_block="<benchmark_result>\nsuccess: True\n</benchmark_result>\n",
                agent_answer="Current run diagnosis",
                agent_justification="Current run justification",
            )

        with patch(
            "sregym_agents.crucible.orchestrator._run_stage_loop", new=AsyncMock(side_effect=fake_run_stage_loop)
        ):
            await run(
                model="test-model",
                app_info={"app_name": "Hotel Reservation", "namespace": "hotel-reservation", "descriptions": ""},
                problem_id="update_incompatible_correlated",
                diagnosis_shared_file=diagnosis_shared,
                mitigation_shared_file=mitigation_shared,
                planned_stages=["diagnosis"],
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=PromptRenderer("v2"),
                crucible_config=CrucibleConfig(prompt_version="v2"),
                driver=driver,
            )

        content = observed_shared_content["diagnosis"]
        assert "Hotel Reservation" in content
        assert "hotel-reservation" in content
        assert "Blueprint Hotel Reservation" not in content
        assert "stale state from previous run" not in content

    @pytest.mark.asyncio
    async def test_playbook_shortcut_passes_diagnosis_shared_file_to_runner(self, tmp_path: Path):
        from sregym_agents.crucible.knowledge_base.root_cause import (
            MitigationFrontMatter,
            MitigationPlaybook,
        )
        from sregym_agents.crucible.orchestrator import _try_playbook_shortcut

        diagnosis_path = tmp_path / "diagnosis_session_state.md"
        diagnosis_path.write_text("# diagnosis state\n**Diagnosis**: deployment/coredns is faulting.\n")
        diagnosis_shared = SharedFile(diagnosis_path)

        mitigation_path = tmp_path / "mitigation_session_state.md"
        mitigation_path.write_text("# mitigation state\n")
        mitigation_shared = SharedFile(mitigation_path)

        playbook_text = MitigationPlaybook(
            front_matter=MitigationFrontMatter(
                slug="coredns-nxdomain",
                root_cause="CoreDNS returns NXDOMAIN for a valid service hostname.",
            ),
            summary="Remove the targeted NXDOMAIN rule from CoreDNS.",
            mitigation_procedure=["1. Patch the CoreDNS ConfigMap to remove the targeted template rule."],
            verification_checks=["1. Confirm the affected Service hostname resolves successfully."],
            rollback_stop_conditions=["Stop if the CoreDNS ConfigMap cannot be identified confidently."],
        ).to_markdown()
        kb_view = MagicMock()
        kb_view.load_mitigation_text.return_value = playbook_text

        with patch(
            "sregym_agents.crucible.tools.run_single_mitigation_playbook",
            new=AsyncMock(
                return_value=MagicMock(
                    applied=False,
                    mitigation_summary="",
                    reasoning="playbook failed",
                )
            ),
        ) as playbook_runner:
            result = await _try_playbook_shortcut(
                run_subagent=AsyncMock(),
                namespace="social-network",
                slug="coredns-nxdomain",
                kb_view=kb_view,
                shared_file=mitigation_shared,
                diagnosis_shared_file=diagnosis_shared,
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=PromptRenderer("v3"),
                usage_collector=UsageCollector(),
                model_id="test-model",
            )

        assert result is None
        assert playbook_runner.await_args is not None
        kwargs = playbook_runner.await_args.kwargs
        assert kwargs["diagnosis_shared_file"] == str(diagnosis_path)
        assert kwargs["diagnosis_shared_content"] == diagnosis_path.read_text()

    @pytest.mark.asyncio
    async def test_run_emits_diagnosis_playbook_candidate_for_successful_novel_diagnosis(self, tmp_path: Path):
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import StageLoopResult, run

        diagnosis_shared = tmp_path / "diagnosis_session_state.md"
        mitigation_shared = tmp_path / "mitigation_session_state.md"
        stage_outputs = tmp_path / "diagnosis_stage_outputs.md"
        stage_outputs.write_text("# stage outputs\n")

        driver = MagicMock()

        diag_result = StageLoopResult(
            approved=True,
            benchmark_block="<benchmark_result>\nsuccess: True\n</benchmark_result>\n",
            agent_answer="CoreDNS misconfiguration",
            agent_justification="NXDOMAIN template for the service",
            agent_causal_chain="coredns template -> NXDOMAIN -> client failures",
            stage_outputs_file=stage_outputs,
            confirmed_slugs=[],
            message_history=[{"role": "assistant", "content": "successful diagnosis context"}],
        )

        with (
            patch("sregym_agents.crucible.orchestrator._run_stage_loop", new=AsyncMock(return_value=diag_result)),
            patch(
                "sregym_agents.crucible.agents.recovery_agent.RecoveryAgent.build_success_diagnosis_playbook_candidate",
                new=AsyncMock(
                    return_value=DiagnosisPlaybookDraft(
                        slug="coredns-nxdomain",
                        root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
                        when_to_consider=["Application logs show service-hostname resolution failures."],
                        disambiguators=["Backend services exist but lookups still return NXDOMAIN."],
                        summary="Check whether CoreDNS is intentionally returning NXDOMAIN for service names.",
                        triage_checks=["1. Inspect application logs for host-resolution errors."],
                        fault_localization_checks=[
                            "1. Trace the failing request path to the dependent backend hostname."
                        ],
                        verification_checks=["1. Inspect CoreDNS configuration for matching NXDOMAIN rules."],
                        required_evidence=["CoreDNS config contains a rule matching the failing service FQDN."],
                        known_confounders=["The Service object is missing."],
                    )
                ),
            ) as build_success_diagnosis,
        ):
            result = await run(
                model="test-model",
                app_info={"app_name": "Social Network", "namespace": "social-network", "descriptions": ""},
                problem_id="service_dns_resolution_failure__v_social_network_text-service",
                diagnosis_shared_file=diagnosis_shared,
                mitigation_shared_file=mitigation_shared,
                planned_stages=["diagnosis"],
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=PromptRenderer("v3"),
                crucible_config=CrucibleConfig(include_benchmark_results=True, prompt_version="v3"),
                driver=driver,
            )

        assert result["diagnosis_succeeded"] is True
        assert result["diagnosis_playbook_candidate"]["slug"] == "coredns-nxdomain"
        assert "CoreDNS misconfiguration" in result["diagnosis_run_md"]
        assert result["recovery_diagnosis_run_md"] is None
        build_success_diagnosis.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_emits_mitigation_playbook_candidate_for_successful_stage(self, tmp_path: Path):
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import StageLoopResult, run

        diagnosis_shared = tmp_path / "diagnosis_session_state.md"
        mitigation_shared = tmp_path / "mitigation_session_state.md"
        diagnosis_stage_outputs = tmp_path / "diagnosis_stage_outputs.md"
        mitigation_stage_outputs = tmp_path / "mitigation_stage_outputs.md"
        diagnosis_stage_outputs.write_text("# diagnosis outputs\n")
        mitigation_stage_outputs.write_text("# mitigation outputs\n")

        driver = MagicMock()
        driver.run = AsyncMock()

        diag_result = StageLoopResult(
            approved=True,
            benchmark_block=(
                "<benchmark_result>\nsuccess: True\n<oracle>\n"
                '{"Diagnosis":{"matched_candidate_index":0}}\n'
                "</oracle>\n</benchmark_result>\n"
            ),
            agent_answer="CoreDNS misconfiguration",
            agent_justification="NXDOMAIN template for the service",
            agent_causal_chain="coredns template -> NXDOMAIN -> client failures",
            stage_outputs_file=diagnosis_stage_outputs,
            confirmed_slugs=["coredns-nxdomain"],
            message_history=[{"role": "assistant", "content": "diagnosis context"}],
        )
        mit_result = StageLoopResult(
            approved=True,
            benchmark_block="<benchmark_result>\nsuccess: True\n</benchmark_result>\n",
            agent_answer="Patched ConfigMap/coredns to remove the NXDOMAIN rules.",
            agent_justification="The affected service names resolve again.",
            stage_outputs_file=mitigation_stage_outputs,
            message_history=[{"role": "assistant", "content": "mitigation context"}],
        )

        with (
            patch(
                "sregym_agents.crucible.orchestrator._run_stage_loop",
                new=AsyncMock(side_effect=[diag_result, mit_result]),
            ),
            patch("sregym_agents.crucible.orchestrator.poll_stage", new=AsyncMock()),
            patch("sregym_agents.crucible.orchestrator._try_playbook_shortcut", new=AsyncMock(return_value=None)),
            patch(
                "sregym_agents.crucible.agents.recovery_agent.RecoveryAgent.build_success_mitigation_playbook_candidate",
                new=AsyncMock(
                    return_value=MitigationPlaybookDraft(
                        slug="coredns-nxdomain",
                        root_cause="CoreDNS misconfiguration",
                        summary="Remove the CoreDNS override and verify DNS recovery.",
                        mitigation_procedure=["1. Patch ConfigMap/coredns to remove the bad template rules."],
                        verification_checks=["1. Verify the affected service names resolve again."],
                        rollback_stop_conditions=["Stop if the correct CoreDNS change cannot be identified."],
                    )
                ),
            ) as build_mitigation,
        ):
            result = await run(
                model="test-model",
                app_info={"app_name": "Social Network", "namespace": "social-network", "descriptions": ""},
                problem_id="service_dns_resolution_failure__v_social_network_text-service",
                diagnosis_shared_file=diagnosis_shared,
                mitigation_shared_file=mitigation_shared,
                planned_stages=["diagnosis", "mitigation"],
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=PromptRenderer("v3"),
                crucible_config=CrucibleConfig(include_benchmark_results=True, prompt_version="v3"),
                driver=driver,
            )

        assert result["mitigation_succeeded"] is True
        assert result["mitigation_playbook_candidate"]["slug"] == "coredns-nxdomain"
        assert "Patched ConfigMap/coredns" in result["mitigation_run_md"]
        assert result["recovery_mitigation_run_md"] is None
        build_mitigation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_does_not_emit_success_diagnosis_candidate_when_playbook_already_matched(self, tmp_path: Path):
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import StageLoopResult, run

        diagnosis_shared = tmp_path / "diagnosis_session_state.md"
        mitigation_shared = tmp_path / "mitigation_session_state.md"
        stage_outputs = tmp_path / "diagnosis_stage_outputs.md"
        stage_outputs.write_text("# stage outputs\n")

        driver = MagicMock()
        driver.run = AsyncMock()

        diag_result = StageLoopResult(
            approved=True,
            benchmark_block=(
                "<benchmark_result>\nsuccess: True\n<oracle>\n"
                '{"Diagnosis":{"matched_candidate_index":0}}\n'
                "</oracle>\n</benchmark_result>\n"
            ),
            agent_answer="CoreDNS misconfiguration",
            agent_justification="NXDOMAIN template for the service",
            agent_causal_chain="coredns template -> NXDOMAIN -> client failures",
            stage_outputs_file=stage_outputs,
            confirmed_slugs=["coredns-nxdomain"],
            message_history=[{"role": "assistant", "content": "successful diagnosis context"}],
        )

        with (
            patch("sregym_agents.crucible.orchestrator._run_stage_loop", new=AsyncMock(return_value=diag_result)),
            patch(
                "sregym_agents.crucible.agents.recovery_agent.RecoveryAgent.build_success_diagnosis_playbook_candidate",
                new=AsyncMock(),
            ) as build_success_diagnosis,
        ):
            result = await run(
                model="test-model",
                app_info={"app_name": "Social Network", "namespace": "social-network", "descriptions": ""},
                problem_id="service_dns_resolution_failure__v_social_network_text-service",
                diagnosis_shared_file=diagnosis_shared,
                mitigation_shared_file=mitigation_shared,
                planned_stages=["diagnosis"],
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=PromptRenderer("v3"),
                crucible_config=CrucibleConfig(include_benchmark_results=True, prompt_version="v3"),
                driver=driver,
            )

        assert result["diagnosis_succeeded"] is True
        assert result["diagnosis_playbook_candidate"] is None
        build_success_diagnosis.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_defers_success_diagnosis_authoring_until_after_mitigation_and_reuses_slug(self, tmp_path: Path):
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import StageLoopResult, run

        diagnosis_shared = tmp_path / "diagnosis_session_state.md"
        mitigation_shared = tmp_path / "mitigation_session_state.md"
        diagnosis_stage_outputs = tmp_path / "diagnosis_stage_outputs.md"
        mitigation_stage_outputs = tmp_path / "mitigation_stage_outputs.md"
        diagnosis_stage_outputs.write_text("# diagnosis outputs\n")
        mitigation_stage_outputs.write_text("# mitigation outputs\n")

        driver = MagicMock()
        driver.run = AsyncMock()

        diag_result = StageLoopResult(
            approved=True,
            benchmark_block="<benchmark_result>\nsuccess: True\n</benchmark_result>\n",
            agent_answer="CoreDNS misconfiguration",
            agent_justification="NXDOMAIN template for the service",
            agent_causal_chain="coredns template -> NXDOMAIN -> client failures",
            stage_outputs_file=diagnosis_stage_outputs,
            confirmed_slugs=[],
            message_history=[{"role": "assistant", "content": "successful diagnosis context"}],
        )
        mit_result = StageLoopResult(
            approved=True,
            benchmark_block="<benchmark_result>\nsuccess: True\n</benchmark_result>\n",
            agent_answer="Patched ConfigMap/coredns to remove the NXDOMAIN rules.",
            agent_justification="The affected service names resolve again.",
            stage_outputs_file=mitigation_stage_outputs,
            confirmed_slugs=[],
            message_history=[{"role": "assistant", "content": "successful mitigation context"}],
        )
        call_order: list[str] = []

        async def fake_run_stage_loop(*args, **kwargs):
            stage = kwargs.get("stage", args[3])
            call_order.append(stage)
            if stage == "diagnosis":
                return diag_result
            return mit_result

        async def fake_build_success_diagnosis(self, **kwargs):
            call_order.append("diagnosis-playbook")
            return DiagnosisPlaybookDraft(
                slug="coredns-nxdomain",
                root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
                when_to_consider=["Application logs show service-hostname resolution failures."],
                disambiguators=["Backend services exist but lookups still return NXDOMAIN."],
                summary="Check whether CoreDNS is intentionally returning NXDOMAIN for service names.",
                triage_checks=["1. Inspect application logs for host-resolution errors."],
                fault_localization_checks=["1. Trace the failing request path to the dependent backend hostname."],
                verification_checks=["1. Inspect CoreDNS configuration for matching NXDOMAIN rules."],
                required_evidence=["CoreDNS config contains a rule matching the failing service FQDN."],
                known_confounders=["The Service object is missing."],
            )

        async def fake_build_success_mitigation(self, **kwargs):
            call_order.append("mitigation-playbook")
            assert kwargs["root_cause_slug"] == "coredns-nxdomain"
            assert kwargs["root_cause"] == "CoreDNS returns NXDOMAIN for targeted service names."
            return MitigationPlaybookDraft(
                slug="coredns-nxdomain",
                root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
                summary="Remove the CoreDNS override and verify DNS recovery.",
                mitigation_procedure=["1. Patch ConfigMap/coredns to remove the bad template rules."],
                verification_checks=["1. Verify the affected service names resolve again."],
                rollback_stop_conditions=["Stop if the correct CoreDNS change cannot be identified."],
            )

        with (
            patch(
                "sregym_agents.crucible.orchestrator._run_stage_loop",
                new=fake_run_stage_loop,
            ),
            patch("sregym_agents.crucible.orchestrator.poll_stage", new=AsyncMock()),
            patch("sregym_agents.crucible.orchestrator._try_playbook_shortcut", new=AsyncMock(return_value=None)),
            patch(
                "sregym_agents.crucible.agents.recovery_agent.RecoveryAgent.build_success_diagnosis_playbook_candidate",
                new=fake_build_success_diagnosis,
            ),
            patch(
                "sregym_agents.crucible.agents.recovery_agent.RecoveryAgent.build_success_mitigation_playbook_candidate",
                new=fake_build_success_mitigation,
            ),
        ):
            result = await run(
                model="test-model",
                app_info={"app_name": "Social Network", "namespace": "social-network", "descriptions": ""},
                problem_id="service_dns_resolution_failure__v_social_network_text-service",
                diagnosis_shared_file=diagnosis_shared,
                mitigation_shared_file=mitigation_shared,
                planned_stages=["diagnosis", "mitigation"],
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=PromptRenderer("v3"),
                crucible_config=CrucibleConfig(include_benchmark_results=True, prompt_version="v3"),
                driver=driver,
            )

        assert call_order == ["diagnosis", "mitigation", "diagnosis-playbook", "mitigation-playbook"]
        assert result["diagnosis_playbook_candidate"]["slug"] == "coredns-nxdomain"
        assert result["mitigation_playbook_candidate"]["slug"] == "coredns-nxdomain"

    @pytest.mark.asyncio
    async def test_run_defers_diagnosis_recovery_until_after_mitigation(self, tmp_path: Path):
        from sregym_agents.crucible.agents.recovery_agent import RecoveryRunResult
        from sregym_agents.crucible.config import CrucibleConfig
        from sregym_agents.crucible.orchestrator import StageLoopResult, run
        from sregym_agents.crucible.tools import SRESubmission

        diagnosis_shared = tmp_path / "diagnosis_session_state.md"
        mitigation_shared = tmp_path / "mitigation_session_state.md"
        diagnosis_stage_outputs = tmp_path / "diagnosis_stage_outputs.md"
        mitigation_stage_outputs = tmp_path / "mitigation_stage_outputs.md"
        diagnosis_stage_outputs.write_text("# diagnosis outputs\n")
        mitigation_stage_outputs.write_text("# mitigation outputs\n")

        driver = MagicMock()
        driver.run = AsyncMock()

        diag_result = StageLoopResult(
            approved=False,
            benchmark_block="<benchmark_result>\nsuccess: False\n</benchmark_result>\n",
            agent_answer="Wrong diagnosis",
            agent_justification="Looked at the wrong component.",
            stage_outputs_file=diagnosis_stage_outputs,
        )
        mit_result = StageLoopResult(
            approved=True,
            benchmark_block="<benchmark_result>\nsuccess: True\n</benchmark_result>\n",
            agent_answer="Patched ConfigMap/coredns to remove the bad template.",
            agent_justification="DNS resolution recovered after the patch.",
            stage_outputs_file=mitigation_stage_outputs,
        )
        call_order: list[str] = []

        async def fake_run_stage_loop(*args, **kwargs):
            stage = kwargs.get("stage", args[3])
            call_order.append(stage)
            if stage == "diagnosis":
                return diag_result
            return mit_result

        async def fake_run_diagnosis_recovery(self, **kwargs):
            call_order.append("recovery-diagnosis")
            return RecoveryRunResult(
                submission=SRESubmission(
                    answer="Grounded CoreDNS diagnosis",
                    justification="Benchmark-guided recovery found the NXDOMAIN template.",
                    causal_chain="bad coredns template -> NXDOMAIN -> client failures",
                ),
                message_history=[{"role": "assistant", "content": "recovery context"}],
            )

        async def fake_build_diagnosis_candidate(self, **kwargs):
            call_order.append("diagnosis-playbook")
            return DiagnosisPlaybookDraft(
                slug="coredns-nxdomain",
                root_cause="CoreDNS returns NXDOMAIN for targeted service names.",
                when_to_consider=["Application logs show service-hostname resolution failures."],
                disambiguators=["Backend services exist but lookups still return NXDOMAIN."],
                summary="Check whether CoreDNS is intentionally returning NXDOMAIN for service names.",
                triage_checks=["1. Inspect application logs for host-resolution errors."],
                fault_localization_checks=["1. Trace the failing request path to the dependent backend hostname."],
                verification_checks=["1. Inspect CoreDNS configuration for matching NXDOMAIN rules."],
                required_evidence=["CoreDNS config contains a rule matching the failing service FQDN."],
                known_confounders=["The Service object is missing."],
            )

        async def fake_poll_stage(*args, **kwargs):
            return None

        async def fake_try_recovery_playbook_shortcut(*args, **kwargs):
            return None

        with (
            patch("sregym_agents.crucible.orchestrator._run_stage_loop", new=fake_run_stage_loop),
            patch(
                "sregym_agents.crucible.orchestrator.poll_stage",
                new=fake_poll_stage,
            ),
            patch(
                "sregym_agents.crucible.orchestrator._try_recovery_playbook_shortcut",
                new=fake_try_recovery_playbook_shortcut,
            ),
            patch(
                "sregym_agents.crucible.agents.recovery_agent.RecoveryAgent.run_diagnosis",
                new=fake_run_diagnosis_recovery,
            ),
            patch(
                "sregym_agents.crucible.agents.recovery_agent.RecoveryAgent.build_diagnosis_playbook_candidate",
                new=fake_build_diagnosis_candidate,
            ),
        ):
            result = await run(
                model="test-model",
                app_info={"app_name": "Social Network", "namespace": "social-network", "descriptions": ""},
                problem_id="service_dns_resolution_failure__v_social_network_text-service",
                diagnosis_shared_file=diagnosis_shared,
                mitigation_shared_file=mitigation_shared,
                planned_stages=["diagnosis", "mitigation"],
                submit_mcp_url="http://localhost:9954/submit/sse",
                renderer=PromptRenderer("v3"),
                crucible_config=CrucibleConfig(include_benchmark_results=True, prompt_version="v3"),
                driver=driver,
            )

        assert call_order == ["diagnosis", "mitigation", "recovery-diagnosis", "diagnosis-playbook"]
        assert result["mitigation_succeeded"] is True
        assert "Grounded CoreDNS diagnosis" in result["recovery_diagnosis_run_md"]
