# pyright: reportPrivateUsage=false
"""Tests for the LTM mitigation phase: per-strategy execution subagents,
short-circuit, and ``search_prior_mitigations`` integration.

Symmetric to ``test_ltm_direct_submit.py``: when
``CrucibleConfig.enable_ltm_verified_direct_submit`` is on and
``search_prior_mitigations`` finds a mitigation playbook for a retrieved
strategy AND the per-strategy mitigation subagent successfully applies it,
the SRE main agent is short-circuited via ``LTMMitigationShortCircuit`` and
the orchestrator submits the applied mitigation directly to the benchmark.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from benchmarks.sregym.agents.crucible._prompts import PromptRenderer
from benchmarks.sregym.agents.crucible.tools import (
    SharedFile,
    SharedState,
    SREDeps,
)
from benchmarks.sregym.agents.crucible.tools._kb_tools import (
    LTMMitigationShortCircuit,
    MitigationApplication,
    MitigationSearchResult,
    MitigationStrategy,
    VerifiedMitigationSearchResult,
    _format_applied_mitigations_md,
    _run_mitigation_phase,
    search_prior_mitigations,
)
from libs.pydantic_agent import UsageCollector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


VALID_MITIGATION_PLAYBOOK_MD = (
    "# Mitigation Playbook: Missing env\n"
    "\n"
    "<!-- meta -->\n"
    "slug: missing_env\n"
    "class_name: Missing env\n"
    "seen: 1\n"
    "created_from: success\n"
    "last_updated: 2026-04-09T12:00:00Z\n"
    "<!-- /meta -->\n"
    "\n"
    "## Summary\n"
    "Patch the failing Deployment.\n"
    "\n"
    "## Applicability\n"
    "- Required env var missing from container env spec.\n"
    "\n"
    "## Mitigation Procedure\n"
    "1. **Action:** kubectl set env deploy/<DEPLOYMENT> -n <NAMESPACE> <ENV_VAR>=<VALUE>\n"
    "   **Expected outcome:** rollout starts.\n"
    "\n"
    "## Post-Mitigation Verification\n"
    "1. **Action:** kubectl get pods -n <NAMESPACE>\n"
    "   **Expected outcome:** Running.\n"
    "\n"
    "## Required Evidence\n"
    "- [ ] Pods Running with restartCount=0.\n"
    "\n"
    "## Known Pitfalls\n"
    "- (none observed yet)\n"
    "\n"
    "## Failure Patterns\n"
    "- (none observed yet)\n"
)


def _strategy(name: str) -> MitigationStrategy:
    return MitigationStrategy(
        root_cause_class=f"class-{name}",
        mitigation_approach=f"approach-{name}",
        detailed_steps=f"steps-{name}",
        incident_refs=[],
        caveats="",
    )


def _application(idx: int, name: str, *, applied: bool) -> MitigationApplication:
    return MitigationApplication(
        strategy_index=idx,
        root_cause_class=f"class-{name}",
        applied=applied,
        applied_steps=[f"step-{name}"] if applied else [],
        verification_evidence=[f"evidence-{name}"] if applied else [],
        mitigation_summary=f"summary-{name}" if applied else "",
        reasoning=f"reasoning-{name}",
    )


def _make_lt_summary(tmp_path: Path) -> Path:
    """Write a long-term summary that maps the test class names to slugs."""
    path = tmp_path / "lt_summary.md"
    path.write_text(
        "## Long-Term Summary\n"
        "\n"
        "### Root Cause: class-hit\n"
        "#### Slug: missing_env\n"
        "\n"
        "### Root Cause: class-miss\n"
        "#### Slug: missing_env_other\n"
    )
    return path


def _write_mitigation_playbook(mitigation_playbooks_dir: Path) -> None:
    mitigation_playbooks_dir.mkdir(parents=True, exist_ok=True)
    (mitigation_playbooks_dir / "missing_env.md").write_text(VALID_MITIGATION_PLAYBOOK_MD)


def _make_sre_deps(
    tmp_path: Path,
    *,
    enable_ltm_verified_direct_submit: bool = False,
    iteration: int = 1,
    with_mitigation_playbook: bool = True,
    with_lt_summary: bool = True,
) -> SREDeps:
    from benchmarks.sregym.agents.crucible.config import CrucibleConfig

    shared_path = tmp_path / "shared.md"
    shared_path.write_text("# Session\n")
    mitigation_playbooks_dir: Path | None = None
    if with_mitigation_playbook:
        mitigation_playbooks_dir = tmp_path / "mitigation_playbooks"
        _write_mitigation_playbook(mitigation_playbooks_dir)
    lt_summary_file: Path | None = None
    if with_lt_summary:
        lt_summary_file = _make_lt_summary(tmp_path)
    return SREDeps(
        namespace="ns",
        shared_file=SharedFile(shared_path),
        iteration=iteration,
        stage="mitigation",
        renderer=PromptRenderer("v3"),
        state=SharedState(),
        config=CrucibleConfig(enable_ltm_verified_direct_submit=enable_ltm_verified_direct_submit),
        lt_summary_file=lt_summary_file,
        model_id="test",  # type: ignore[arg-type]
        ltm_call_budget=1,
        usage_collector=UsageCollector(),
        mitigation_playbooks_dir=mitigation_playbooks_dir,
    )


def _make_ctx(deps: SREDeps) -> MagicMock:
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def _set_run_subagent_for_retrieval(deps: SREDeps, search_result: MitigationSearchResult):
    """Set deps.run_subagent to return the given search result directly."""
    deps.run_subagent = AsyncMock(return_value=search_result)


# ---------------------------------------------------------------------------
# _format_applied_mitigations_md
# ---------------------------------------------------------------------------


class TestFormatAppliedMitigations:
    def test_no_applications(self):
        verified = VerifiedMitigationSearchResult(
            strategies=[_strategy("hit")],
            applications=[],
            successful_applications=[],
        )
        out = _format_applied_mitigations_md(verified, iteration=2)
        assert "Iteration 2" in out
        assert "LTM Mitigation Phase" in out
        assert "No mitigation playbooks" in out

    def test_mixed_applied_and_failed(self):
        applied = _application(0, "hit", applied=True)
        failed = _application(1, "miss", applied=False)
        verified = VerifiedMitigationSearchResult(
            strategies=[_strategy("hit"), _strategy("miss")],
            applications=[applied, failed],
            successful_applications=[applied],
        )
        out = _format_applied_mitigations_md(verified, iteration=1)
        assert "Applied 2 strategy(ies)" in out or "2 strategy" in out
        assert "1 successful" in out
        assert "APPLIED" in out
        assert "failed" in out
        assert "summary-hit" in out
        assert "reasoning-hit" in out
        assert "reasoning-miss" in out


# ---------------------------------------------------------------------------
# _run_mitigation_phase
# ---------------------------------------------------------------------------


class TestRunMitigationPhase:
    @pytest.mark.asyncio
    async def test_skips_strategy_with_no_playbook(self, tmp_path: Path):
        """A strategy whose root_cause_class doesn't resolve to a playbook is
        recorded as not-applied with an explanatory reasoning."""
        # No lt_summary, no playbooks dir — slug resolution should fail.
        playbooks_dir = tmp_path / "mitigation_playbooks"
        playbooks_dir.mkdir(parents=True, exist_ok=True)

        mock_run_subagent = AsyncMock()

        verified = await _run_mitigation_phase(
            strategies=[_strategy("hit")],
            search_result=MitigationSearchResult(strategies=[_strategy("hit")]),
            namespace="ns",
            stage="mitigation",
            run_subagent=mock_run_subagent,
            renderer=PromptRenderer("v3"),
            mitigation_playbooks_dir=playbooks_dir,
            lt_summary_file=None,
            failed_attempts="",
            usage_collector=UsageCollector(),
        )

        assert len(verified.applications) == 1
        assert verified.applications[0].applied is False
        assert "playbook" in verified.applications[0].reasoning.lower()
        assert verified.successful_applications == []

    @pytest.mark.asyncio
    async def test_runs_sequentially_and_stops_after_first_success(self, tmp_path: Path):
        """When the first strategy applies successfully, no further subagents are spawned."""
        playbooks_dir = tmp_path / "mitigation_playbooks"
        _write_mitigation_playbook(playbooks_dir)
        # Both classes resolve to the same slug for simplicity.
        lt_summary = tmp_path / "lt_summary.md"
        lt_summary.write_text(
            "## Long-Term Summary\n"
            "### Root Cause: class-hit\n"
            "#### Slug: missing_env\n"
            "### Root Cause: class-second\n"
            "#### Slug: missing_env\n"
        )

        # run_subagent returns applied=True for the first call.
        successful_app = _application(0, "hit", applied=True)
        mock_run_subagent = AsyncMock(return_value=successful_app)

        verified = await _run_mitigation_phase(
            strategies=[_strategy("hit"), _strategy("second")],
            search_result=MitigationSearchResult(strategies=[_strategy("hit"), _strategy("second")]),
            namespace="ns",
            stage="mitigation",
            run_subagent=mock_run_subagent,
            renderer=PromptRenderer("v3"),
            mitigation_playbooks_dir=playbooks_dir,
            lt_summary_file=lt_summary,
            failed_attempts="",
            usage_collector=UsageCollector(),
        )

        # Only ONE subagent call was made (early exit on success).
        assert mock_run_subagent.call_count == 1
        assert len(verified.applications) == 1
        assert verified.applications[0].applied is True
        assert verified.successful_applications == verified.applications

    @pytest.mark.asyncio
    async def test_continues_when_first_strategy_fails(self, tmp_path: Path):
        """If strategy 0 fails, strategy 1 is attempted."""
        playbooks_dir = tmp_path / "mitigation_playbooks"
        _write_mitigation_playbook(playbooks_dir)
        lt_summary = tmp_path / "lt_summary.md"
        lt_summary.write_text(
            "## Long-Term Summary\n"
            "### Root Cause: class-hit\n"
            "#### Slug: missing_env\n"
            "### Root Cause: class-second\n"
            "#### Slug: missing_env\n"
        )

        failed_app = _application(0, "hit", applied=False)
        applied_app = _application(1, "second", applied=True)

        results = [failed_app, applied_app]

        def next_result(**_kwargs: Any) -> MitigationApplication:
            return results.pop(0)

        mock_run_subagent = AsyncMock(side_effect=next_result)

        verified = await _run_mitigation_phase(
            strategies=[_strategy("hit"), _strategy("second")],
            search_result=MitigationSearchResult(strategies=[_strategy("hit"), _strategy("second")]),
            namespace="ns",
            stage="mitigation",
            run_subagent=mock_run_subagent,
            renderer=PromptRenderer("v3"),
            mitigation_playbooks_dir=playbooks_dir,
            lt_summary_file=lt_summary,
            failed_attempts="",
            usage_collector=UsageCollector(),
        )

        assert mock_run_subagent.call_count == 2
        assert len(verified.applications) == 2
        assert verified.applications[0].applied is False
        assert verified.applications[1].applied is True
        assert len(verified.successful_applications) == 1


# ---------------------------------------------------------------------------
# search_prior_mitigations integration
# ---------------------------------------------------------------------------


def _patch_mitigation_phase(verified: VerifiedMitigationSearchResult):
    return patch(
        "benchmarks.sregym.agents.crucible.tools._kb_tools._run_mitigation_phase",
        AsyncMock(return_value=verified),
    )


class TestSearchPriorMitigations:
    @pytest.mark.asyncio
    async def test_no_playbooks_dir_returns_plain_search_result(self, tmp_path: Path):
        """When mitigation_playbooks_dir is unset, behavior matches the legacy path:
        return the JSON of the plain MitigationSearchResult; no phase runs."""
        deps = _make_sre_deps(
            tmp_path,
            with_mitigation_playbook=False,
        )
        ctx = _make_ctx(deps)

        retrieved = MitigationSearchResult(strategies=[_strategy("hit")])

        _set_run_subagent_for_retrieval(deps, retrieved)
        result = await search_prior_mitigations(ctx, "diagnosed root cause")

        # Plain search result, no "applications" key.
        assert "strategies" in result
        assert "applications" not in result
        # No short-circuit raised.
        # No mitigation phase block in shared file.
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Mitigation Phase" not in shared_text

    @pytest.mark.asyncio
    async def test_logs_phase_results_when_flag_off(self, tmp_path: Path):
        deps = _make_sre_deps(
            tmp_path,
            enable_ltm_verified_direct_submit=False,
        )
        ctx = _make_ctx(deps)

        retrieved = MitigationSearchResult(strategies=[_strategy("hit")])
        applied_app = _application(0, "hit", applied=True)
        verified = VerifiedMitigationSearchResult(
            strategies=[_strategy("hit")],
            applications=[applied_app],
            successful_applications=[applied_app],
        )

        _set_run_subagent_for_retrieval(deps, retrieved)
        with _patch_mitigation_phase(verified):
            result = await search_prior_mitigations(ctx, "root cause")

        # Returned the verified JSON (not raised).
        assert "applications" in result
        # Shared file gained the mitigation phase block.
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Mitigation Phase" in shared_text
        assert "summary-hit" in shared_text

    @pytest.mark.asyncio
    async def test_short_circuits_when_flag_on_and_applied(self, tmp_path: Path):
        deps = _make_sre_deps(
            tmp_path,
            enable_ltm_verified_direct_submit=True,
            iteration=4,
        )
        ctx = _make_ctx(deps)

        retrieved = MitigationSearchResult(strategies=[_strategy("hit")])
        applied_app = _application(0, "hit", applied=True)
        verified = VerifiedMitigationSearchResult(
            strategies=[_strategy("hit")],
            applications=[applied_app],
            successful_applications=[applied_app],
        )

        _set_run_subagent_for_retrieval(deps, retrieved)
        with _patch_mitigation_phase(verified):
            with pytest.raises(LTMMitigationShortCircuit) as excinfo:
                await search_prior_mitigations(ctx, "root cause")

        sig = excinfo.value
        assert sig.applied == ["summary-hit"]
        assert sig.iteration == 4

        # Phase block was appended *before* the raise.
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Mitigation Phase" in shared_text

    @pytest.mark.asyncio
    async def test_no_short_circuit_when_flag_on_but_no_applied(self, tmp_path: Path):
        deps = _make_sre_deps(
            tmp_path,
            enable_ltm_verified_direct_submit=True,
        )
        ctx = _make_ctx(deps)

        retrieved = MitigationSearchResult(strategies=[_strategy("hit")])
        failed_app = _application(0, "hit", applied=False)
        verified = VerifiedMitigationSearchResult(
            strategies=[_strategy("hit")],
            applications=[failed_app],
            successful_applications=[],
        )

        _set_run_subagent_for_retrieval(deps, retrieved)
        with _patch_mitigation_phase(verified):
            result = await search_prior_mitigations(ctx, "root cause")

        assert "applications" in result
        shared_text = (tmp_path / "shared.md").read_text()
        assert "LTM Mitigation Phase" in shared_text
