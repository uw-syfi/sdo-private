"""Dedicated KB review worker for the root-cause playbook KB."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic_ai import RunContext  # noqa: TC002 — required at runtime for pydantic-ai tool introspection

from libs.pydantic_agent import UsageCollector
from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.config import crucible_config_from_kb_task
from sregym_agents.crucible.kb_update_queue import (
    KbUpdateTask,
    list_pending_tasks,
    move_to_completed,
    move_to_failed,
)
from sregym_agents.crucible.knowledge_base.incident_review import (
    DiagnosisPlaybookDraft,
    MitigationPlaybookDraft,
    ReviewDecision,
    TriageAreaCandidate,
)
from sregym_agents.crucible.knowledge_base.root_cause import (
    DiagnosisFrontMatter,
    DiagnosisPlaybook,
    MitigationFrontMatter,
    MitigationPlaybook,
    RootCauseStore,
)
from sregym_agents.crucible.knowledge_base.structured import StructuredKnowledgeBase
from sregym_agents.crucible.tools import TriagePriors

if TYPE_CHECKING:
    from collections.abc import Callable

    from sregym_agents.crucible.agents.base import AgentDriver, AgentResult

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2
DEFAULT_IDLE_TIMEOUT = 300
_TASK_MAX_RETRIES = 3
_TASK_RETRY_DELAY = 5.0


def _format_cards(cards: list[DiagnosisFrontMatter]) -> str:
    if not cards:
        return "(no diagnosis playbooks available)"
    parts: list[str] = []
    for card in cards:
        parts.append(f"- slug: {card.slug}")
        parts.append(f"  root_cause: {card.root_cause}")
        parts.append("  when_to_consider:")
        parts.extend(f"    - {item}" for item in card.when_to_consider)
        parts.append("  disambiguators:")
        parts.extend(f"    - {item}" for item in card.disambiguators)
    return "\n".join(parts)


def _make_read_playbook_tool(store: RootCauseStore) -> Callable[..., str]:
    """Build a pydantic-ai tool that reads a playbook's full content from ``store``.

    The returned tool lets the reviewer/classifier fetch full markdown
    (summary, triage_checks, verification_checks, required_evidence, etc.) for
    any existing playbook — the default ``diagnosis_cards`` context only
    includes front matter.
    """

    def read_playbook(
        ctx: RunContext[Any],
        playbook_type: Literal["diagnosis", "mitigation"],
        slug: str,
    ) -> str:
        """Read the full content of a playbook from the knowledge base.

        Args:
            playbook_type: ``"diagnosis"`` or ``"mitigation"``.
            slug: The playbook's stable slug (from ``Existing diagnosis playbooks``).

        Returns:
            The full playbook markdown (front matter + all sections). Raises
            ``ValueError`` if the slug is not found or the type is invalid.
        """
        if playbook_type == "diagnosis":
            playbook: DiagnosisPlaybook | MitigationPlaybook | None = store.load_diagnosis(slug)
        elif playbook_type == "mitigation":
            playbook = store.load_mitigation(slug)
        else:
            raise ValueError(f"Invalid playbook_type: {playbook_type!r}. Must be 'diagnosis' or 'mitigation'.")

        if playbook is None:
            raise ValueError(f"{playbook_type} playbook not found: slug={slug!r}")
        return playbook.to_markdown()

    return read_playbook


async def _review_diagnosis_candidate(
    *,
    driver: AgentDriver,
    renderer: PromptRenderer,
    diagnosis_run_md: str,
    recovery_diagnosis_run_md: str | None,
    candidate_playbook: DiagnosisPlaybookDraft,
    cards: list[DiagnosisFrontMatter],
    usage_collector: UsageCollector,
    candidate_origin: str = "recovery",
    store: RootCauseStore,
) -> ReviewDecision:
    if candidate_origin == "success":
        prompt = renderer.render(
            "kb_review_success_diagnosis",
            diagnosis_cards=_format_cards(cards),
            diagnosis_run_md=diagnosis_run_md,
            candidate_playbook_json=json.dumps(candidate_playbook.model_dump(mode="python"), indent=2),
        )
    else:
        prompt = renderer.render(
            "kb_review_failure",
            diagnosis_cards=_format_cards(cards),
            diagnosis_run_md=diagnosis_run_md,
            recovery_diagnosis_run_md=recovery_diagnosis_run_md or "(none)",
            candidate_playbook_json=json.dumps(candidate_playbook.model_dump(mode="python"), indent=2),
        )
    result: AgentResult[ReviewDecision] = await driver.run(
        prompt=prompt,
        output_type=ReviewDecision,
        agent_name="kb-review-classifier",
        usage_collector=usage_collector,
        tools=[_make_read_playbook_tool(store)],
    )
    return result.unwrap("kb-review-classifier")


async def _merge_playbooks(
    *,
    driver: AgentDriver,
    renderer: PromptRenderer,
    candidate_playbook: DiagnosisPlaybookDraft,
    existing_playbooks: list[DiagnosisPlaybook],
    diagnosis_run_md: str,
    recovery_diagnosis_run_md: str | None,
    usage_collector: UsageCollector,
) -> DiagnosisPlaybookDraft:
    prompt = renderer.render(
        "kb_merge_diagnosis_playbooks",
        candidate_playbook_json=json.dumps(candidate_playbook.model_dump(mode="python"), indent=2),
        existing_playbooks_md="\n\n".join(pb.to_markdown() for pb in existing_playbooks),
        diagnosis_run_md=diagnosis_run_md,
        recovery_diagnosis_run_md=recovery_diagnosis_run_md or "(none)",
    )
    result: AgentResult[DiagnosisPlaybookDraft] = await driver.run(
        prompt=prompt,
        output_type=DiagnosisPlaybookDraft,
        agent_name="kb-merge-playbooks",
        usage_collector=usage_collector,
    )
    return result.unwrap("kb-merge-playbooks")


async def _refine_triage_priors(
    *,
    driver: AgentDriver,
    renderer: PromptRenderer,
    triage_area_candidate: TriageAreaCandidate,
    existing_priors: TriagePriors,
    diagnosis_run_md: str,
    recovery_diagnosis_run_md: str | None,
    usage_collector: UsageCollector,
) -> TriagePriors:
    prompt = renderer.render(
        "kb/refine_triage_priors",
        existing_triage_priors_yaml=yaml.safe_dump(
            existing_priors.model_dump(mode="python"),
            sort_keys=False,
        ).strip(),
        triage_area_candidate_json=json.dumps(triage_area_candidate.model_dump(mode="python"), indent=2),
        diagnosis_run_md=diagnosis_run_md,
        recovery_diagnosis_run_md=recovery_diagnosis_run_md or "(none)",
    )
    result: AgentResult[TriagePriors] = await driver.run(
        prompt=prompt,
        output_type=TriagePriors,
        agent_name="kb-refine-triage-priors",
        usage_collector=usage_collector,
    )
    return result.unwrap("kb-refine-triage-priors")


def _draft_to_playbook(draft: DiagnosisPlaybookDraft) -> DiagnosisPlaybook:
    return DiagnosisPlaybook(
        front_matter=DiagnosisFrontMatter(
            slug=draft.slug,
            root_cause=draft.root_cause,
            when_to_consider=draft.when_to_consider,
            disambiguators=draft.disambiguators,
        ),
        summary=draft.summary,
        triage_checks=draft.triage_checks,
        fault_localization_checks=draft.fault_localization_checks,
        verification_checks=draft.verification_checks,
        required_evidence=draft.required_evidence,
        known_confounders=draft.known_confounders,
    )


async def _merge_mitigation_playbooks(
    *,
    driver: AgentDriver,
    renderer: PromptRenderer,
    candidate_playbook: MitigationPlaybookDraft,
    existing_playbook: MitigationPlaybook,
    mitigation_run_md: str,
    recovery_mitigation_run_md: str | None,
    usage_collector: UsageCollector,
) -> MitigationPlaybookDraft:
    prompt = renderer.render(
        "kb_merge_mitigation_playbooks",
        candidate_playbook_json=json.dumps(candidate_playbook.model_dump(mode="python"), indent=2),
        existing_playbook_md=existing_playbook.to_markdown(),
        mitigation_run_md=mitigation_run_md,
        recovery_mitigation_run_md=recovery_mitigation_run_md or "(none)",
    )
    result: AgentResult[MitigationPlaybookDraft] = await driver.run(
        prompt=prompt,
        output_type=MitigationPlaybookDraft,
        agent_name="kb-merge-mitigation-playbooks",
        usage_collector=usage_collector,
    )
    return result.unwrap("kb-merge-mitigation-playbooks")


def _mitigation_draft_to_playbook(draft: MitigationPlaybookDraft) -> MitigationPlaybook:
    return MitigationPlaybook(
        front_matter=MitigationFrontMatter(
            slug=draft.slug,
            root_cause=draft.root_cause,
        ),
        summary=draft.summary,
        mitigation_procedure=draft.mitigation_procedure,
        placeholder_resolution=draft.placeholder_resolution,
        verification_checks=draft.verification_checks,
        rollback_stop_conditions=draft.rollback_stop_conditions,
    )


async def process_task(task_path: Path) -> None:
    # Validate the queue file up-front so field typos (missing/renamed fields)
    # fail here rather than later when the worker tries to key into the dict.
    task = KbUpdateTask.model_validate_json(task_path.read_text())
    crucible_config = crucible_config_from_kb_task(task.model_dump(mode="python"))
    renderer = PromptRenderer(crucible_config.prompt_version)

    from sregym_agents.crucible.agents import PydanticAIDriver

    kb_driver = PydanticAIDriver(task.model_id)
    kb = StructuredKnowledgeBase(
        Path(task.kb_dir),
        app_name=task.app_name,
        config=crucible_config,
        renderer=renderer,
        driver=kb_driver,
    )
    kb.write_scope_metadata()
    store = RootCauseStore(kb.scope_dir)

    diagnosis_run_md = Path(task.diagnosis_run_file).read_text() if task.diagnosis_run_file else ""
    recovery_diagnosis_run_md = (
        Path(task.recovery_diagnosis_run_file).read_text() if task.recovery_diagnosis_run_file else None
    )
    candidate_playbook = (
        DiagnosisPlaybookDraft.model_validate(json.loads(Path(task.diagnosis_playbook_candidate_file).read_text()))
        if task.diagnosis_playbook_candidate_file
        else None
    )
    triage_area_candidate = (
        TriageAreaCandidate.model_validate(json.loads(Path(task.triage_area_candidate_file).read_text()))
        if task.triage_area_candidate_file
        else None
    )
    cards = store.list_active_diagnosis_cards()

    collector = UsageCollector()
    decision = None
    canonical_diagnosis_slug = None
    if candidate_playbook is not None:
        candidate_origin = task.diagnosis_playbook_candidate_origin or "recovery"
        decision = await _review_diagnosis_candidate(
            driver=kb_driver,
            renderer=renderer,
            diagnosis_run_md=diagnosis_run_md,
            recovery_diagnosis_run_md=recovery_diagnosis_run_md,
            candidate_playbook=candidate_playbook,
            cards=cards,
            usage_collector=collector,
            candidate_origin=candidate_origin,
            store=store,
        )
        logger.info("KB review decision for %s: %s", task.problem_id, decision.model_dump_json(indent=2))

        if decision.recommended_action == "add_playbook":
            store.save_diagnosis(_draft_to_playbook(candidate_playbook), created_from=task.problem_id)
            canonical_diagnosis_slug = candidate_playbook.slug
        elif decision.recommended_action == "merge_playbooks":
            existing_playbooks = [store.load_diagnosis(slug) for slug in decision.target_slugs]
            existing_playbooks = [playbook for playbook in existing_playbooks if playbook is not None]
            if existing_playbooks:
                draft = await _merge_playbooks(
                    driver=kb_driver,
                    renderer=renderer,
                    candidate_playbook=candidate_playbook,
                    existing_playbooks=existing_playbooks,
                    diagnosis_run_md=diagnosis_run_md,
                    recovery_diagnosis_run_md=recovery_diagnosis_run_md,
                    usage_collector=collector,
                )
                canonical_slug = decision.target_slugs[0]
                merged = _draft_to_playbook(draft.model_copy(update={"slug": canonical_slug}))
                merged_from = [candidate_playbook.slug, *decision.target_slugs[1:]]
                store.save_diagnosis(merged, created_from=task.problem_id, merged_from=merged_from)
                for merged_slug in decision.target_slugs[1:]:
                    meta = store.load_meta(merged_slug)
                    store.save_meta(meta.model_copy(update={"status": "deprecated"}))
                store.refresh_manifest()
                canonical_diagnosis_slug = canonical_slug
        else:
            logger.info(
                "No diagnosis KB mutation applied for %s (action=%s)",
                task.problem_id,
                decision.recommended_action,
            )

    if triage_area_candidate is not None:
        triage_priors_path = kb.scope_dir / "triage_priors.yaml"
        existing_priors = (
            TriagePriors.model_validate(yaml.safe_load(triage_priors_path.read_text()))
            if triage_priors_path.exists()
            else TriagePriors(areas=[])
        )
        refined_priors = await _refine_triage_priors(
            driver=kb_driver,
            renderer=renderer,
            triage_area_candidate=triage_area_candidate,
            existing_priors=existing_priors,
            diagnosis_run_md=diagnosis_run_md,
            recovery_diagnosis_run_md=recovery_diagnosis_run_md,
            usage_collector=collector,
        )
        triage_priors_path.write_text(
            yaml.safe_dump(refined_priors.model_dump(mode="python"), sort_keys=False),
        )

    mitigation_run_md = Path(task.mitigation_run_file).read_text() if task.mitigation_run_file else ""
    recovery_mitigation_run_md = (
        Path(task.recovery_mitigation_run_file).read_text() if task.recovery_mitigation_run_file else None
    )
    mitigation_candidate = (
        MitigationPlaybookDraft.model_validate(json.loads(Path(task.mitigation_playbook_candidate_file).read_text()))
        if task.mitigation_playbook_candidate_file
        else None
    )
    if mitigation_candidate is not None:
        target_slug = mitigation_candidate.slug
        if store.load_diagnosis(target_slug) is None and canonical_diagnosis_slug:
            target_slug = canonical_diagnosis_slug

        if store.load_diagnosis(target_slug) is None:
            logger.warning(
                "Skipping mitigation playbook candidate for %s: no matching diagnosis root cause %r",
                task.problem_id,
                target_slug,
            )
        else:
            normalized_candidate = mitigation_candidate.model_copy(update={"slug": target_slug})
            existing_mitigation = store.load_mitigation(target_slug)
            if existing_mitigation is None:
                store.save_mitigation(_mitigation_draft_to_playbook(normalized_candidate), created_from=task.problem_id)
            else:
                draft = await _merge_mitigation_playbooks(
                    driver=kb_driver,
                    renderer=renderer,
                    candidate_playbook=normalized_candidate,
                    existing_playbook=existing_mitigation,
                    mitigation_run_md=mitigation_run_md,
                    recovery_mitigation_run_md=recovery_mitigation_run_md,
                    usage_collector=collector,
                )
                merged = _mitigation_draft_to_playbook(
                    draft.model_copy(update={"slug": target_slug, "root_cause": normalized_candidate.root_cause})
                )
                store.save_mitigation(merged, created_from=task.problem_id)

    completed_task_path = move_to_completed(task_path, Path(task.kb_dir))
    completed_task_path.with_suffix(".usage.json").write_text(
        json.dumps(
            {
                "problem_id": task.problem_id,
                "app_name": task.app_name,
                "usage_metrics": collector.to_dict(),
                "decision": decision.model_dump(mode="python") if decision is not None else None,
            },
            indent=2,
        )
    )


async def run_worker(
    kb_dir: Path, idle_timeout: float = DEFAULT_IDLE_TIMEOUT, poll_interval: float = POLL_INTERVAL
) -> None:
    pending_dir = kb_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    pid_path = kb_dir / "kb_worker.pid"

    logger.info("KB worker started (pid=%d), watching %s", os.getpid(), pending_dir)
    try:
        pid_path.write_text(str(os.getpid()))
        last_activity = time.monotonic()
        while True:
            tasks = list_pending_tasks(kb_dir)
            if tasks:
                last_activity = time.monotonic()
            for task_path in tasks:
                succeeded = False
                for attempt in range(_TASK_MAX_RETRIES):
                    try:
                        await process_task(task_path)
                        succeeded = True
                        break
                    except Exception:
                        logger.error(
                            "KB update failed for %s (attempt %d/%d)",
                            task_path.name,
                            attempt + 1,
                            _TASK_MAX_RETRIES,
                            exc_info=True,
                        )
                        if attempt < _TASK_MAX_RETRIES - 1:
                            await asyncio.sleep(_TASK_RETRY_DELAY * (2**attempt))
                if not succeeded:
                    move_to_failed(task_path, kb_dir)
            if time.monotonic() - last_activity > idle_timeout:
                logger.info("KB worker idle for %.1fs, exiting.", idle_timeout)
                break
            await asyncio.sleep(poll_interval)
    finally:
        pid_path.unlink(missing_ok=True)
        logger.info("KB worker exiting, PID file cleaned up.")


def _setup_signal_handlers() -> None:
    def _handler(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="KB review queue worker")
    parser.add_argument("--kb-dir", required=True, type=Path)
    parser.add_argument("--idle-timeout", type=int, default=DEFAULT_IDLE_TIMEOUT)
    args = parser.parse_args()

    log_path = args.kb_dir / "kb_worker.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    root.addHandler(handler)

    _setup_signal_handlers()
    try:
        asyncio.run(run_worker(args.kb_dir, idle_timeout=args.idle_timeout))
    except KeyboardInterrupt:
        logger.info("KB worker interrupted.")


if __name__ == "__main__":
    main()
