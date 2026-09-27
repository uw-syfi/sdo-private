from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from libs.agent_cli.structured import AgentProvider, StructuredTurnError, run_structured_turn, turn_usage
from sdo.operational_memory import OutcomeClassification, OutcomeRecord

if TYPE_CHECKING:
    from pathlib import Path

    from agentshim import CommandExecutor


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
    learning_decision: Literal["updated", "no_change"]
    no_change_reason: str | None = Field(default=None, min_length=1)
    proposed_changes: list[str]
    # Provider accounting for the turn; kept out of the model-facing schema.
    _usage: dict[str, int | float] = PrivateAttr(default_factory=dict)

    @property
    def usage(self) -> dict[str, int | float]:
        return dict(self._usage)

    def with_usage(self, usage: dict[str, int | float]) -> ReflectionTurn:
        self._usage = dict(usage)
        return self

    def model_post_init(self, __context: object) -> None:
        if self.learning_decision == "updated" and not self.proposed_changes:
            raise ValueError("updated reflection requires proposed_changes")
        if self.learning_decision == "no_change":
            if self.proposed_changes:
                raise ValueError("no_change reflection cannot propose changes")
            if self.no_change_reason is None:
                raise ValueError("no_change reflection requires no_change_reason")


def reflection_output_schema() -> dict[str, object]:
    """ReflectionTurn as a strict structured-output schema.

    Strict provider schemas (Codex/OpenAI) require every property to be listed
    in ``required``; optional fields are expressed as nullable instead.
    """

    schema = ReflectionTurn.model_json_schema()
    properties = cast("dict[str, dict[str, object]]", schema["properties"])
    for prop in properties.values():
        prop.pop("default", None)
    schema["required"] = list(properties)
    schema["additionalProperties"] = False
    return schema


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
            "Reflect using the same incident context. Edit only responder-owned `.sdo/playbooks/`, "
            "`.sdo/diagnostics/detectors/incidents/` (the directory name is exactly the plural `incidents`), and "
            "the corresponding responder-owned detector entries in `.sdo/diagnostics/manifest.yaml`; "
            "never edit goal.md, health detectors, or outcomes.jsonl. "
            "Generalize roles with placeholders and ground structural changes in the supplied history. Create a "
            "sharp fault-specific playbook for the confirmed cause, with deterministic diagnosis, repair, and "
            "independent verification steps. When the confirmed cause exposes a stable low-noise Kubernetes "
            "signature, add a fault-specific incident detector immediately and include both a matching test and a "
            "near-miss test. Register it with owner responder, class incident, originatingIncident set to this "
            "incident, and originatingCommit set to the authoritative outcome commit. Preserve every existing health "
            "detector and shared manifest field.\n"
            "Return learning_decision=updated when you edit memory. Use learning_decision=no_change only when no "
            "safe reusable signature or playbook improvement exists, leave proposed_changes empty, and provide a "
            "specific no_change_reason grounded in this incident. Never claim files were changed unless they exist "
            "in the worktree.\n"
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
    """Resume the responder's own Codex session to reflect on its outcome."""

    provider: ClassVar[AgentProvider] = "codex"

    def __init__(
        self,
        *,
        model: str | None = None,
        reasoning_effort: str = "medium",
        timeout_seconds: int = 900,
        executor: CommandExecutor | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.executor = executor

    def resume(
        self,
        *,
        session_id: str,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        try:
            turn = run_structured_turn(
                self.provider,
                f"Idempotency key: {idempotency_key}\n\n{prompt}",
                output_schema=reflection_output_schema(),
                cwd=worktree,
                access="danger-full-access",
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                resume_session_id=session_id,
                executor=self.executor,
            )
        except StructuredTurnError as exc:
            raise RuntimeError(f"{self.provider} reflection failed: {exc}") from exc
        return ReflectionTurn.model_validate_json(turn.output_json).with_usage(turn_usage(turn))


class ClaudeSessionBackend(CodexSessionBackend):
    """Resume the responder's own Claude Code session to reflect on its outcome."""

    provider: ClassVar[AgentProvider] = "claude"
