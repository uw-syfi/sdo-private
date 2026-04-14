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
from typing import TYPE_CHECKING

from libs.pydantic_agent import UsageCollector
from sregym_agents.crucible._prompts import PromptRenderer
from sregym_agents.crucible.config import crucible_config_from_kb_task
from sregym_agents.crucible.kb_update_queue import list_pending_tasks, move_to_completed, move_to_failed
from sregym_agents.crucible.knowledge_base.incident_review import (
    DiagnosisPlaybookDraft,
    ReviewDecision,
)
from sregym_agents.crucible.knowledge_base.root_cause import DiagnosisFrontMatter, DiagnosisPlaybook, RootCauseStore
from sregym_agents.crucible.knowledge_base.structured import StructuredKnowledgeBase

if TYPE_CHECKING:
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


async def _review_failure(
    *,
    driver: AgentDriver,
    renderer: PromptRenderer,
    original_run_md: str,
    grounded_run_md: str,
    cards: list[DiagnosisFrontMatter],
    usage_collector: UsageCollector,
) -> ReviewDecision:
    prompt = renderer.render(
        "kb_review_failure",
        diagnosis_cards=_format_cards(cards),
        original_run_md=original_run_md,
        grounded_run_md=grounded_run_md,
    )
    result: AgentResult[ReviewDecision] = await driver.run(
        prompt=prompt,
        output_type=ReviewDecision,
        agent_name="kb-review-classifier",
        usage_collector=usage_collector,
    )
    return result.unwrap("kb-review-classifier")


async def _draft_new_playbook(
    *,
    driver: AgentDriver,
    renderer: PromptRenderer,
    original_run_md: str,
    grounded_run_md: str,
    usage_collector: UsageCollector,
) -> DiagnosisPlaybookDraft:
    prompt = renderer.render(
        "kb_draft_diagnosis_playbook",
        original_run_md=original_run_md,
        grounded_run_md=grounded_run_md,
    )
    result: AgentResult[DiagnosisPlaybookDraft] = await driver.run(
        prompt=prompt,
        output_type=DiagnosisPlaybookDraft,
        agent_name="kb-draft-playbook",
        usage_collector=usage_collector,
    )
    return result.unwrap("kb-draft-playbook")


async def _refine_playbook(
    *,
    driver: AgentDriver,
    renderer: PromptRenderer,
    existing_playbook: DiagnosisPlaybook,
    original_run_md: str,
    grounded_run_md: str,
    usage_collector: UsageCollector,
) -> DiagnosisPlaybookDraft:
    prompt = renderer.render(
        "kb_refine_diagnosis_playbook",
        existing_playbook_md=existing_playbook.to_markdown(),
        original_run_md=original_run_md,
        grounded_run_md=grounded_run_md,
    )
    result: AgentResult[DiagnosisPlaybookDraft] = await driver.run(
        prompt=prompt,
        output_type=DiagnosisPlaybookDraft,
        agent_name="kb-refine-playbook",
        usage_collector=usage_collector,
    )
    draft = result.unwrap("kb-refine-playbook")
    return draft.model_copy(update={"slug": existing_playbook.slug})


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
        verification_checks=draft.verification_checks,
        required_evidence=draft.required_evidence,
        known_confounders=draft.known_confounders,
    )


async def process_task(task_path: Path) -> None:
    task = json.loads(task_path.read_text())
    crucible_config = crucible_config_from_kb_task(task)
    renderer = PromptRenderer(crucible_config.prompt_version)

    from sregym_agents.crucible.agents import PydanticAIDriver

    kb_driver = PydanticAIDriver(task["model_id"])
    kb = StructuredKnowledgeBase(
        Path(task["kb_dir"]),
        app_name=task["app_name"],
        config=crucible_config,
        renderer=renderer,
        driver=kb_driver,
    )
    kb.write_scope_metadata()
    store = RootCauseStore(kb.scope_dir)

    original_run_md = Path(task["original_run_file"]).read_text()
    grounded_run_md = Path(task["grounded_run_file"]).read_text()
    cards = store.list_active_diagnosis_cards()

    collector = UsageCollector()
    decision = await _review_failure(
        driver=kb_driver,
        renderer=renderer,
        original_run_md=original_run_md,
        grounded_run_md=grounded_run_md,
        cards=cards,
        usage_collector=collector,
    )
    logger.info("KB review decision for %s: %s", task["problem_id"], decision.model_dump_json(indent=2))

    if decision.recommended_action == "create_playbook":
        draft = await _draft_new_playbook(
            driver=kb_driver,
            renderer=renderer,
            original_run_md=original_run_md,
            grounded_run_md=grounded_run_md,
            usage_collector=collector,
        )
        store.save_diagnosis(_draft_to_playbook(draft), created_from=task["problem_id"])
    elif decision.recommended_action == "refine_playbook" and decision.target_slug:
        existing = store.load_diagnosis(decision.target_slug)
        if existing is not None:
            draft = await _refine_playbook(
                driver=kb_driver,
                renderer=renderer,
                existing_playbook=existing,
                original_run_md=original_run_md,
                grounded_run_md=grounded_run_md,
                usage_collector=collector,
            )
            store.save_diagnosis(_draft_to_playbook(draft), created_from=task["problem_id"])
    else:
        logger.info("No KB mutation applied for %s (action=%s)", task["problem_id"], decision.recommended_action)

    completed_task_path = move_to_completed(task_path, Path(task["kb_dir"]))
    completed_task_path.with_suffix(".usage.json").write_text(
        json.dumps(
            {
                "problem_id": task["problem_id"],
                "app_name": task["app_name"],
                "usage_metrics": collector.to_dict(),
                "decision": decision.model_dump(mode="python"),
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
