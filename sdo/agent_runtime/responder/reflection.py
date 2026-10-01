from __future__ import annotations

import json
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from libs.agent_cli.structured import AgentProvider, StructuredTurnError, run_structured_turn, turn_usage
from sdo.agent_runtime.responder.reflection_brief import incident_brief
from sdo.agent_runtime.responder.reflection_outcomes import current_outcome_view, history_view
from sdo.operational_memory import (
    DETECTOR_ID_PATTERN,
    DETECTOR_SDK_REFERENCE,
    FAULT_CLASS_PATTERN,
    INCIDENT_DETECTOR_MAX_FIRING,
    PLACEHOLDER_RE,
    PLAYBOOK_INDEX_PATH,
    PLAYBOOK_SCRIPT_SUFFIX,
    REFLECTION_GUIDANCE_MODES,
    REFLECTION_SESSION_MODES,
    OutcomeClassification,
    OutcomeRecord,
    TopologyReview,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agentshim import CommandExecutor

    from sdo.operational_memory import BrokerClosure, ReflectionGuidance, ReflectionSessionMode


def _classification_directive(classification: OutcomeClassification, guidance: ReflectionGuidance = "baseline") -> str:
    if classification == OutcomeClassification.FALSE_POSITIVE:
        return "tighten the over-broad detector signature and add a near-miss regression test."
    if classification == OutcomeClassification.FALSE_NEGATIVE:
        return "add or widen the missed detector signature and include the reproducing test that previously failed."
    if classification == OutcomeClassification.SUCCESS:
        if guidance in _GENERALIZING_GUIDANCE:
            return (
                "Capture the confirmed signature at the level of its root-cause class, following the generalization "
                "protocol above, and stay within what the evidence supports."
            )
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

#: Reasoning effort of the incident agents (responder and reflection). A
#: benchmark comparing SDO against a stock agent declares the same effort.
INCIDENT_REASONING_EFFORT = "medium"

_INCIDENT_DETECTOR_SKELETON = """Incident detector layout (an existing incident detector under
`.sdo/diagnostics/detectors/incidents/` is the closest concrete example; do not explore SDK source):
- files `.sdo/diagnostics/detectors/incidents/<snake_name>/detector.go` and `detector_test.go`, `package <snake_name>`,
  `func New() sdk.Detector`, table tests built on `sdktest.Snapshot` with one matching and one near-miss case.
- `Spec()` returns `sdk.DetectorSpec{ID: "<incident-detector-id>", Class: sdk.DetectorClassIncident,
  Owner: sdk.DetectorOwnerResponder, Description: "...", Watches: []sdk.WatchKind{{APIVersion: "apps/v1",
  Kind: "Deployment"}, {APIVersion: "v1", Kind: "Pod"}, {APIVersion: "v1", Kind: "Event"}},
  Interval: 30 * time.Second, Persistence: sdk.PersistencePolicy{Firing: 1, Clearing: 2},
  Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
  Playbooks: []string{".sdo/playbooks/<playbook>/README.md"}, OriginatingIncident: "<incident id>",
  OriginatingCommit: "<outcome commit>"}` for a new detector (an existing detector keeps its values); every field
  must equal its manifest entry.
- manifest entry under `detectors:` in `.sdo/diagnostics/manifest.yaml`: `id`, `package:
  ./detectors/incidents/<snake_name>`, `constructor: New`, `class: incident`, `owner: responder`, `watches`
  (`apiVersion`, `kind`), `interval`, `persistence` (`firing`, `clearing`), `batching` (`severity`, `debounce`),
  `possiblePlaybooks`, `originatingIncident`, and `originatingCommit`.
"""


#: A compiled, passing incident detector and its test (gofmt-clean), so the first incident detector needs no
#: reading of other detectors' source; the example package is renamed to the fault's `<snake_name>`.
INCIDENT_DETECTOR_EXAMPLE_FILES: dict[str, str] = {
    "detector.go": """package example_fault

import (
\t"context"
\t"time"

\t"sdo.dev/controller/sdk"
)

const detectorID = "example-fault"

type Detector struct{}

func New() sdk.Detector { return Detector{} }

func (Detector) Spec() sdk.DetectorSpec {
\treturn sdk.DetectorSpec{
\t\tID: detectorID, Class: sdk.DetectorClassIncident, Owner: sdk.DetectorOwnerResponder,
\t\tDescription:         "Detects the confirmed signature.",
\t\tWatches:             []sdk.WatchKind{{APIVersion: "apps/v1", Kind: "Deployment"}},
\t\tInterval:            30 * time.Second,
\t\tPersistence:         sdk.PersistencePolicy{Firing: 1, Clearing: 2},
\t\tBatching:            sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
\t\tPlaybooks:           []string{".sdo/playbooks/example-fault/README.md"},
\t\tOriginatingIncident: "<incident id>", OriginatingCommit: "<outcome commit>",
\t}
}

func (Detector) Detect(_ context.Context, snapshot sdk.DetectionContext) ([]sdk.Finding, error) {
\tvar findings []sdk.Finding
\tfor _, deployment := range snapshot.Deployments() {
\t\tif deployment.Namespace != snapshot.Namespace() || deployment.Name != "example" {
\t\t\tcontinue
\t\t}
\t\tfindings = append(findings, sdk.Finding{
\t\t\tRuleID: "example-rule", Status: sdk.FindingActive, Severity: sdk.SeverityCritical,
\t\t\tSummary:         "Deployment example matches the confirmed signature",
\t\t\tEvidence:        "Deployment " + deployment.Namespace + "/" + deployment.Name + " ...",
\t\t\tPrimaryResource: sdk.ObjectRefFrom("Deployment", "apps/v1", &deployment),
\t\t\tPlaybooks:       []string{".sdo/playbooks/example-fault/README.md"},
\t\t\tFingerprint:     detectorID + "/example-rule/" + deployment.Namespace + "/" + deployment.Name,
\t\t})
\t}
\treturn findings, nil
}
""",
    "detector_test.go": """package example_fault

import (
\t"context"
\t"testing"

\tappsv1 "k8s.io/api/apps/v1"
\tmetav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
\t"sdo.dev/controller/sdk/sdktest"
)

func deployment(name string) appsv1.Deployment {
\treturn appsv1.Deployment{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "demo"}}
}

func TestMatchesTheConfirmedSignature(t *testing.T) {
\tfindings, err := (Detector{}).Detect(context.Background(), sdktest.Snapshot{
\t\tNamespaceName: "demo", DeploymentList: []appsv1.Deployment{deployment("example")},
\t})
\tif err != nil || len(findings) != 1 {
\t\tt.Fatalf("want one finding, got %v, %#v", err, findings)
\t}
}

func TestNearMissDoesNotMatch(t *testing.T) {
\tfindings, err := (Detector{}).Detect(context.Background(), sdktest.Snapshot{
\t\tNamespaceName: "demo", DeploymentList: []appsv1.Deployment{deployment("other")},
\t})
\tif err != nil || len(findings) != 0 {
\t\tt.Fatalf("want no finding, got %v, %#v", err, findings)
\t}
}
""",
}


def _incident_detector_example() -> str:
    files = "".join(f"`{name}`:\n```go\n{source}```\n" for name, source in INCIDENT_DETECTOR_EXAMPLE_FILES.items())
    return (
        "Worked incident detector example (compiles and passes against controller/sdk; rename "
        "`package example_fault` to the fault's `<snake_name>`, replace the predicate and names, keep one matching "
        "case and one near-miss case; "
        "do not read health detector source, it is not a template):\n"
        f"{files}"
    )


_INCIDENT_DETECTOR_RULES = (
    f"Incident detectors must fire promptly: use `persistence.firing: {INCIDENT_DETECTOR_MAX_FIRING}` in the manifest "
    f"and `Firing: {INCIDENT_DETECTOR_MAX_FIRING}` in Spec(); the validator rejects a new or changed incident "
    "detector with a larger value. Watch the resources where the fault is visible, not only the root object: when "
    "the symptom is pod-level (a pod status, restart, or event reason), watch "
    'Pods and Events as well (`{APIVersion: "v1", Kind: "Pod"}`, `{APIVersion: "v1", Kind: "Event"}`) so '
    "the detector evaluates when the symptom appears and fires alongside the health detectors. "
)


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


MEMORY_CHECK_COMMAND = "python3 -m sdo.operational_memory.memory_check --app . --actor responder"

_SELF_CHECK_RULES = (
    f"Before returning, run `{MEMORY_CHECK_COMMAND}` whenever you edited `.sdo/`: it applies the broker "
    "validator's memory rules below to your uncommitted edits against the outcome commit, prints each violation "
    "with its fix, and exits non-zero on failure. Fix every reported error and rerun it until it prints OK. "
    "Self-check scope: validate only the incident detector you added or changed, with "
    "`python3 -m controller.builder.check_cli draft-test --app . --detector-id <incident-detector-id>`. "
    "Batch your edits, then verify once, after your last edit, in a single command that chains `chmod +x` and "
    "`bash -n` on the playbook scripts, `gofmt -l`, the draft-test, and the memory check; fix what it reports and "
    "rerun it only if it failed, and do not re-read your edits with `git diff`, `git status`, or `cat` "
    "afterwards. "
    "Do not run the health detector's tests, `go test ./...`, or the full `check_cli test`: the broker's isolated "
    "validator runs the complete suite after you return. Do not `git commit`, `git add`, or `git stash`; leave "
    "your edits uncommitted in the worktree, because the broker commits accepted memory. Do not read "
    "`.sdo/lifecycle-provenance.yaml`; it is large lifecycle evidence that reflection does not need.\n"
)


_RESPONDER_PERMISSIONS = (
    "The responder that runs a playbook works from its own pod under the responder's RBAC in the application "
    "namespace, which is namespace-scoped edit on application resources: it may get, list, watch, create, apply, "
    "patch, and delete configmaps, services, pods, persistentvolumeclaims, deployments, statefulsets, daemonsets, "
    "replicasets, networkpolicies, ingresses, and jobs (including `kubectl rollout restart`), and read pod logs, "
    "events, endpoints, and endpointslices, and it may run `kubectl exec`, `kubectl attach`, and `kubectl "
    "port-forward` against application pods. It has no access to Secrets or RBAC objects. Send "
    "representative requests from the responder pod to the Service DNS name with python3 (the image has no curl "
    "or wget), for example `python3 -c 'import urllib.request as u; r = u.urlopen(\"http://<SERVICE>.<NAMESPACE>"
    ".svc:<PORT>/\", timeout=10); print(r.status); print(r.read().decode())'`. "
)


_PLAYBOOK_RULES = (
    "Playbook rules: a fault-specific playbook is surfaced when its incident detector fires, and that detector's "
    "evidence already establishes the playbook's preconditions, so do not prescribe re-diagnosis the detector "
    "establishes; keep at most one combined sanity check. Give concrete repair commands and copy-pasteable "
    "verification commands with role placeholders (never prose such as 'check every Deployment'), including a concrete "
    "representative request command against the entrypoint Service with its expected status and body when the "
    "health objective needs one. "
    f"{_RESPONDER_PERMISSIONS}Put multi-step repair and verification "
    "commands in executable scripts under `.sdo/playbooks/<playbook>/scripts/` (`.sh`, parameters as positional "
    "arguments, `set -eu`) and reference them from the README. After restoring a missing dependency of "
    "a workload, delete the pods blocked on it or rollout-restart their workload instead of waiting for "
    "the platform's retry backoff. Include a `scripts/verify.sh` (and a "
    "`scripts/diagnose.sh` when the preconditions need their own check): when this playbook is reused before its "
    "incident detector fires, the responder runs it as its one sanity check. "
)


def _memory_rules(*, incident_id: str, outcome_commit: str) -> str:
    """The broker validator's rules for reflection-authored files, built from its own constants."""

    return (
        "Memory rules (the broker's validator rejects any violation; "
        f"`{MEMORY_CHECK_COMMAND}` checks them locally):\n"
        "- A playbook is `.sdo/playbooks/<fault-class>/README.md` starting with YAML front matter holding only "
        f"`schema_version: 1`, `owner: responder`, `fault_class` matching `{FAULT_CLASS_PATTERN}`, "
        "`originating_incident`, and optional `originating_commit`; the body is non-empty.\n"
        f"- Link every playbook from `{PLAYBOOK_INDEX_PATH}` with a relative Markdown link; every link must "
        "resolve inside `.sdo/playbooks/`.\n"
        f"- Each playbook body needs at least one role placeholder matching `{PLACEHOLDER_RE.pattern}`, such as "
        "`<NAMESPACE>`; lowercase `<namespace>` does not count.\n"
        f"- Files under `.sdo/playbooks/<fault-class>/scripts/` end in `{PLAYBOOK_SCRIPT_SUFFIX}` and pass "
        "`bash -n`.\n"
        f"- Detector `id` matches `{DETECTOR_ID_PATTERN}` and is unique; each `possiblePlaybooks` entry is an "
        "existing `.sdo/playbooks/<fault-class>/README.md`, listed once. No symlink anywhere under `.sdo/`.\n"
        "Example playbook README.md:\n"
        "```markdown\n"
        "---\n"
        "schema_version: 1\n"
        "owner: responder\n"
        "fault_class: example-fault\n"
        f"originating_incident: {json.dumps(incident_id)}\n"
        f"originating_commit: {json.dumps(outcome_commit)}\n"
        "---\n"
        "# Example fault\n\n"
        "Repair: `kubectl -n <NAMESPACE> rollout restart deployment/<DEPLOYMENT>`\n"
        "```\n"
        "Example index line: `- [Example fault](example-fault/README.md)`\n"
    )


_GENERALIZING_GUIDANCE = ("generalize", "generalize-spec")

_GENERALIZATION_PROTOCOL = (
    "Generalization protocol (follow it before creating or changing any detector or playbook):\n"
    "1. Compare the confirmed signature of this incident with every existing incident detector. Use each "
    "detector's description and match logic (the brief excerpts them when present; otherwise open its source), "
    "not only its id.\n"
    "2. Two incidents belong to the same root-cause class when the failure mechanism and the repair are the same "
    "and only the identity of the affected resource, or a parameter value, differs. For a same-class incident do "
    "not add a sibling detector or playbook. Widen the existing detector so its predicate keys on the class-level "
    "condition rather than on observed names, numbers, or message text, and have it report the affected resource "
    "through `Finding.ParameterBindings` (a role name mapped to that resource's `sdk.ObjectRef`). Add a matching "
    "test case for this new instance, keep the existing matching and near-miss cases passing, and keep the "
    "detector id, its playbook link, and its `originatingIncident` and `originatingCommit` unchanged. Parameterize "
    "the existing playbook and its scripts with role placeholders instead of observed names, and do not create "
    "another playbook for it.\n"
    "3. Create a new detector and playbook only when the root cause differs from every existing one: a different "
    "failure mechanism or a different repair. When you do, express the predicate over the class-level condition "
    "for any resource it could apply to, report the affected resource through `Finding.ParameterBindings`, and "
    "write the playbook against role placeholders, so the next same-class incident needs no new memory.\n"
    "4. Widening must stay precise. The widened predicate must still not fire for a different root cause: state the "
    "discriminating condition explicitly, and keep or add a near-miss test in which the same kind of resource is "
    "present but the class-level condition is absent or another cause applies. If you cannot widen safely, create "
    "a new detector instead.\n"
    "5. Name in your summary whether you widened an existing detector, created a new one, or made no change, and "
    "which detector.\n"
)

_SPEC_FIRST_PROTOCOL = (
    "Spec-first protocol (also follow it for every incident detector you create or widen):\n"
    "a. Choose the evidence a predicate needs by when that evidence exists. Prefer a predicate that is decidable "
    "from the current spec or status of the resources involved: that state exists the moment the fault exists, so "
    "the detector fires immediately and its playbook reaches the responder at dispatch. Evidence produced later by "
    "the platform (event objects, logs, metrics, restart counts) may only corroborate a finding or raise its "
    "severity; never make it a precondition for firing. A detector that waits for later evidence fires after the "
    "incident was dispatched, so its playbook is not used for that incident.\n"
    "b. When the signature is decidable from spec or status, the detector's matching test must include a snapshot "
    "without any event objects in which the detector fires, in addition to the near-miss test. A snapshot with "
    "events may stay as an extra case showing corroboration.\n"
    "c. When an existing incident detector reads event objects (the brief marks this per detector) and the "
    "confirmed cause is decidable from spec or status, rewrite its predicate while widening it so that it fires on "
    "spec and status alone, keep the events as corroboration, and prove it with the snapshot without events.\n"
    "d. A spec-only predicate must stay precise: state the discriminating condition, make sure healthy resources "
    "do not match, and cover that in the near-miss test.\n"
    "e. If the confirmed cause is genuinely not decidable from spec or status, say so in your summary and use the "
    "earliest available evidence, naming it.\n"
)


def _learning_request(
    *,
    outcome: OutcomeRecord,
    history: list[OutcomeRecord],
    outcome_commit: str,
    topology_review: TopologyReview | None,
    guidance: ReflectionGuidance = "baseline",
) -> str:
    """The structured reflection request shared by first attempts and retries."""

    generalize = guidance in _GENERALIZING_GUIDANCE
    spec_first = _SPEC_FIRST_PROTOCOL if guidance == "generalize-spec" else ""
    playbook_intro = (
        "Generalize roles with placeholders and ground structural changes in the supplied history. Keep playbooks "
        "at the level of the root-cause class, with deterministic repair and independent verification steps. "
        f"{_PLAYBOOK_RULES}\n{_GENERALIZATION_PROTOCOL}{spec_first}"
        if generalize
        else "Generalize roles with placeholders and ground structural changes in the supplied history. Create a "
        "sharp fault-specific playbook for the confirmed cause, with deterministic repair and independent "
        f"verification steps. {_PLAYBOOK_RULES}\n"
    )
    detector_intro = (
        "When the confirmed cause exposes a stable low-noise Kubernetes signature that no existing incident "
        "detector covers, add an incident detector for it and include both a matching test and a near-miss test. "
        if generalize
        else "When the confirmed cause exposes a stable low-noise Kubernetes "
        "signature, add a fault-specific incident detector immediately and include both a matching test and a "
        "near-miss test. "
    )
    covered = (
        "When an existing incident detector and playbook already cover this incident, change them only to fix a "
        "demonstrated gap or to widen them as the generalization protocol describes. "
        if generalize
        else "When an existing incident "
        "detector and playbook already cover this incident, change them only to fix a demonstrated gap. "
    )
    return (
        f"Outcome commit: {outcome_commit}\n"
        "Edit only responder-owned `.sdo/playbooks/`, "
        "`.sdo/diagnostics/detectors/incidents/` (the directory name is exactly the plural `incidents`), and "
        "the corresponding responder-owned detector entries in `.sdo/diagnostics/manifest.yaml`; "
        "never edit goal.md, health detectors, or outcomes.jsonl. "
        f"{playbook_intro}"
        f"{_memory_rules(incident_id=outcome.incident_id, outcome_commit=outcome_commit)}"
        f"{detector_intro}"
        f"{_INCIDENT_DETECTOR_RULES}"
        "Register a new detector with owner responder, class incident, originatingIncident set to "
        "this incident, and originatingCommit set to the authoritative outcome commit. Never change "
        "originatingIncident or originatingCommit of an existing detector, in the manifest or its Spec(): they "
        "record the incident that first taught it, and the broker rejects any rewrite. "
        f"{covered}Preserve every existing health detector and shared manifest field.\n"
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
        f"{_incident_detector_example()}\n"
        f"Required action for this {outcome.classification.value} outcome: "
        f"{_classification_directive(outcome.classification, guidance)}\n\n"
        f"{_diagnosis_directive(outcome)}"
        f"Current outcome (repeated detector evaluations collapsed into runs):\n"
        f"{json.dumps(current_outcome_view(outcome), indent=2)}\n\n"
        f"{_history_section(outcome, history)}"
    )


def _history_section(outcome: OutcomeRecord, history: list[OutcomeRecord]) -> str:
    prior = history_view(history, current_incident_id=outcome.incident_id)
    if not prior:
        return "Outcome history (prior incidents, compact): none\n"
    return f"Outcome history (prior incidents, compact; raw evidence omitted):\n{json.dumps(prior, indent=2)}\n"


def _diagnosis_directive(outcome: OutcomeRecord) -> str:
    """Summarize SDO's deterministic diagnosis check and how learning must use it."""

    if not outcome.diagnosis_verification:
        return ""
    lines = []
    for verification in outcome.diagnosis_verification:
        flipped = ", ".join(flip.detector_id for flip in verification.detectors if flip.flipped) or "none"
        verified = ", ".join(f"{check.kind} {check.source}" for check in verification.evidence if check.verified)
        contradicted = ", ".join(
            f"{check.kind} {check.source}" + (f" ({check.reason})" if check.reason else "")
            for check in verification.evidence
            if check.verified is False
        )
        line = f"- {verification.verdict.value}: {verification.summary}; detectors flipped: {flipped}"
        line += f"; verified evidence: {verified or 'none'}"
        if contradicted:
            line += f"; evidence not confirmed by the controller: {contradicted}"
        if verification.repair is not None:
            line += f"; repair attribution: {verification.repair.reason}"
        lines.append(line + "\n")
    return (
        "Diagnosis verification (deterministic, by SDO):\n"
        f"{''.join(lines)}"
        "Learn only from confirmed causes: a playbook's diagnosis and any new incident detector must describe a "
        "confirmed cause, never a contradicted, unverified, or unattributed one (an unattributed cause was not "
        "backed by your own repair: something else restored health). Every playbook you create or refine must end "
        "with a `## Verification` section that records how this incident confirmed the cause: the verified "
        "evidence above, the detectors that flipped, and the check (for example `python3 -m sdo incident "
        "status`) that proved recovery.\n\n"
    )


def _bounded_diff(diff: str | None) -> str:
    if not diff or not diff.strip():
        return "(the rejected proposal changed no files, or its diff is unavailable)\n"
    if len(diff) <= _MAX_REJECTED_DIFF_CHARS:
        return diff if diff.endswith("\n") else diff + "\n"
    omitted = len(diff) - _MAX_REJECTED_DIFF_CHARS
    return f"{diff[:_MAX_REJECTED_DIFF_CHARS]}\n[... diff truncated: {omitted} more characters omitted ...]\n"


class SessionReflector:
    """Reflect on a verified outcome.

    The first attempt resumes the responder session (``session_mode="resume"``)
    or, opt-in, starts a fresh session from a compact incident brief
    (``"fresh"``); a retry after a validation rejection is always a short fresh
    session. ``responder_turn_log`` is the responder's per-turn usage log, the
    source of its shell commands for the brief.
    """

    def __init__(
        self,
        backend: StatefulResponderBackend,
        *,
        responder_turn_log: Path | None = None,
        guidance: ReflectionGuidance = "baseline",
    ) -> None:
        if guidance not in REFLECTION_GUIDANCE_MODES:
            raise ValueError(f"unsupported reflection guidance: {guidance!r}")
        self.backend = backend
        self.responder_turn_log = responder_turn_log
        self.guidance: ReflectionGuidance = guidance

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
        session_mode: ReflectionSessionMode = "resume",
        closure: BrokerClosure | None = None,
    ) -> ReflectionTurn:
        if session_mode not in REFLECTION_SESSION_MODES:
            raise ValueError(f"unsupported reflection session mode: {session_mode!r}")
        request = _learning_request(
            outcome=outcome,
            history=history,
            outcome_commit=outcome_commit,
            topology_review=topology_review,
            guidance=self.guidance,
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
        if session_mode == "fresh":
            if closure is None:
                raise ValueError("a fresh first reflection attempt requires the verified closure for its brief")
            # The brief replaces the responder transcript, which a resumed
            # session would re-send on every model request.
            brief = incident_brief(
                closure,
                worktree=worktree,
                responder_turn_log=self.responder_turn_log,
                detector_detail=self.guidance in _GENERALIZING_GUIDANCE,
                event_dependence=self.guidance == "generalize-spec",
            )
            prompt = (
                "The controller has independently verified incident closure and committed its authoritative "
                "outcome. You are reflecting in a fresh session: the responder's session transcript is not "
                "available, so rely on the incident brief below and read the worktree's source and `.sdo/` files "
                "as needed.\n\n"
                f"{brief}\n"
                f"Reflection request:\n{request}"
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
        reasoning_effort: str = INCIDENT_REASONING_EFFORT,
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
