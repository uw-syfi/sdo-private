from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from sdo.operational_memory.commit_broker import CommandProposalValidator, CommitBroker
from sdo.operational_memory.models import (
    ArtifactOwner,
    OutcomeClassification,
    OutcomeRecord,
    OutcomeTimestamps,
)
from sdo.operational_memory.repository import MemoryRepository
from sdo.operational_memory.sandbox import KubernetesJobSandboxRunner, LocalSandboxRunner
from sdo.operational_memory.validation import MemoryValidationError, MemoryValidator

if TYPE_CHECKING:
    from pathlib import Path


def _write_memory(app_root: Path, *, memory_dir: str = ".sdo", application: str = "demo") -> None:
    root = app_root / memory_dir
    detector = root / "diagnostics" / "detectors" / "incidents" / "missing_configmap"
    playbook = root / "playbooks" / "missing-configmap"
    detector.mkdir(parents=True)
    playbook.mkdir(parents=True)
    (root / "schema-version").write_text("1\n", encoding="utf-8")
    (root / "goal.md").write_text(
        f"""---
schema_version: 1
owner: human
application: {application}
---
# Health objective

The application serves successful requests.
""",
        encoding="utf-8",
    )
    (root / "arch.md").write_text(
        f"""---
schema_version: 1
generated_at_commit: abc123
generated_at: 2026-01-01T00:00:00Z
application: {application}
topology_fingerprint: topology-1
---
# Architecture

The entrypoint uses the configuration service.
""",
        encoding="utf-8",
    )
    (root / "outcomes.jsonl").write_text("", encoding="utf-8")
    (root / "playbooks" / "README.md").write_text(
        "# Playbooks\n\n- [Missing ConfigMap](missing-configmap/README.md)\n",
        encoding="utf-8",
    )
    (playbook / "README.md").write_text(
        """---
schema_version: 1
owner: responder
fault_class: missing-configmap
originating_incident: incident-seed
originating_commit: seed-commit
---
# Missing ConfigMap

Check `<TARGET_RESOURCE>` and restore `<MISSING_CONFIG_MAP>` from source.
""",
        encoding="utf-8",
    )
    (root / "diagnostics" / "manifest.yaml").write_text(
        """apiVersion: sdo.dev/v1alpha1
kind: DetectorManifest
sdkVersion: v0.1
detectors:
  - id: missing-configmap
    package: ./detectors/incidents/missing_configmap
    constructor: New
    class: incident
    owner: responder
    watches: []
    interval: 1m
    persistence:
      firing: 2
      clearing: 2
    batching:
      severity: critical
      debounce: 500ms
    possiblePlaybooks: []
    originatingIncident: incident-seed
    originatingCommit: abc123
""",
        encoding="utf-8",
    )
    (root / "diagnostics" / "go.mod").write_text(
        "module app-diagnostics\n\ngo 1.24\n\nrequire sdo.dev/controller/sdk v0.0.0\n",
        encoding="utf-8",
    )
    (detector / "detector.go").write_text(
        """package missing_configmap

import (
    "context"
    "time"

    "sdo.dev/controller/sdk"
)

func New() sdk.Detector { return Detector{} }

type Detector struct{}

func (Detector) Spec() sdk.DetectorSpec {
    return sdk.DetectorSpec{
        ID: "missing-configmap",
        Class: sdk.DetectorClassIncident,
        Owner: sdk.DetectorOwnerResponder,
        Interval: time.Minute,
        Persistence: sdk.PersistencePolicy{Firing: 2, Clearing: 2},
        Batching: sdk.BatchingPolicy{Severity: sdk.SeverityCritical, Debounce: 500 * time.Millisecond},
        OriginatingIncident: "incident-seed",
        OriginatingCommit: "abc123",
        Watches: []sdk.WatchKind{},
        Playbooks: []string{},
    }
}

func (Detector) Detect(context.Context, sdk.DetectionContext) ([]sdk.Finding, error) {
    return nil, nil
}
""",
        encoding="utf-8",
    )


def _outcome(incident_id: str = "incident-1") -> OutcomeRecord:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return OutcomeRecord(
        incident_id=incident_id,
        source_commit="source",
        deployed_commit="deployed",
        classification=OutcomeClassification.SUCCESS,
        responder_backend="codex",
        responder_model="gpt-5",
        timestamps=OutcomeTimestamps(detected_at=now, dispatched_at=now, completed_at=now),
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_repository(root: Path) -> None:
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "add", ".sdo")
    _git(root, "commit", "-m", "initial memory")


def test_repository_reads_canonical_memory_and_typed_front_matter(tmp_path: Path) -> None:
    _write_memory(tmp_path, application="canonical")

    repository = MemoryRepository(tmp_path)

    assert repository.memory_root == tmp_path / ".sdo"
    assert repository.schema_version() == 1
    assert repository.goal().metadata.application == "canonical"
    assert repository.architecture().metadata.generated_at_commit == "abc123"
    assert repository.playbooks()[0].metadata.fault_class == "missing-configmap"
    assert repository.playbooks()[0].metadata.originating_commit == "seed-commit"


def test_validator_enforces_ownership_append_only_outcomes_and_links(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    shutil.copytree(baseline, candidate)
    validator = MemoryValidator(run_diagnostics=False)

    goal = candidate / ".sdo" / "goal.md"
    goal.write_text(goal.read_text(encoding="utf-8") + "weakened\n", encoding="utf-8")
    with pytest.raises(MemoryValidationError, match="does not own"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.RESPONDER,
            changed_paths=[".sdo/goal.md"],
            baseline_root=baseline,
        )


def test_validator_prevents_cross_owner_detector_and_dependency_edits(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    manifest = baseline / ".sdo" / "diagnostics" / "manifest.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "detectors:\n",
            """detectors:
  - id: health-objective
    package: ./detectors/health/objective
    constructor: New
    class: health
    owner: health_judge
    watches: []
    interval: 30s
    persistence:
      firing: 1
      clearing: 3
    batching:
      severity: critical
      debounce: 1s
    possiblePlaybooks: []
    originatingCommit: health123
""",
        ),
        encoding="utf-8",
    )
    health_package = baseline / ".sdo" / "diagnostics" / "detectors" / "health" / "objective"
    health_package.mkdir(parents=True)
    (health_package / "detector.go").write_text("package objective\n", encoding="utf-8")
    shutil.copytree(baseline, candidate)
    validator = MemoryValidator(run_diagnostics=False)

    candidate_manifest = candidate / ".sdo" / "diagnostics" / "manifest.yaml"
    candidate_manifest.write_text(
        candidate_manifest.read_text(encoding="utf-8").replace("clearing: 3", "clearing: 1"),
        encoding="utf-8",
    )
    with pytest.raises(MemoryValidationError, match="health detector"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.RESPONDER,
            changed_paths=[".sdo/diagnostics/manifest.yaml"],
            baseline_root=baseline,
        )

    shutil.rmtree(candidate)
    shutil.copytree(baseline, candidate)
    candidate_manifest = candidate / ".sdo" / "diagnostics" / "manifest.yaml"
    candidate_manifest.write_text(
        candidate_manifest.read_text(encoding="utf-8").replace("interval: 1m", "interval: 5m"),
        encoding="utf-8",
    )
    with pytest.raises(MemoryValidationError, match="incident detector"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.HEALTH_JUDGE,
            changed_paths=[".sdo/diagnostics/manifest.yaml"],
            baseline_root=baseline,
        )

    go_mod = candidate / ".sdo" / "diagnostics" / "go.mod"
    go_mod.write_text(go_mod.read_text(encoding="utf-8") + "require example.invalid/evil v1.0.0\n", encoding="utf-8")
    with pytest.raises(MemoryValidationError, match="does not own"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.RESPONDER,
            changed_paths=[".sdo/diagnostics/go.mod"],
            baseline_root=baseline,
        )

    shutil.rmtree(candidate)
    shutil.copytree(baseline, candidate)
    index = candidate / ".sdo" / "playbooks" / "README.md"
    index.write_text("[Dead](../outside.md)\n", encoding="utf-8")
    with pytest.raises(MemoryValidationError, match="playbook index"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.RESPONDER,
            changed_paths=[".sdo/playbooks/README.md"],
            baseline_root=baseline,
        )

    shutil.rmtree(candidate)
    shutil.copytree(baseline, candidate)
    MemoryRepository(candidate).append_outcome(_outcome(), actor=ArtifactOwner.CONTROLLER)
    validator.validate(
        candidate,
        actor=ArtifactOwner.CONTROLLER,
        changed_paths=[".sdo/outcomes.jsonl"],
        baseline_root=baseline,
    )
    with pytest.raises(MemoryValidationError, match="does not own"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.RESPONDER,
            changed_paths=[".sdo/outcomes.jsonl"],
            baseline_root=baseline,
        )


def test_validator_rejects_symlinks_and_invalid_shell(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    shutil.copytree(baseline, candidate)
    scripts = candidate / ".sdo" / "playbooks" / "missing-configmap" / "scripts"
    scripts.mkdir()
    script = scripts / "diagnose.sh"
    script.write_text("if then\n", encoding="utf-8")
    validator = MemoryValidator(run_diagnostics=False)

    with pytest.raises(MemoryValidationError, match="shell syntax"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.RESPONDER,
            changed_paths=[".sdo/playbooks/missing-configmap/scripts/diagnose.sh"],
            baseline_root=baseline,
        )

    script.unlink()
    script.symlink_to(tmp_path / "outside.sh")
    with pytest.raises(MemoryValidationError, match="symlink"):
        validator.validate(
            candidate,
            actor=ArtifactOwner.RESPONDER,
            changed_paths=[".sdo/playbooks/missing-configmap/scripts/diagnose.sh"],
            baseline_root=baseline,
        )


def test_outcome_only_validation_does_not_recompile_unchanged_detectors(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_memory(baseline)
    shutil.copytree(baseline, candidate)
    MemoryRepository(candidate).append_outcome(_outcome(), actor=ArtifactOwner.CONTROLLER)

    class FailingIfCalledSandbox:
        def run(self, _app_root: Path) -> None:
            raise AssertionError("unchanged diagnostics must not enter executable validation")

    validator = MemoryValidator(sandbox_runner=FailingIfCalledSandbox())  # type: ignore[arg-type]

    assert (
        validator.validate(
            candidate,
            actor=ArtifactOwner.CONTROLLER,
            changed_paths=[".sdo/outcomes.jsonl"],
            baseline_root=baseline,
        )
        == ()
    )


def test_validator_runs_generated_detector_test_and_build_gate(tmp_path: Path) -> None:
    _write_memory(tmp_path)

    MemoryValidator(sandbox_runner=LocalSandboxRunner()).validate(
        tmp_path,
        actor=ArtifactOwner.RESPONDER,
        changed_paths=[".sdo/playbooks/missing-configmap/README.md"],
    )


def test_local_sandbox_preserves_runtime_python_import_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured_environment: dict[str, str] = {}

    def command_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_environment.update(kwargs["env"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    expected_runtime_environment = {
        "PYTHONPATH": "/opt/sdo",
        "GOMODCACHE": "/go/pkg/mod",
        "GOPROXY": "off",
        "GOSUMDB": "off",
        "GOMAXPROCS": "2",
        "GOFLAGS": "-p=2",
    }
    for name, value in expected_runtime_environment.items():
        monkeypatch.setenv(name, value)

    result = LocalSandboxRunner(command_runner=command_runner).run(tmp_path)

    assert result.returncode == 0
    for name, value in expected_runtime_environment.items():
        assert captured_environment[name] == value
    assert captured_environment["PATH"]


def test_kubernetes_validator_job_isolated_from_cluster_credentials_network_and_other_worktrees(
    tmp_path: Path,
) -> None:
    repository_mount = tmp_path / "workspace"
    worktree = repository_mount / "worktrees" / "incident-1"
    worktree.mkdir(parents=True)
    created: list[dict[str, object]] = []
    commands: list[list[str]] = []

    def command_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if "create" in command:
            created.append(json.loads(str(kwargs["input"])))
            return subprocess.CompletedProcess(command, 0, "", "")
        if "get" in command:
            return subprocess.CompletedProcess(command, 0, '{"status":{"succeeded":1}}', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = KubernetesJobSandboxRunner(
        namespace="demo",
        image="sdo-detector-validator:test",
        repository_pvc="application-repository",
        repository_mount_path=repository_mount,
        command_runner=command_runner,
        poll_interval_seconds=0,
    ).run(worktree)

    assert result.returncode == 0
    assert [evidence.mode for evidence in result.network_policy_canaries] == ["allow", "deny"]
    assert all(evidence.passed for evidence in result.network_policy_canaries)
    assert all(evidence.job_name for evidence in result.network_policy_canaries)
    assert all(evidence.details for evidence in result.network_policy_canaries)
    policy = next(resource for resource in created if resource["kind"] == "NetworkPolicy")
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]  # type: ignore[index]
    assert policy["spec"]["ingress"] == []  # type: ignore[index]
    assert policy["spec"]["egress"] == []  # type: ignore[index]
    jobs = [resource for resource in created if resource["kind"] == "Job"]
    assert len(jobs) == 3
    assert {job["metadata"]["labels"]["sdo.dev/network-canary"] for job in jobs[:2]} == {  # type: ignore[index]
        "allow",
        "deny",
    }
    job = next(
        resource
        for resource in jobs
        if not resource["metadata"]["name"].endswith(("-allow", "-deny"))  # type: ignore[index]
    )
    pod = job["spec"]["template"]["spec"]  # type: ignore[index]
    assert pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod
    assert pod["enableServiceLinks"] is False
    container = pod["containers"][0]
    assert "envFrom" not in container
    assert container["volumeMounts"] == [
        {
            "name": "application",
            "mountPath": "/workspace",
            "subPath": "worktrees/incident-1",
            "readOnly": True,
        },
        {"name": "scratch", "mountPath": "/tmp"},
    ]
    assert {volume["name"] for volume in pod["volumes"]} == {"application", "scratch"}
    assert pod["volumes"][0]["persistentVolumeClaim"] == {
        "claimName": "application-repository",
        "readOnly": True,
    }
    assert pod["volumes"][1]["emptyDir"] == {"sizeLimit": "3Gi"}
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["resources"]["limits"]["cpu"] == "2"
    environment = {item["name"]: item["value"] for item in container["env"]}
    assert environment["GOMAXPROCS"] == "2"
    assert environment["GOFLAGS"] == "-p=2"
    assert environment["SDO_GO_CACHE_SEED"] == "/opt/sdo/go-build-cache"
    assert job["spec"]["activeDeadlineSeconds"] == 600  # type: ignore[index]
    assert any("delete" in command for command in commands)


def test_kubernetes_validator_fails_closed_when_network_policy_deny_canary_can_reach_api(
    tmp_path: Path,
) -> None:
    repository_mount = tmp_path / "workspace"
    worktree = repository_mount / "worktrees" / "incident-1"
    worktree.mkdir(parents=True)
    created_resources: list[tuple[str, str]] = []

    def command_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "create" in command:
            resource = json.loads(str(kwargs["input"]))
            created_resources.append((resource["kind"], resource["metadata"]["name"]))
            return subprocess.CompletedProcess(command, 0, "", "")
        if "pods" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                '{"items":[{"status":{"containerStatuses":[{"state":{"terminated":'
                '{"message":"egress unexpectedly reachable"}}}]}}]}',
                "",
            )
        if "get" in command and any(part.endswith("-deny") for part in command):
            return subprocess.CompletedProcess(command, 0, '{"status":{"failed":1}}', "")
        if "get" in command and "pods" not in command:
            return subprocess.CompletedProcess(command, 0, '{"status":{"succeeded":1}}', "")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = KubernetesJobSandboxRunner(
        namespace="demo",
        image="validator:test",
        repository_pvc="repository",
        repository_mount_path=repository_mount,
        command_runner=command_runner,
        poll_interval_seconds=0,
    ).run(worktree)

    assert result.returncode != 0
    assert "egress unexpectedly reachable" in result.stderr
    created_jobs = [name for kind, name in created_resources if kind == "Job"]
    assert not any(
        name.startswith("sdo-validator-") and not name.endswith(("-allow", "-deny")) for name in created_jobs
    )


def test_kubernetes_validator_rejects_path_outside_shared_repository_mount(tmp_path: Path) -> None:
    calls = 0

    def command_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del command, kwargs
        calls += 1
        return subprocess.CompletedProcess([], 0, "", "")

    result = KubernetesJobSandboxRunner(
        namespace="demo",
        image="validator:test",
        repository_pvc="repository",
        repository_mount_path=tmp_path / "workspace",
        command_runner=command_runner,
    ).run(tmp_path / "outside")

    assert result.returncode == 125
    assert "outside shared repository mount" in result.stderr
    assert calls == 0


def test_kubernetes_validator_reports_bounded_termination_diagnostics(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    worktree = root / "worktrees" / "incident"
    worktree.mkdir(parents=True)

    def command_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "create" in command or "delete" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if "pods" in command:
            payload = {
                "items": [
                    {
                        "status": {
                            "containerStatuses": [{"state": {"terminated": {"message": "detector.go: unused import"}}}]
                        }
                    }
                ]
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if "get" in command and any(part.startswith("job/") for part in command):
            job_name = next(part for part in command if part.startswith("job/"))
            if job_name.endswith(("-allow", "-deny")):
                return subprocess.CompletedProcess(command, 0, '{"status":{"succeeded":1}}', "")
            return subprocess.CompletedProcess(command, 0, '{"status":{"failed":1}}', "")
        raise AssertionError(command)

    result = KubernetesJobSandboxRunner(
        namespace="demo",
        image="validator:test",
        repository_pvc="repository",
        repository_mount_path=root,
        command_runner=command_runner,
        poll_interval_seconds=0,
    ).run(worktree)

    assert result.returncode == 1
    assert "detector.go: unused import" in result.stderr


def test_commit_broker_creates_one_validated_commit_and_rejects_invalid_candidate(tmp_path: Path) -> None:
    target = tmp_path / "target"
    accepted_worktree = tmp_path / "accepted"
    rejected_worktree = tmp_path / "rejected"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    _git(target, "worktree", "add", "-b", "incident-accepted", str(accepted_worktree))
    before = _git(target, "rev-parse", "HEAD")
    MemoryRepository(accepted_worktree).append_outcome(_outcome(), actor=ArtifactOwner.CONTROLLER)
    broker = CommitBroker(target, validator=MemoryValidator(run_diagnostics=False))

    result = broker.commit(
        incident_worktree=accepted_worktree,
        incident_id="incident-1",
        actor=ArtifactOwner.CONTROLLER,
    )

    assert _git(target, "rev-list", "--count", f"{before}..HEAD") == "1"
    assert _git(target, "rev-parse", "HEAD") == result.commit_sha
    assert "SDO-Incident: incident-1" in _git(target, "show", "-s", "--format=%B", "HEAD")

    _git(target, "worktree", "add", "-b", "incident-rejected", str(rejected_worktree))
    rejected_before = _git(target, "rev-parse", "HEAD")
    goal = rejected_worktree / ".sdo" / "goal.md"
    goal.write_text(goal.read_text(encoding="utf-8") + "weaken health\n", encoding="utf-8")
    with pytest.raises(MemoryValidationError, match="does not own"):
        broker.commit(
            incident_worktree=rejected_worktree,
            incident_id="incident-2",
            actor=ArtifactOwner.RESPONDER,
        )
    assert _git(target, "rev-parse", "HEAD") == rejected_before


def test_commit_broker_rebases_serialized_non_conflicting_proposals(tmp_path: Path) -> None:
    target = tmp_path / "target"
    first_worktree = tmp_path / "first"
    second_worktree = tmp_path / "second"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    _git(target, "worktree", "add", "-b", "first-proposal", str(first_worktree))
    _git(target, "worktree", "add", "-b", "second-proposal", str(second_worktree))
    first_script = first_worktree / ".sdo" / "playbooks" / "missing-configmap" / "scripts" / "diagnose.sh"
    second_script = second_worktree / ".sdo" / "playbooks" / "missing-configmap" / "scripts" / "verify.sh"
    first_script.parent.mkdir()
    second_script.parent.mkdir()
    first_script.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    second_script.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    broker = CommitBroker(target, validator=MemoryValidator(run_diagnostics=False))

    broker.commit(incident_worktree=first_worktree, incident_id="first", actor=ArtifactOwner.RESPONDER)
    broker.commit(incident_worktree=second_worktree, incident_id="second", actor=ArtifactOwner.RESPONDER)

    assert _git(target, "rev-list", "--count", "HEAD~2..HEAD") == "2"
    assert (target / ".sdo" / "playbooks" / "missing-configmap" / "scripts" / "diagnose.sh").is_file()
    assert (target / ".sdo" / "playbooks" / "missing-configmap" / "scripts" / "verify.sh").is_file()


def test_commit_broker_squashes_and_validates_precommitted_responder_repair(tmp_path: Path) -> None:
    target = tmp_path / "target"
    worktree = tmp_path / "incident"
    target.mkdir()
    _write_memory(target)
    _init_repository(target)
    source = target / "application.txt"
    source.write_text("before\n", encoding="utf-8")
    _git(target, "add", "application.txt")
    _git(target, "commit", "-m", "application source")
    base = _git(target, "rev-parse", "HEAD")
    _git(target, "worktree", "add", "-b", "precommitted-repair", str(worktree))
    (worktree / "application.txt").write_text("after\n", encoding="utf-8")
    _git(worktree, "add", "application.txt")
    _git(worktree, "commit", "-m", "responder-authored repair")
    untrusted_commit = _git(worktree, "rev-parse", "HEAD")
    broker = CommitBroker(
        target,
        validator=MemoryValidator(run_diagnostics=False),
        proposal_validator=CommandProposalValidator([["git", "diff", "--check", "HEAD", "--"]]),
    )

    result = broker.commit_proposal(
        incident_worktree=worktree,
        incident_id="incident-precommitted",
        allow_empty=True,
    )

    assert result.changed_paths == ("application.txt",)
    assert (target / "application.txt").read_text(encoding="utf-8") == "after\n"
    assert _git(target, "rev-list", "--count", f"{base}..HEAD") == "1"
    assert result.commit_sha != untrusted_commit
    assert "SDO-Phase: proposal" in _git(target, "show", "-s", "--format=%B", "HEAD")
