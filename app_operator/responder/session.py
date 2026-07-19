from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app_operator.memory import OutcomeClassification, OutcomeRecord


def _classification_directive(classification: OutcomeClassification) -> str:
    if classification == OutcomeClassification.FALSE_POSITIVE:
        return "tighten the over-broad detector signature and add a near-miss regression test."
    if classification == OutcomeClassification.FALSE_NEGATIVE:
        return "add or widen the missed detector signature and include the reproducing test that previously failed."
    if classification == OutcomeClassification.SUCCESS:
        return "Capture the confirmed signature without generalizing beyond the observed successful evidence."
    return "Do not encode the result as successful operational memory."


class ReflectionTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    proposed_changes: list[str]


class StatefulResponderBackend(Protocol):
    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn: ...


class SessionReflector:
    def __init__(self, backend: StatefulResponderBackend) -> None:
        self.backend = backend

    def should_reflect(self, outcome: OutcomeRecord, *, health_verified: bool, session_id: str | None) -> bool:
        return bool(
            health_verified
            and session_id
            and outcome.classification
            in {
                OutcomeClassification.SUCCESS,
                OutcomeClassification.FALSE_POSITIVE,
                OutcomeClassification.FALSE_NEGATIVE,
            }
        )

    def resume(
        self,
        *,
        session_id: str,
        incident_id: str,
        worktree: Path,
        outcome: OutcomeRecord,
        history: list[OutcomeRecord],
        outcome_commit: str,
        validation_feedback: str | None = None,
    ) -> ReflectionTurn:
        feedback = (
            "The prior reflection proposal was rejected by the isolated validator. Correct every reported "
            f"error before returning a replacement proposal:\n{validation_feedback}\n\n"
            if validation_feedback
            else ""
        )
        prompt = (
            "The controller has independently verified incident closure and committed its authoritative outcome.\n"
            f"Outcome commit: {outcome_commit}\n"
            "Reflect using the same incident context. Edit only responder-owned `.sdo/playbooks/` and "
            "`.sdo/diagnostics/detectors/incidents/`; never edit goal.md, health detectors, or outcomes.jsonl. "
            "Generalize roles with placeholders and ground structural changes in the supplied history. Create a "
            "sharp fault-specific playbook for the confirmed cause, with deterministic diagnosis, repair, and "
            "independent verification steps. When the confirmed cause exposes a stable low-noise Kubernetes "
            "signature, add a fault-specific incident detector immediately and include both a matching test and a "
            "near-miss test. Register it with owner responder, class incident, originatingIncident set to this "
            "incident, and originatingCommit set to the authoritative outcome commit. Preserve every existing health "
            "detector and shared manifest field.\n"
            "Apply classification-aware learning: false-positive refinement must tighten an over-broad signature and "
            "add a regression near-miss; false-negative refinement must add or widen a signature with a reproducing "
            "test; repeated success may only generalize fields supported by history. Compare arch.md's topology "
            "fingerprint and resource table with the current source before reusing names, and call out stale memory "
            "in the reflection summary without editing deployer-owned architecture.\n\n"
            f"Required action for this {outcome.classification.value} outcome: "
            f"{_classification_directive(outcome.classification)}\n\n"
            f"{feedback}"
            f"Current outcome:\n{outcome.model_dump_json(indent=2)}\n\n"
            f"Outcome history:\n{json.dumps([record.model_dump(mode='json') for record in history], indent=2)}\n"
        )
        return self.backend.resume(
            session_id=session_id,
            worktree=worktree,
            prompt=prompt,
            idempotency_key=f"reflection:{incident_id}:{outcome_commit}",
        )


class CodexSessionBackend:
    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        reasoning_effort: str = "medium",
        timeout_seconds: int = 900,
    ) -> None:
        self.executable = executable
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        with tempfile.TemporaryDirectory(prefix="sdo-codex-reflection-") as temp_dir:
            root = Path(temp_dir)
            schema = root / "reflection.schema.json"
            output = root / "reflection.json"
            schema.write_text(json.dumps(ReflectionTurn.model_json_schema()), encoding="utf-8")
            command = [
                self.executable,
                "exec",
                "resume",
                "--dangerously-bypass-approvals-and-sandbox",
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
            command.extend(
                [
                    "--output-schema",
                    str(schema),
                    "--output-last-message",
                    str(output),
                    session_id,
                    "-",
                ]
            )
            completed = subprocess.run(
                command,
                cwd=worktree,
                input=f"Idempotency key: {idempotency_key}\n\n{prompt}",
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr or completed.stdout or "Codex reflection failed")
            return ReflectionTurn.model_validate_json(output.read_text(encoding="utf-8"))
