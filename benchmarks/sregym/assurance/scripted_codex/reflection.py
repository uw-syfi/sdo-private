"""The scripted reflection: encode (or decline to encode) what the incident taught.

It edits the incident worktree exactly as a reflecting agent does, runs SDO's
memory self-check, and leaves the edits uncommitted for the broker.

A reflection learns what the responder *believed*: after a claimed-but-wrong
repair it encodes the wrong cause. Whether that reaches memory is decided by
SDO (closure gate, outcome classification, broker validation), which is what
the assurance scenarios test.

Stdlib-only: this runs inside the controller image.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .faults import PLANS, Facts, MemoryProposal

if TYPE_CHECKING:
    from pathlib import Path

    from .directive import Binding
    from .store import Store
    from .turn import Turn

MEMORY_CHECK = "python3 -m sdo.operational_memory.memory_check --app . --actor responder"
_IDEMPOTENCY = re.compile(r"Idempotency key: reflection:(?P<incident>.+):(?P<commit>[0-9a-f]{7,40})\s*$", re.MULTILINE)
RETRY_MARKER = "You are correcting an operational-memory proposal"


class ReflectionCrash(RuntimeError):
    """The directive asks this reflection turn to crash like a dying CLI."""


def idempotency(prompt: str) -> tuple[str, str]:
    match = _IDEMPOTENCY.search(prompt)
    if match is None:
        raise ValueError("reflection prompt has no idempotency key")
    return match.group("incident"), match.group("commit")


def _no_change(reason: str) -> dict[str, Any]:
    return {
        "summary": reason,
        "learning_decision": "no_change",
        "no_change_reason": reason,
        "proposed_changes": [],
    }


def _write(worktree: Path, proposal: MemoryProposal, *, firing: int) -> list[str]:
    for relative, text in proposal.files.items():
        path = worktree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.replace("Firing: 1,", f"Firing: {firing},"), encoding="utf-8")
    for relative in proposal.executable:
        (worktree / relative).chmod(0o755)
    manifest = worktree / ".sdo" / "diagnostics" / "manifest.yaml"
    text = manifest.read_text(encoding="utf-8")
    entry = proposal.manifest_entry.replace("      firing: 1\n", f"      firing: {firing}\n")
    manifest.write_text(text.rstrip("\n") + "\n" + entry, encoding="utf-8")
    index = worktree / ".sdo" / "playbooks" / "README.md"
    lines = index.read_text(encoding="utf-8").rstrip("\n")
    index.write_text(lines + "\n" + proposal.index_line + "\n", encoding="utf-8")
    return sorted([*proposal.files, ".sdo/diagnostics/manifest.yaml", ".sdo/playbooks/README.md"])


def reflect(turn: Turn, binding: Binding, prompt: str, worktree: Path, store: Store) -> dict[str, Any]:
    directive = binding.directive
    incident_id, outcome_commit = idempotency(prompt)
    retry = RETRY_MARKER in prompt
    mode = directive.reflection
    if mode == "crash_always" or (mode == "crash_once" and store.claim(f"{incident_id}:reflection-crash")):
        turn.run("git status --short")
        raise ReflectionCrash(f"scripted reflection crash for {incident_id} ({mode})")
    if mode == "no_change":
        turn.run("git status --short")
        return _no_change("scripted directive: this incident teaches nothing new")
    plan = PLANS[directive.fault]
    facts = Facts(namespace="", target=directive.target)
    claimed = directive.mitigation == "wrong_only_claimed"
    if claimed:
        proposal = plan.claimed_memory(facts, incident_id=incident_id, outcome_commit=outcome_commit)
    else:
        proposal = plan.memory(facts, incident_id=incident_id, outcome_commit=outcome_commit)
    turn.run("cat .sdo/diagnostics/manifest.yaml")
    manifest = (worktree / ".sdo" / "diagnostics" / "manifest.yaml").read_text(encoding="utf-8")
    if f"- id: {proposal.detector_id}\n" in manifest and (worktree / proposal.playbook_path).is_file():
        return _no_change(
            f"memory already encodes this incident: detector {proposal.detector_id} and playbook "
            f"{proposal.playbook_path} exist"
        )
    invalid = mode == "invalid_then_learn" and not retry and store.claim(f"{incident_id}:reflection-invalid")
    changed = _write(worktree, proposal, firing=5 if invalid else 1)
    turn.run(MEMORY_CHECK, timeout=600)
    return {
        "summary": f"Encoded {proposal.detector_id} with playbook {proposal.playbook_path}"
        + (" (claimed cause)" if claimed else ""),
        "learning_decision": "updated",
        "no_change_reason": None,
        "proposed_changes": changed,
    }
