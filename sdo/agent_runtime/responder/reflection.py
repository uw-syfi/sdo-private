from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from libs.agent_cli.structured import AgentProvider, StructuredTurnError, run_structured_turn, turn_usage
from sdo.operational_memory import OutcomeClassification, OutcomeRecord, TopologyReview
from sdo.operational_memory.detector_sdk import DETECTOR_SDK_REFERENCE

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

    def fresh(
        self,
        *,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn: ...


#: Upper bound on the rejected diff quoted into a retry prompt.
_MAX_REJECTED_DIFF_CHARS = 48_000

_INCIDENT_DETECTOR_SKELETON = """Incident detector layout (an existing incident detector under
`.sdo/diagnostics/detectors/incidents/` is the closest concrete example; do not explore SDK source):
- files `.sdo/diagnostics/detectors/incidents/<snake_name>/detector.go` and `detector_test.go`, `package <snake_name>`,
  `func New() sdk.Detector`, table tests built on `sdktest.Snapshot` with one matching and one near-miss case.
- `Spec()` returns `sdk.DetectorSpec{ID: "<incident-detector-id>", Class: sdk.DetectorClassIncident,
  Owner: sdk.DetectorOwnerResponder, Description: "...", Watches: []sdk.WatchKind{{APIVersion: "apps/v1",
  Kind: "Deployment"}}, Interval: 30 * time.Second, Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
  Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
  Playbooks: []string{".sdo/playbooks/<playbook>/README.md"}, OriginatingIncident: "<incident id>",
  OriginatingCommit: "<outcome commit>"}`; every field must equal its manifest entry.
- manifest entry under `detectors:` in `.sdo/diagnostics/manifest.yaml`: `id`, `package:
  ./detectors/incidents/<snake_name>`, `constructor: New`, `class: incident`, `owner: responder`, `watches`
  (`apiVersion`, `kind`), `interval`, `persistence` (`firing`, `clearing`), `batching` (`severity`, `debounce`),
  `possiblePlaybooks`, `originatingIncident`, and `originatingCommit`.
"""


def _topology_facts(review: TopologyReview | None) -> str:
    if review is None:
        return (
            "Topology review: the broker has no topology comparison for this incident; do not recompute one. "
            "Never edit deployer-owned architecture.\n"
        )
    status = (
        "arch.md no longer matches the current source; say so in the reflection summary and confirm each resource "
        "name you reuse from arch.md in the source file that defines it"
        if review.stale_memory_detected
        else "arch.md matches the current source, so its resource names are current"
    )
    return (
        "Broker-computed topology review (authoritative; do not recompute fingerprints or compare arch.md with the "
        "source yourself):\n"
        f"- arch.md topology fingerprint: {review.architecture_topology_fingerprint}\n"
        f"- current source topology fingerprint: {review.source_topology_fingerprint}\n"
        f"- stale_memory_detected: {str(review.stale_memory_detected).lower()} ({status}).\n"
        "Never edit deployer-owned architecture.\n"
    )


_SELF_CHECK_RULES = (
    "Self-check scope: validate only the incident detector you added or changed, with "
    "`python3 -m controller.builder.check_cli draft-test --app . --detector-id <incident-detector-id>`. "
    "Do not run the health detector's tests, `go test ./...`, or the full `check_cli test`: the broker's isolated "
    "validator runs the complete suite after you return. Do not `git commit`, `git add`, or `git stash`; leave "
    "your edits uncommitted in the worktree, because the broker commits accepted memory. Do not read "
    "`.sdo/lifecycle-provenance.yaml`; it is large lifecycle evidence that reflection does not need.\n"
)


_PLAYBOOK_RULES = (
    "Playbook rules: a fault-specific playbook is surfaced when its incident detector fires, and that detector's "
    "evidence already establishes the playbook's preconditions, so do not prescribe re-diagnosis the detector "
    "establishes; keep at most one combined sanity check. Give concrete repair commands and copy-pasteable "
    "verification commands with role placeholders (never prose such as 'check every Deployment'), including a concrete "
    "representative request command (for example a `kubectl exec` or `curl` against the entrypoint with its "
    "expected status and body) when the health objective needs one. Put multi-step repair and verification "
    "commands in executable scripts under `.sdo/playbooks/<playbook>/scripts/` (`.sh`, parameters as positional "
    "arguments, `set -eu`) and reference them from the README. After restoring a missing mount source (a "
    "ConfigMap or Secret), delete the pods stuck on it or rollout-restart their workload instead of waiting for "
    "the kubelet mount backoff. "
)


def _learning_request(
    *,
    outcome: OutcomeRecord,
    history: list[OutcomeRecord],
    outcome_commit: str,
    topology_review: TopologyReview | None,
) -> str:
    """The structured reflection request shared by first attempts and retries."""

    return (
        f"Outcome commit: {outcome_commit}\n"
        "Edit only responder-owned `.sdo/playbooks/`, "
        "`.sdo/diagnostics/detectors/incidents/` (the directory name is exactly the plural `incidents`), and "
        "the corresponding responder-owned detector entries in `.sdo/diagnostics/manifest.yaml`; "
        "never edit goal.md, health detectors, or outcomes.jsonl. "
        "Generalize roles with placeholders and ground structural changes in the supplied history. Create a "
        "sharp fault-specific playbook for the confirmed cause, with deterministic repair and independent "
        "verification steps. "
        f"{_PLAYBOOK_RULES}"
        "When the confirmed cause exposes a stable low-noise Kubernetes "
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
        "test; repeated success may only generalize fields supported by history.\n\n"
        f"{_topology_facts(topology_review)}\n"
        f"{_SELF_CHECK_RULES}\n"
        f"{DETECTOR_SDK_REFERENCE}\n"
        f"{_INCIDENT_DETECTOR_SKELETON}\n"
        f"Required action for this {outcome.classification.value} outcome: "
        f"{_classification_directive(outcome.classification)}\n\n"
        f"Current outcome:\n{outcome.model_dump_json(indent=2)}\n\n"
        f"Outcome history:\n{json.dumps([record.model_dump(mode='json') for record in history], indent=2)}\n"
    )


def _bounded_diff(diff: str | None) -> str:
    if not diff or not diff.strip():
        return "(the rejected proposal changed no files, or its diff is unavailable)\n"
    if len(diff) <= _MAX_REJECTED_DIFF_CHARS:
        return diff if diff.endswith("\n") else diff + "\n"
    omitted = len(diff) - _MAX_REJECTED_DIFF_CHARS
    return f"{diff[:_MAX_REJECTED_DIFF_CHARS]}\n[... diff truncated: {omitted} more characters omitted ...]\n"


class SessionReflector:
    """Reflect on a verified outcome: same session first, a short fresh session on retry."""

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
        topology_review: TopologyReview | None = None,
        rejected_proposal_diff: str | None = None,
    ) -> ReflectionTurn:
        request = _learning_request(
            outcome=outcome,
            history=history,
            outcome_commit=outcome_commit,
            topology_review=topology_review,
        )
        idempotency_key = f"reflection:{incident_id}:{outcome_commit}"
        if validation_feedback:
            # Re-sending the responder transcript on every model request is the
            # dominant retry cost, and the rejected diff plus the validator's
            # error is all the retry needs from the first attempt.
            prompt = (
                "You are correcting an operational-memory proposal that SDO's isolated validator rejected. The "
                "controller has independently verified incident closure and committed its authoritative outcome; "
                "the responder's session transcript is intentionally not available.\n\n"
                f"Validator error:\n{validation_feedback}\n\n"
                "The rejected proposal was rolled back, so the worktree is clean at the accepted outcome commit. "
                "Rejected proposal diff against that commit (reapply what was valid, fix every reported error):\n"
                f"```diff\n{_bounded_diff(rejected_proposal_diff)}```\n\n"
                f"Original reflection request:\n{request}"
            )
            return self.backend.fresh(worktree=worktree, prompt=prompt, idempotency_key=idempotency_key)
        prompt = (
            "The controller has independently verified incident closure and committed its authoritative outcome.\n"
            "Reflect using the same incident context.\n"
            f"{request}"
        )
        return self.backend.resume(
            session_id=session_id,
            worktree=worktree,
            prompt=prompt,
            idempotency_key=idempotency_key,
        )


class CodexSessionBackend:
    """Resume the responder's own Codex session to reflect on its outcome.

    Validation retries use :meth:`fresh` instead, so they never re-send the
    responder transcript.
    """

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
        return self._turn(worktree=worktree, prompt=prompt, idempotency_key=idempotency_key, session_id=session_id)

    def fresh(
        self,
        *,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
    ) -> ReflectionTurn:
        """Run a new session in the incident worktree; used for validation retries."""

        return self._turn(worktree=worktree, prompt=prompt, idempotency_key=idempotency_key, session_id=None)

    def _turn(
        self,
        *,
        worktree: Path,
        prompt: str,
        idempotency_key: str,
        session_id: str | None,
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
