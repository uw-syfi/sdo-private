from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agentshim.providers.codex import CodexSandboxConfig, parse_sandbox

from sdo.agent_runtime.responder.reflection import (
    ClaudeSessionBackend,
    CodexSessionBackend,
    ReflectionTurn,
    SessionReflector,
)
from sdo.contracts import IncidentRequest, IncidentResult
from sdo.operational_memory import (
    BrokerClosure,
    DetectorFlip,
    DiagnosisVerdict,
    EvidenceCheck,
    OutcomeClassification,
    OutcomeRecord,
    RootCauseVerification,
    TopologyReview,
)
from sdo.operational_memory.models import (
    DETECTOR_ID_PATTERN,
    FAULT_CLASS_PATTERN,
    ArtifactOwner,
    OutcomeTimestamps,
    PlaybookMetadata,
)
from sdo.operational_memory.validation import (
    PLACEHOLDER_RE,
    PLAYBOOK_INDEX_PATH,
    PLAYBOOK_SCRIPT_SUFFIX,
    RESPONDER_FORBIDDEN_KUBECTL_VERBS,
    MemoryValidator,
)
from tests.structured_turns import ScriptedAgent, reply, turn_schema
from tests.unit.sdo.operational_memory.test_memory import _write_memory

if TYPE_CHECKING:
    from agentshim import CommandRequest
    from agentshim.testing import FakeRun


def test_codex_reflection_resumes_structured_session_in_incident_worktree(tmp_path: Path) -> None:
    def respond(request: CommandRequest) -> FakeRun:
        assert "proposed_changes" in turn_schema(request)["properties"]
        return reply(
            "codex",
            {
                "summary": "captured signature",
                "learning_decision": "updated",
                "proposed_changes": [".sdo/playbooks/example.md"],
            },
            session_id="session-1",
        )

    agent = ScriptedAgent(respond)
    backend = CodexSessionBackend(
        model="gpt-test",
        reasoning_effort="high",
        timeout_seconds=456,
        executor=agent.executor,
    )
    result = backend.resume(
        session_id="session-1",
        worktree=tmp_path,
        prompt="reflect on verified recovery",
        idempotency_key="reflection:incident:commit",
    )

    assert result.summary == "captured signature"
    (request,) = agent.requests
    argv = list(request.argv)
    assert argv[1:5] == ["exec", "resume", "session-1", "-"]
    assert parse_sandbox(argv) == CodexSandboxConfig(mode="danger-full-access")
    assert argv[argv.index("--model") + 1] == "gpt-test"
    assert 'model_reasoning_effort="high"' in argv
    assert request.cwd == str(tmp_path.resolve())
    assert request.timeout == 456
    assert (request.stdin or "").startswith("Idempotency key: reflection:incident:commit")


def test_claude_reflection_resumes_structured_session(tmp_path: Path) -> None:
    agent = ScriptedAgent(
        lambda _request: reply(
            "claude",
            {
                "summary": "existing memory covers the incident",
                "learning_decision": "no_change",
                "no_change_reason": "the existing playbook already captures this verified signature",
                "proposed_changes": [],
            },
            session_id="session-1",
        )
    )

    result = ClaudeSessionBackend(model="haiku", executor=agent.executor).resume(
        session_id="session-1",
        worktree=tmp_path,
        prompt="reflect",
        idempotency_key="reflection:incident:commit",
    )

    (command,) = agent.argvs
    assert command[command.index("--resume") + 1] == "session-1"
    assert command[command.index("--model") + 1] == "haiku"
    assert result.learning_decision == "no_change"


class _CapturingBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    @staticmethod
    def _turn() -> ReflectionTurn:
        return ReflectionTurn(
            summary="existing memory covers the incident",
            learning_decision="no_change",
            no_change_reason="the existing detector already encodes this verified signature",
            proposed_changes=[],
        )

    def resume(self, *, session_id: str, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        del worktree
        self.calls.append({"mode": "resume", "session_id": session_id, "prompt": prompt, "key": idempotency_key})
        return self._turn()

    def fresh(self, *, worktree: Path, prompt: str, idempotency_key: str) -> ReflectionTurn:
        del worktree
        self.calls.append({"mode": "fresh", "prompt": prompt, "key": idempotency_key})
        return self._turn()


def _outcome() -> OutcomeRecord:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    return OutcomeRecord(
        incident_id="inc-1",
        source_commit="source",
        deployed_commit="deployed",
        classification=OutcomeClassification.SUCCESS,
        responder_backend="codex",
        responder_model="gpt-5",
        timestamps=OutcomeTimestamps(detected_at=now, dispatched_at=now, completed_at=now),
    )


_REVIEW = TopologyReview(
    architecture_topology_fingerprint="a" * 64,
    source_topology_fingerprint="b" * 64,
    stale_memory_detected=True,
)


def test_first_reflection_resumes_same_session_with_broker_facts_and_bounded_validation(tmp_path: Path) -> None:
    backend = _CapturingBackend()
    outcome = _outcome()

    SessionReflector(backend).resume(
        session_id="session-1",
        incident_id="inc-1",
        worktree=tmp_path,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        topology_review=_REVIEW,
    )

    (call,) = backend.calls
    assert call["mode"] == "resume"
    assert call["session_id"] == "session-1"
    prompt = str(call["prompt"])
    # The broker's own topology comparison is supplied, not recomputed by the model.
    assert "a" * 64 in prompt
    assert "b" * 64 in prompt
    assert "stale_memory_detected: true" in prompt
    assert "do not recompute" in prompt
    assert "with the current source before reusing names" not in prompt
    # Validation is scoped to the new incident detector only.
    assert "python3 -m controller.builder.check_cli draft-test --app . --detector-id <incident-detector-id>" in prompt
    assert "Do not run the health detector's tests" in prompt
    assert "Do not `git commit`" in prompt
    assert ".sdo/lifecycle-provenance.yaml" in prompt
    # A compact SDK reference and incident skeleton replace exploration.
    assert "sdo.dev/controller/sdk" in prompt
    assert "sdk.DetectorClassIncident" in prompt
    assert "sdk.DetectorOwnerResponder" in prompt
    # Existing output and validation contracts are unchanged.
    assert "learning_decision=updated" in prompt
    assert "originatingIncident" in prompt
    assert "outcome-sha" in prompt
    assert "sharp fault-specific playbook" in prompt
    assert outcome.model_dump_json(indent=2) in prompt


def test_reflection_retry_after_validation_failure_uses_a_short_fresh_session(tmp_path: Path) -> None:
    backend = _CapturingBackend()
    outcome = _outcome()
    diff = "diff --git a/.sdo/playbooks/x.md b/.sdo/playbooks/x.md\n+rejected line\n"

    SessionReflector(backend).resume(
        session_id="session-1",
        incident_id="inc-1",
        worktree=tmp_path,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        validation_feedback="detector test failed: undefined: sdk.Foo",
        topology_review=_REVIEW,
        rejected_proposal_diff=diff,
    )

    (call,) = backend.calls
    assert call["mode"] == "fresh"
    prompt = str(call["prompt"])
    assert "detector test failed: undefined: sdk.Foo" in prompt
    assert diff in prompt
    assert "rolled back" in prompt
    assert "Reflect using the same incident context" not in prompt
    # The original structured request is repeated in full.
    assert outcome.model_dump_json(indent=2) in prompt
    assert "Required action for this success outcome" in prompt
    assert "a" * 64 in prompt
    assert "learning_decision=updated" in prompt


def test_reflection_retry_bounds_an_oversized_rejected_diff(tmp_path: Path) -> None:
    backend = _CapturingBackend()
    outcome = _outcome()

    SessionReflector(backend).resume(
        session_id="session-1",
        incident_id="inc-1",
        worktree=tmp_path,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        validation_feedback="invalid",
        rejected_proposal_diff="+" + "x" * 500_000,
    )

    prompt = str(backend.calls[0]["prompt"])
    assert len(prompt) < 100_000
    assert "truncated" in prompt


def test_codex_fresh_reflection_starts_a_new_session_with_full_access(tmp_path: Path) -> None:
    agent = ScriptedAgent(
        lambda _request: reply(
            "codex",
            {
                "summary": "s",
                "learning_decision": "no_change",
                "no_change_reason": "r",
                "proposed_changes": [],
            },
            session_id="fresh-1",
        )
    )

    result = CodexSessionBackend(model="gpt-test", executor=agent.executor).fresh(
        worktree=tmp_path,
        prompt="retry",
        idempotency_key="reflection:incident:commit",
    )

    (request,) = agent.requests
    argv = list(request.argv)
    assert "resume" not in argv
    assert parse_sandbox(argv) == CodexSandboxConfig(mode="danger-full-access")
    assert request.cwd == str(tmp_path.resolve())
    assert result.learning_decision == "no_change"


def _first_reflection_prompt(tmp_path: Path) -> str:
    backend = _CapturingBackend()
    outcome = _outcome()
    SessionReflector(backend).resume(
        session_id="session-1",
        incident_id="inc-1",
        worktree=tmp_path,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        topology_review=_REVIEW,
    )
    return str(backend.calls[0]["prompt"])


def _reflection_prompt_for(tmp_path: Path, outcome: OutcomeRecord) -> str:
    backend = _CapturingBackend()
    SessionReflector(backend).resume(
        session_id="session-1",
        incident_id="inc-1",
        worktree=tmp_path,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        topology_review=_REVIEW,
    )
    return str(backend.calls[0]["prompt"])


def test_reflection_records_the_diagnosis_verification_and_learns_only_confirmed_causes(tmp_path: Path) -> None:
    verified = _outcome().model_copy(
        update={
            "diagnosis_verification": [
                RootCauseVerification(
                    summary="frontend selector matches no pods",
                    verdict=DiagnosisVerdict.CONFIRMED,
                    evidence=[EvidenceCheck(kind="state-change", source="Service/frontend", verified=True)],
                    detectors=[
                        DetectorFlip(
                            detector_id="traffic-health", fired_at_dispatch=True, cleared_after_fix=True, flipped=True
                        )
                    ],
                ),
                RootCauseVerification(
                    summary="mongo admin roles revoked",
                    verdict=DiagnosisVerdict.CONTRADICTED,
                    evidence=[EvidenceCheck(kind="state-change", source="ConfigMap/failure-admin-geo", verified=False)],
                ),
            ]
        }
    )

    prompt = _reflection_prompt_for(tmp_path, verified)

    assert "Diagnosis verification (deterministic, by SDO):" in prompt
    assert (
        "- confirmed: frontend selector matches no pods; detectors flipped: traffic-health; verified evidence: "
        in prompt
    )
    assert "state-change Service/frontend" in prompt
    assert "- contradicted: mongo admin roles revoked" in prompt
    assert "Learn only from confirmed causes" in prompt
    assert "## Verification" in prompt


def test_reflection_requires_executable_playbooks_that_trust_the_incident_detector(tmp_path: Path) -> None:
    prompt = _first_reflection_prompt(tmp_path)

    # Verification must be concrete and copy-pasteable, preferably as a script.
    assert "copy-pasteable verification commands" in prompt
    assert "representative request" in prompt
    assert ".sdo/playbooks/<playbook>/scripts/" in prompt
    # Restored mount sources must not wait on the kubelet backoff.
    assert "ConfigMap or Secret" in prompt
    assert "rollout-restart" in prompt
    assert "kubelet" in prompt
    # The incident detector already establishes the playbook's preconditions.
    assert "do not prescribe re-diagnosis" in prompt


def test_reflection_asks_for_prompt_incident_detectors_on_the_visible_symptom(tmp_path: Path) -> None:
    prompt = _first_reflection_prompt(tmp_path)

    # Incident detectors fire on their first matching evaluation, and the skeleton example agrees.
    assert "`persistence.firing: 1`" in prompt
    assert "Firing: 1" in prompt
    assert "Firing: 2" not in prompt
    # Watch where the fault is visible, not only the root object.
    assert "Pods and Events" in prompt
    assert "FailedMount" in prompt
    assert "CrashLoopBackOff" in prompt
    assert '{APIVersion: "v1", Kind: "Event"}' in prompt
    # The warm path runs a verify or diagnose script when the detector has not fired yet.
    assert "scripts/verify.sh" in prompt


def test_reflection_never_asks_to_rewrite_existing_detector_provenance(tmp_path: Path) -> None:
    prompt = _first_reflection_prompt(tmp_path)

    assert "Register a new detector" in prompt
    assert "Never change originatingIncident or originatingCommit of an existing detector" in prompt
    assert "an existing detector keeps its values" in prompt


def test_reflection_states_every_playbook_rule_the_memory_validator_enforces(tmp_path: Path) -> None:
    prompt = _first_reflection_prompt(tmp_path)

    # Rule text comes from the validator's own constants, so prompt and validator cannot drift.
    assert PLAYBOOK_INDEX_PATH.as_posix() in prompt
    assert PLACEHOLDER_RE.pattern in prompt
    assert PLAYBOOK_SCRIPT_SUFFIX in prompt
    assert "bash -n" in prompt
    assert FAULT_CLASS_PATTERN in prompt
    assert DETECTOR_ID_PATTERN in prompt
    for field in PlaybookMetadata.model_fields:
        assert f"`{field}" in prompt
    assert "owner: responder" in prompt
    assert "symlink" in prompt
    # Lowercase placeholders were the live rejection; the prompt must call them out.
    assert "`<namespace>`" in prompt


def test_reflection_example_playbook_passes_the_memory_validator(tmp_path: Path) -> None:
    prompt = _first_reflection_prompt(tmp_path)
    example = prompt.split("Example playbook README.md:\n```markdown\n", 1)[1].split("```", 1)[0]
    index_line = prompt.split("Example index line: `", 1)[1].split("`\n", 1)[0]

    app = tmp_path / "app"
    memory = app / ".sdo"
    (memory / "diagnostics" / "detectors" / "health" / "entrypoint").mkdir(parents=True)
    (memory / "schema-version").write_text("1\n", encoding="utf-8")
    (memory / "goal.md").write_text("---\nowner: human\napplication: demo\n---\n# Goal\n\nServe.\n", encoding="utf-8")
    (memory / "arch.md").write_text(
        "---\ngenerated_at_commit: c\ngenerated_at: 2026-01-01T00:00:00Z\napplication: demo\n"
        "topology_fingerprint: t\n---\n# Architecture\n\nOne service.\n",
        encoding="utf-8",
    )
    (memory / "outcomes.jsonl").write_text("", encoding="utf-8")
    (memory / "diagnostics" / "detectors" / "health" / "entrypoint" / "detector.go").write_text(
        "package entrypoint\n", encoding="utf-8"
    )
    (memory / "diagnostics" / "manifest.yaml").write_text(
        "apiVersion: sdo.dev/v1alpha1\nkind: DetectorManifest\nsdkVersion: v0.1\ndetectors:\n"
        "  - id: entrypoint-health\n    package: ./detectors/health/entrypoint\n    class: health\n"
        "    owner: health_judge\n    watches: []\n    interval: 30s\n    persistence: {firing: 2, clearing: 2}\n"
        "    batching: {severity: critical, debounce: 500ms}\n    possiblePlaybooks: []\n"
        "    originatingCommit: c\n",
        encoding="utf-8",
    )
    playbook = memory / "playbooks" / "example-fault" / "README.md"
    playbook.parent.mkdir(parents=True)
    playbook.write_text(example, encoding="utf-8")
    (memory / "playbooks" / "README.md").write_text(f"# Playbooks\n\n{index_line}\n", encoding="utf-8")

    MemoryValidator(run_diagnostics=False).validate(
        app,
        actor=ArtifactOwner.RESPONDER,
        changed_paths=[".sdo/playbooks/README.md", ".sdo/playbooks/example-fault/README.md"],
    )


_FAILED_MOUNT_EVENT = (
    'Warning  FailedMount  pod/geo-7c9d-x2k  MountVolume.SetUp failed for volume "geo-config" : '
    'configmap "geo-config" not found'
)


def _fresh_closure(worktree: Path, *, evidence: str = _FAILED_MOUNT_EVENT) -> BrokerClosure:
    root = Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "sdo" / "contracts"
    request = IncidentRequest.model_validate_json((root / "incident_request.json").read_text(encoding="utf-8"))
    finding = request.findings[0].model_copy(
        update={
            "evidence": evidence,
            "metadata": {"event_reason": "FailedMount", "volume": {"configMap": "geo-config"}},
        }
    )
    request = request.model_copy(update={"findings": [finding], "repository_worktree": str(worktree)})
    result = IncidentResult.model_validate_json((root / "incident_result.json").read_text(encoding="utf-8"))
    at = datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)
    return BrokerClosure(
        request=request,
        result=result,
        final_detector_states=result.final_detector_states,
        detected_at=at,
        dispatched_at=at,
        responder_completed_at=at,
        verified_at=at,
    )


def _memory_worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "worktree"
    worktree.mkdir(parents=True)
    _write_memory(worktree)
    return worktree


def _turn_log(tmp_path: Path, session_id: str, commands: list[str]) -> Path:
    log = tmp_path / "responder-turns.jsonl"
    records = [
        {"session_id": "other-session", "shell_command_lines": ["kubectl delete ns unrelated"]},
        {"session_id": session_id, "shell_command_lines": commands},
    ]
    log.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return log


def _fresh_first_attempt(
    tmp_path: Path, *, closure: BrokerClosure | None = None, log: Path | None = None
) -> dict[str, object]:
    worktree = _memory_worktree(tmp_path)
    backend = _CapturingBackend()
    outcome = _outcome()
    SessionReflector(backend, responder_turn_log=log).resume(
        session_id="019c-session-0001",
        incident_id="inc-1",
        worktree=worktree,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        topology_review=_REVIEW,
        session_mode="fresh",
        closure=closure or _fresh_closure(worktree),
    )
    (call,) = backend.calls
    return call


def test_fresh_first_reflection_starts_a_new_session_with_a_compact_incident_brief(tmp_path: Path) -> None:
    log = _turn_log(
        tmp_path,
        "019c-session-0001",
        ["kubectl -n hotel-reservation describe pod geo-7c9d-x2k", "kubectl -n hotel-reservation apply -f cm.yaml"],
    )

    call = _fresh_first_attempt(tmp_path, log=log)

    assert call["mode"] == "fresh"
    prompt = str(call["prompt"])
    assert "Reflect using the same incident context" not in prompt
    assert "responder's session transcript is not available" in prompt
    # Live evidence is verbatim, including the object shapes.
    assert _FAILED_MOUNT_EVENT in prompt
    assert '"configMap": "geo-config"' in prompt
    assert "Deployment hotel-reservation/geo" in prompt
    assert "ConfigMap hotel-reservation/geo-config" in prompt
    # The responder's structured result.
    assert "The geo Deployment referenced an absent required ConfigMap" in prompt
    assert "Applied the repaired ConfigMap and waited for the rollout" in prompt
    assert "All required endpoints passed and the missing-configmap detector cleared" in prompt
    # Only this responder session's shell commands.
    assert "kubectl -n hotel-reservation describe pod geo-7c9d-x2k" in prompt
    assert "kubectl delete ns unrelated" not in prompt
    # Relevant existing memory: paths plus short excerpts.
    assert ".sdo/playbooks/missing-configmap/README.md" in prompt
    assert "restore `<MISSING_CONFIG_MAP>` from source" in prompt
    assert "missing-configmap (package ./detectors/incidents/missing_configmap" in prompt
    # The same reflection instructions as a resumed first attempt.
    assert "Outcome commit: outcome-sha" in prompt
    assert "learning_decision=updated" in prompt
    assert "Required action for this success outcome" in prompt
    assert "stale_memory_detected: true" in prompt
    assert "python3 -m sdo.operational_memory.memory_check --app . --actor responder" in prompt


def test_fresh_incident_brief_stays_under_its_token_budget(tmp_path: Path) -> None:
    worktree = _memory_worktree(tmp_path / "first")
    huge = _FAILED_MOUNT_EVENT + "\n" + "x" * 200_000
    closure = _fresh_closure(worktree, evidence=huge)
    log = _turn_log(tmp_path, "019c-session-0001", [f"kubectl get pods # {i} " + "y" * 3000 for i in range(400)])

    call = _fresh_first_attempt(tmp_path / "second", closure=closure, log=log)

    prompt = str(call["prompt"])
    brief = prompt.split("Incident brief (", 1)[1].split("End of incident brief.", 1)[0]
    # About 4 characters per token: the brief stays under about 8K tokens.
    assert len(brief) <= 8_000 * 4
    assert _FAILED_MOUNT_EVENT in brief
    assert "omitted" in brief


def test_fresh_brief_without_a_shell_command_log_says_so(tmp_path: Path) -> None:
    call = _fresh_first_attempt(tmp_path, log=tmp_path / "missing.jsonl")

    assert "shell commands are not available" in str(call["prompt"])


def test_resumed_and_retry_reflection_both_run_the_memory_check(tmp_path: Path) -> None:
    first = _first_reflection_prompt(tmp_path)
    backend = _CapturingBackend()
    outcome = _outcome()
    SessionReflector(backend).resume(
        session_id="session-1",
        incident_id="inc-1",
        worktree=tmp_path,
        outcome=outcome,
        history=[outcome],
        outcome_commit="outcome-sha",
        validation_feedback="playbook is missing from index",
        rejected_proposal_diff="+x\n",
        session_mode="fresh",
    )
    retry = str(backend.calls[0]["prompt"])

    for prompt in (first, retry):
        assert "python3 -m sdo.operational_memory.memory_check --app . --actor responder" in prompt
        assert "there is no local command that checks them" not in prompt
    # A validation retry keeps its own short prompt in either mode.
    assert "Validator error:\nplaybook is missing from index" in retry


def test_reflection_states_which_kubectl_verbs_the_responder_may_use(tmp_path: Path) -> None:
    prompt = _first_reflection_prompt(tmp_path)

    # The responder's RBAC has no pods/exec, pods/portforward, or pods/attach; playbooks must not need them.
    assert "`kubectl exec`" not in prompt.split("cannot", 1)[0]
    for verb in RESPONDER_FORBIDDEN_KUBECTL_VERBS:
        assert f"`kubectl {verb}`" in prompt
    assert "responder's RBAC" in prompt
    for allowed in ("get", "logs", "patch", "rollout restart", "delete pod"):
        assert allowed in prompt
    # The representative request comes from the responder's own pod, which has python3 but no curl or wget.
    assert "python3" in prompt
    assert ".svc" in prompt
