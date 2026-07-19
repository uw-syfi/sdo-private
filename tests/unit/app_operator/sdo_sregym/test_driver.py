from __future__ import annotations

import importlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from app_operator.memory.broker_service import ControllerRolloutExpectation, ControllerRolloutRecord
from app_operator.runtime.kubernetes import (
    RuntimeInstallError,
    _controller_job_logs,
    _delete_repository_sync,
    _job_state,
    _pod_is_ready,
    _tar_environment,
    _wait_for_controller_job,
)
from app_operator.sdo_sregym.driver import (
    _cleanup_defer_timeout_seconds,
    _in_cluster_api_base,
    _relay_target_api_base,
    persist_lifecycle_seed,
    persist_production_receipt,
)
from app_operator.sdo_sregym.runtime import (
    RuntimeConfig,
    _controller_update_rollout_succeeded,
    _load_incident_ledger,
    _resolve_responder_dispatch,
    _validated_controller_rollout_record,
    run_production_runtime,
    runtime_resources,
    validate_production_receipt,
)
from libs.sregym_lib.experiment import ExperimentConfig, config_to_env
from libs.sregym_lib.pipeline import load_pipeline_config, merge_stage_config


def test_sregym_adapter_routes_only_through_production_job_controller(tmp_path: Path) -> None:
    repository = tmp_path / "workspace" / "application"
    repository.mkdir(parents=True)
    resources = runtime_resources(
        RuntimeConfig(
            repository=repository,
            namespace="hotel-reservation",
            application="Hotel Reservation",
            controller_image="sdo-controller:v1",
            responder_image="sdo-responder:v1",
            repository_pvc="sdo-repository",
            credentials_secret="sdo-codex-credentials",
            model="gpt-5",
            timeout_seconds=900,
            submission_api_base="http://sdo-sregym-bridge:8000",
            submission_relay_target_base="http://host.docker.internal:8123",
        )
    )
    controller = next(resource for resource in resources if resource["kind"] == "Job")
    assert controller["spec"]["backoffLimit"] >= 3
    # The controller already has a process-runtime duration and the host driver
    # has its own bounded wait.  A Job active deadline uses wall-clock time,
    # including time while a Kind/Docker cluster is stopped, and would therefore
    # destroy the retry Pod immediately after a long cluster restart instead of
    # letting it restore the durable ConfigMap/PVC closure state.
    assert "activeDeadlineSeconds" not in controller["spec"]
    assert controller["spec"]["podFailurePolicy"] == {
        "rules": [
            {
                "action": "Ignore",
                "onPodConditions": [{"type": "DisruptionTarget", "status": "True"}],
            }
        ]
    }
    command = controller["spec"]["template"]["spec"]["containers"][0]["args"]

    assert command[0] == "controller"
    assert "--responder-image" in command
    assert "--repository-pvc" in command
    assert "--credentials-secret" in command
    assert "/workspace/worktrees" in command
    assert any(value.endswith("app_operator.responder.broker_cli") for value in command)
    assert "evaluate-once" not in command
    assert "--responder-env=SDO_SREGYM_API_BASE=http://sdo-sregym-bridge:8000" in command
    assert "--responder-env=CODEX_HOME=/workspace/.sdo-runtime/codex" in command
    assert "--broker-arg=--validator-mode" in command
    assert "--broker-arg=kubernetes" in command
    assert "--broker-arg=--validator-image" in command
    assert "--broker-arg=sdo-detector-validator:v0.1.0" in command
    assert "--broker-arg=--validator-namespace" in command
    assert "--broker-arg=hotel-reservation" in command
    assert "--broker-arg=--validator-repository-pvc" in command
    assert "--broker-arg=sdo-repository" in command
    environment = controller["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {"name": "CODEX_HOME", "value": "/workspace/.sdo-runtime/codex"} in environment
    assert {"name": "SDO_CONTROLLER_JOB", "value": "sdo-controller-run"} in environment
    assert {
        "name": "SDO_CONTROLLER_POD_UID",
        "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
    } in environment
    assert {"name": "TMPDIR", "value": "/workspace/.sdo-runtime/build/tmp"} in environment
    assert {"name": "GOTMPDIR", "value": "/workspace/.sdo-runtime/build/go-tmp"} in environment
    assert {"name": "GOCACHE", "value": "/workspace/.sdo-runtime/build/go-cache"} in environment
    initializer = controller["spec"]["template"]["spec"]["initContainers"][0]
    assert initializer["command"] == [
        "mkdir",
        "-p",
        "/workspace/.sdo-runtime/build/tmp",
        "/workspace/.sdo-runtime/build/go-tmp",
        "/workspace/.sdo-runtime/build/go-cache",
    ]
    assert {"name": "repository", "mountPath": "/workspace"} in initializer["volumeMounts"]
    responder_role = next(
        resource
        for resource in resources
        if resource["kind"] == "Role" and resource["metadata"]["name"] == "sdo-responder"
    )
    assert {
        "apiGroups": ["networking.k8s.io"],
        "resources": ["networkpolicies"],
        "verbs": ["get", "list", "watch", "delete"],
    } in responder_role["rules"]
    relay = next(
        resource
        for resource in resources
        if resource["kind"] == "Deployment" and resource["metadata"]["name"] == "sdo-sregym-bridge"
    )
    relay_pod = relay["spec"]["template"]["spec"]
    assert relay_pod["hostNetwork"] is True
    assert relay_pod["dnsPolicy"] == "ClusterFirstWithHostNet"
    assert relay_pod["automountServiceAccountToken"] is False
    assert relay_pod["securityContext"]["runAsNonRoot"] is True
    relay_container = relay_pod["containers"][0]
    assert relay_container["args"] == [
        "--listen-port",
        "18000",
        "--target-base",
        "http://host.docker.internal:8123",
    ]
    assert relay_container["securityContext"]["allowPrivilegeEscalation"] is False
    assert relay_container["readinessProbe"]["httpGet"] == {"path": "/healthz", "port": 18000}
    service = next(
        resource
        for resource in resources
        if resource["kind"] == "Service" and resource["metadata"]["name"] == "sdo-sregym-bridge"
    )
    assert service["spec"]["ports"] == [{"name": "http", "port": 8000, "targetPort": 18000}]
    assert controller["spec"]["template"]["spec"].get("hostNetwork") is not True


def test_sregym_adapter_delegates_execution_to_production_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import app_operator.sdo_sregym.runtime as runtime

    config = RuntimeConfig(
        repository=tmp_path,
        namespace="demo",
        application="demo",
        controller_image="controller:test",
        responder_image="responder:test",
        repository_pvc="repository",
        credentials_secret="credentials",
        model="gpt-test",
        timeout_seconds=60,
        submission_api_base="http://sdo-sregym-bridge:8000",
    )
    calls: list[tuple[object, object]] = []

    def fake_run(production_config: object, extension: object) -> dict[str, bool]:
        calls.append((production_config, extension))
        return {"completed": True}

    monkeypatch.setattr(runtime, "run_kubernetes_runtime", fake_run)

    assert run_production_runtime(config) == {"completed": True}
    assert calls[0][0] is config
    assert calls[0][1].controller_args(config)[-1] == "--responder-env=SDO_SREGYM_SUBMISSION_BRIDGE=1"


def test_remote_conductor_address_is_not_rewritten() -> None:
    assert _in_cluster_api_base("http://conductor.internal:8123/") == "http://conductor.internal:8123"
    assert _relay_target_api_base("http://conductor.internal:8123/") is None


def test_local_conductor_is_routed_through_narrow_in_cluster_relay() -> None:
    assert _in_cluster_api_base("http://localhost:8123/") == "http://sdo-sregym-bridge:8000"
    assert _relay_target_api_base("http://localhost:8123/") == "http://host.docker.internal:8123"


def test_driver_reports_explicit_cleanup_timeout_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS", "1800")

    assert _cleanup_defer_timeout_seconds() == 1800


def test_adapter_persists_standalone_strict_receipt_beside_run_artifacts(tmp_path: Path) -> None:
    receipt = {
        "schema_version": "sdo.production-receipt/v1",
        "incident_id": "Hotel Reservation-incident/1",
        "completed": True,
        "acknowledged": True,
    }

    path = persist_production_receipt(receipt, tmp_path)
    updated = {**receipt, "recorded_at": "2026-07-10T12:00:00+00:00"}
    replacement = persist_production_receipt(updated, tmp_path)

    assert path.parent == tmp_path
    assert path.name == "sdo_production_receipt_strict.json"
    assert replacement == path
    assert path.suffix == ".json"
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert __import__("json").loads(path.read_text(encoding="utf-8")) == updated
    assert list(tmp_path.glob("sdo_production_receipt_*.json")) == [path]
    assert list(tmp_path.glob("*.tmp")) == []


def test_adapter_persists_validated_lifecycle_seed_outside_resettable_stage(tmp_path: Path) -> None:
    repository = tmp_path / "pipeline" / "stage_1_missing-configmap" / "application_workspace"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    provenance = repository / ".sdo" / "lifecycle-provenance.yaml"
    provenance.parent.mkdir()
    provenance.write_text("health_judge: validated\n", encoding="utf-8")
    logs_dir = repository.parent / "problem_runs" / "run" / "agent"

    seed = persist_lifecycle_seed(repository, logs_dir)

    assert seed == tmp_path / "pipeline" / "lifecycle_seed_stage1"
    assert (seed / ".git").is_dir()
    assert (seed / ".sdo" / "lifecycle-provenance.yaml").read_text(encoding="utf-8") == ("health_judge: validated\n")
    assert not seed.with_name(seed.name + ".tmp").exists()


def test_sregym_passes_run_artifact_directory_to_sdo_adapter() -> None:
    root = Path(__file__).resolve().parents[4]
    source = (root / "bench" / "sregym" / "main.py").read_text(encoding="utf-8")

    assert 'AGENT_RESULT_JSON = AGENT_LT_SUMMARY | {"cli_agent", "sdo_codex"}' in source


def test_runtime_job_state_fails_fast() -> None:
    assert _job_state({"status": {"succeeded": 1}}) == "complete"
    assert _job_state({"status": {"failed": 1}}) == "running"
    assert (
        _job_state(
            {
                "status": {
                    "failed": 6,
                    "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
                }
            }
        )
        == "failed"
    )
    assert _job_state({"status": {"active": 1}}) == "running"


def test_controller_job_poll_retries_transient_kubernetes_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import app_operator.runtime.kubernetes as runtime

    responses = iter(
        [
            subprocess.CompletedProcess([], 1, "", "Unable to connect to the server: TLS handshake timeout"),
            subprocess.CompletedProcess([], 0, '{"status":{"succeeded":1}}', ""),
        ]
    )
    checks: list[bool] = []

    def fake_kubectl(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        checks.append(bool(kwargs.get("check", True)))
        return next(responses)

    monkeypatch.setattr(runtime, "kubectl", fake_kubectl)
    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)

    _wait_for_controller_job(
        RuntimeConfig(
            repository=Path("/tmp/application"),
            namespace="demo",
            application="demo",
            controller_image="controller:test",
            responder_image="responder:test",
            repository_pvc="repository",
            credentials_secret="credentials",
            model="gpt-test",
            timeout_seconds=1,
        )
    )

    assert checks == [False, False]


def test_controller_logs_select_successful_retry_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    import app_operator.runtime.kubernetes as runtime

    calls: list[list[str]] = []

    def fake_kubectl(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:2] == ["get", "pods"]:
            payload = {
                "items": [
                    {"metadata": {"name": "controller-failed"}, "status": {"phase": "Failed"}},
                    {"metadata": {"name": "controller-success"}, "status": {"phase": "Succeeded"}},
                ]
            }
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(
            args,
            0,
            '{"controller_update_rollout":"fingerprint","returncode":0}\n',
            "",
        )

    monkeypatch.setattr(runtime, "kubectl", fake_kubectl)

    logs = _controller_job_logs("demo")

    assert "controller_update_rollout" in logs
    assert calls[-1] == ["logs", "pod/controller-success"]


def test_repository_sync_readiness_is_pollable_without_kubernetes_watch() -> None:
    assert _pod_is_ready({"status": {"conditions": [{"type": "Ready", "status": "True"}]}}) is True
    assert _pod_is_ready({"status": {"conditions": [{"type": "Ready", "status": "False"}]}}) is False
    assert _pod_is_ready({"status": {}}) is False


def test_receipt_uses_durable_request_result_pair_after_responder_job_ttl() -> None:
    incident_id = "Hotel Reservation-incident-1"
    job_name = "sdo-hotel-reservation-incident-1-deadbeef00"
    configmaps = [
        {
            "metadata": {"name": f"{job_name}-request"},
            "data": {"incident-request.json": json.dumps({"incident_id": incident_id})},
        },
        {
            "metadata": {"name": f"{job_name}-result"},
            "data": {"incident-result.json": json.dumps({"incident_id": incident_id, "status": "completed"})},
        },
    ]

    responder_jobs, result, evidence = _resolve_responder_dispatch(
        incident_id=incident_id,
        jobs=[],
        configmaps=configmaps,
    )

    assert responder_jobs == [job_name]
    assert result == {"incident_id": incident_id, "status": "completed"}
    assert evidence == "durable-request-result-configmaps"


@pytest.mark.parametrize("missing_suffix", ["-request", "-result"])
def test_receipt_rejects_incomplete_durable_responder_pair(missing_suffix: str) -> None:
    incident_id = "incident-1"
    job_name = "sdo-incident-1-deadbeef00"
    configmaps = [
        {
            "metadata": {"name": f"{job_name}-request"},
            "data": {"incident-request.json": json.dumps({"incident_id": incident_id})},
        },
        {
            "metadata": {"name": f"{job_name}-result"},
            "data": {"incident-result.json": json.dumps({"incident_id": incident_id})},
        },
    ]
    configmaps = [item for item in configmaps if not item["metadata"]["name"].endswith(missing_suffix)]

    with pytest.raises(RuntimeInstallError, match="request/result ConfigMap pair"):
        _resolve_responder_dispatch(incident_id=incident_id, jobs=[], configmaps=configmaps)


def test_repository_sync_cleanup_does_not_wait_on_filtered_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    import app_operator.runtime.kubernetes as runtime

    calls: list[tuple[list[str], str, bool]] = []

    def fake_kubectl(args: list[str], *, namespace: str, check: bool = True, **_kwargs: object) -> None:
        calls.append((args, namespace, check))

    monkeypatch.setattr(runtime, "kubectl", fake_kubectl)

    _delete_repository_sync("demo")

    assert calls == [(["delete", "pod/sdo-repository-sync", "--ignore-not-found=true", "--wait=false"], "demo", False)]


def test_runtime_tar_disables_macos_appledouble_files() -> None:
    assert _tar_environment()["COPYFILE_DISABLE"] == "1"


def test_strict_production_receipt_requires_job_route_reflection_clear_ack_and_cleanup() -> None:
    receipt = {
        "schema_version": "sdo.production-receipt/v1",
        "pre_cutover": False,
        "validator_mode": "kubernetes-job",
        "lifecycle_provenance": True,
        "production_job_dispatch": True,
        "completed": True,
        "proposal_commit": "proposal",
        "outcome_commit": "outcome",
        "reflection_commit": "reflection",
        "validator_evidence_commit": "reflection",
        "same_session_reflection": True,
        "detector_clear": [{"status": "clear", "fingerprints": []}],
        "independent_verification": [{"passed": True}],
        "validator_network_policy_canaries": [
            {
                "mode": "allow",
                "passed": True,
                "job_name": "sdo-validator-example-allow",
                "observed_at": "2026-07-10T12:00:00+00:00",
                "details": "Kubernetes API reachable before isolation policy",
            },
            {
                "mode": "deny",
                "passed": True,
                "job_name": "sdo-validator-example-deny",
                "observed_at": "2026-07-10T12:00:01+00:00",
                "details": "Kubernetes API unreachable with isolation policy",
            },
        ],
        "acknowledged": True,
        "cleaned": True,
        "remaining_worktrees": [],
        "responder_jobs": ["sdo-incident-job"],
        "controller_update_required": True,
        "controller_update_rollout": True,
        "controller_update_rollout_record": {
            "schema_version": "sdo.controller-rollout/v1",
            "incident_id": "incident-1",
            "reflection_commit": "reflection",
            "before_detector_fingerprint": "before",
            "after_detector_fingerprint": "after",
            "controller_job": "sdo-controller-run",
            "controller_pod_uid": "pod-uid",
            "started_at": "2026-07-10T12:00:00+00:00",
            "completed_at": "2026-07-10T12:00:01+00:00",
            "returncode": 0,
            "success": True,
        },
        "incident_id": "incident-1",
    }

    validate_production_receipt(receipt)
    for field in (
        "production_job_dispatch",
        "same_session_reflection",
        "acknowledged",
        "cleaned",
        "lifecycle_provenance",
        "controller_update_rollout",
    ):
        invalid = {**receipt, field: False}
        with pytest.raises(RuntimeInstallError, match=field):
            validate_production_receipt(invalid)
    with pytest.raises(RuntimeInstallError, match="detector_clear"):
        validate_production_receipt({**receipt, "detector_clear": [{"status": "clear", "fingerprints": ["stale"]}]})
    with pytest.raises(RuntimeInstallError, match="validator_evidence_commit=reflection_commit"):
        validate_production_receipt({**receipt, "validator_evidence_commit": "outcome"})

    test_double_receipt = {**receipt, "lifecycle_provenance": False}
    with pytest.raises(RuntimeInstallError, match="lifecycle_provenance"):
        validate_production_receipt(test_double_receipt)
    validate_production_receipt(test_double_receipt, allow_test_lifecycle=True)


def test_strict_production_receipt_rejects_missing_false_or_duplicate_network_policy_canaries() -> None:
    valid_canary = {
        "mode": "allow",
        "passed": True,
        "job_name": "allow-job",
        "observed_at": "2026-07-10T12:00:00+00:00",
        "details": "positive control passed",
    }
    receipt = {
        "schema_version": "sdo.production-receipt/v1",
        "pre_cutover": False,
        "validator_mode": "kubernetes-job",
        "lifecycle_provenance": True,
        "production_job_dispatch": True,
        "completed": True,
        "proposal_commit": "proposal",
        "outcome_commit": "outcome",
        "reflection_commit": "reflection",
        "validator_evidence_commit": "reflection",
        "same_session_reflection": True,
        "detector_clear": [{"status": "clear", "fingerprints": []}],
        "independent_verification": [{"passed": True}],
        "acknowledged": True,
        "cleaned": True,
        "remaining_worktrees": [],
        "responder_jobs": ["sdo-incident-job"],
        "controller_update_required": False,
        "validator_network_policy_canaries": [
            valid_canary,
            {**valid_canary, "mode": "deny", "job_name": "deny-job"},
        ],
    }

    validate_production_receipt(receipt)
    with pytest.raises(RuntimeInstallError, match="validator_network_policy_canaries"):
        validate_production_receipt(
            {key: value for key, value in receipt.items() if key != "validator_network_policy_canaries"}
        )
    with pytest.raises(RuntimeInstallError, match="passed=true"):
        validate_production_receipt(
            {
                **receipt,
                "validator_network_policy_canaries": [
                    valid_canary,
                    {**valid_canary, "mode": "deny", "passed": False, "job_name": "deny-job"},
                ],
            }
        )
    with pytest.raises(RuntimeInstallError, match="exactly one allow and one deny"):
        validate_production_receipt(
            {
                **receipt,
                "validator_network_policy_canaries": [valid_canary, {**valid_canary, "job_name": "allow-job-2"}],
            }
        )


def test_controller_update_rollout_receipt_requires_successful_structured_record() -> None:
    failed = '{"controller_update_rollout":"fingerprint","returncode":1,"source_commit":"commit"}\n'
    succeeded = '{"controller_update_rollout":"fingerprint","returncode":0,"source_commit":"commit"}\n'

    assert _controller_update_rollout_succeeded(failed) is False
    assert _controller_update_rollout_succeeded("controller_update_rollout\n") is False
    assert _controller_update_rollout_succeeded(succeeded) is True


def _rollout_ledger(*, returncode: int = 0, success: bool = True) -> dict[str, object]:
    return {
        "incident_id": "incident-1",
        "reflection_commit": "reflection",
        "controller_update_required": True,
        "controller_update_before_fingerprint": "before",
        "controller_update_after_fingerprint": "after",
        "controller_update_rollouts": [
            {
                "schema_version": "sdo.controller-rollout/v1",
                "incident_id": "incident-1",
                "reflection_commit": "reflection",
                "before_detector_fingerprint": "before",
                "after_detector_fingerprint": "after",
                "controller_job": "sdo-controller-run",
                "controller_pod_uid": "pod-uid",
                "started_at": "2026-07-10T12:00:00+00:00",
                "completed_at": "2026-07-10T12:00:01+00:00",
                "returncode": returncode,
                "success": success,
            }
        ],
    }


def test_receipt_resolves_ledger_by_result_incident_not_mtime(tmp_path: Path) -> None:
    state_root = tmp_path / "sdo-broker"
    state_root.mkdir()
    selected = state_root / "selected.json"
    unrelated = state_root / "unrelated.json"
    selected.write_text(json.dumps(_rollout_ledger()), encoding="utf-8")
    unrelated.write_text(json.dumps({"incident_id": "other"}), encoding="utf-8")
    os.utime(selected, (1, 1))
    os.utime(unrelated, (2, 2))

    assert _load_incident_ledger(state_root, "incident-1")["incident_id"] == "incident-1"


def test_receipt_rejects_missing_or_duplicate_correlated_ledgers(tmp_path: Path) -> None:
    state_root = tmp_path / "sdo-broker"
    state_root.mkdir()
    with pytest.raises(RuntimeInstallError, match="no durable broker ledger"):
        _load_incident_ledger(state_root, "incident-1")
    for name in ("one.json", "two.json"):
        (state_root / name).write_text(json.dumps(_rollout_ledger()), encoding="utf-8")
    with pytest.raises(RuntimeInstallError, match="multiple durable broker ledgers"):
        _load_incident_ledger(state_root, "incident-1")


def test_receipt_requires_exactly_one_matching_successful_durable_rollout() -> None:
    valid = _rollout_ledger()
    record = _validated_controller_rollout_record(valid)
    assert record["returncode"] == 0
    assert record["success"] is True

    for mutation, message in (
        ({"controller_update_rollouts": []}, "missing"),
        ({"reflection_commit": "different"}, "reflection"),
        ({"controller_update_before_fingerprint": "different"}, "transition"),
        ({"controller_update_rollouts": _rollout_ledger()["controller_update_rollouts"] * 2}, "exactly one"),
    ):
        with pytest.raises(RuntimeInstallError, match=message):
            _validated_controller_rollout_record({**valid, **mutation})

    with pytest.raises(RuntimeInstallError, match="returncode"):
        _validated_controller_rollout_record(_rollout_ledger(returncode=9, success=False))


def test_receipt_accepts_failed_controller_pod_before_one_success_and_never_needs_logs() -> None:
    ledger = _rollout_ledger(returncode=5, success=False)
    successful = _rollout_ledger()["controller_update_rollouts"][0]
    ledger["controller_update_rollouts"] = [*ledger["controller_update_rollouts"], successful]

    assert _validated_controller_rollout_record(ledger) == ControllerRolloutRecord.model_validate(
        successful
    ).model_dump(mode="json")
    assert _controller_update_rollout_succeeded("pod logs were garbage-collected") is False


def test_controller_supervisor_persists_rollout_with_pod_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    import controller.builder.check_cli as checker

    expectation = ControllerRolloutExpectation(
        incident_id="incident-1",
        reflection_commit="reflection",
        before_detector_fingerprint="before",
        after_detector_fingerprint="after",
    )
    records: list[dict[str, object]] = []

    monkeypatch.setenv("SDO_CONTROLLER_JOB", "sdo-controller-run")
    monkeypatch.setenv("SDO_CONTROLLER_POD_UID", "pod-uid")
    monkeypatch.setattr(checker, "_controller", lambda _args: 0)
    monkeypatch.setattr(
        checker,
        "_persist_controller_rollout",
        lambda _app, _worktrees, record: records.append(record),
    )

    assert (
        checker._execute_controller_update_rollout(
            argparse.Namespace(exit_after_closure=True, source_commit="old", duration="1h"),
            Path("/application"),
            Path("/worktrees"),
            expectation.model_dump(),
        )
        == 0
    )
    assert len(records) == 1
    record = ControllerRolloutRecord.model_validate(records[0])
    assert record.incident_id == "incident-1"
    assert record.controller_job == "sdo-controller-run"
    assert record.controller_pod_uid == "pod-uid"
    assert record.returncode == 0
    assert record.success is True


def test_production_smoke_enforces_validator_network_policy_and_adversarial_boundaries() -> None:
    root = Path(__file__).resolve().parents[4]
    source = (root / "scripts" / "smoke_sdo_runtime_kind.sh").read_text(encoding="utf-8")

    assert "disableDefaultCNI: true" in source
    assert "projectcalico/calico/v3.32.1" in source
    assert "a1df919d9721cf667accdc3e72848911b0cb25cfab7d2478ad0c996302c95744" in source
    assert "TestValidatorSandboxIsolation" in source
    assert "/var/run/secrets/kubernetes.io/serviceaccount/token" in source
    assert "/sdo/credentials/auth.json" in source
    assert "/workspace/application" in source
    assert "net.DialTimeout" in source


def test_recovery_smoke_kills_controller_during_dispatch_and_closure() -> None:
    root = Path(__file__).resolve().parents[4]
    source = (root / "scripts" / "smoke_sdo_runtime_kind.sh").read_text(encoding="utf-8")

    assert "SDO_SMOKE_INJECT_CONTROLLER_FAILURES" in source
    assert 'dispatch_state == "running"' in source
    assert 'closure_state == "pending"' in source
    assert source.count("delete pod") >= 2
    assert "first_lease_holder" in source
    assert "second_lease_holder" in source
    assert "responder_job_count" in source
    assert "acknowledged" in source
    assert "cleaned" in source


def test_registered_driver_has_no_direct_codex_or_verdict_orchestration() -> None:
    root = Path(__file__).resolve().parents[4]
    source = (root / "app_operator" / "sdo_sregym" / "driver.py").read_text(encoding="utf-8")

    assert "run_production_runtime" in source
    assert "CodingAgent" not in source
    assert "HealthJudgeVerdict" not in source
    assert "evaluate-once" not in source
    assert "run_initial_lifecycle" in source


def test_sdo_codex_is_an_external_deferred_cleanup_agent(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[4]
    registry_path = root / "sregym_agents" / "agents.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    entry = next(agent for agent in registry["agents"] if agent["name"] == "sdo_codex")

    assert entry["defer_cleanup"] is True
    assert entry["wait_for_natural_exit"] is True
    assert entry["kickoff_command"] == "uv run python -m app_operator.sdo_sregym.driver"
    env = config_to_env(ExperimentConfig(agent="sdo_codex"), project_root=tmp_path)
    assert env["SREGYM_AGENT_REGISTRY"] == str(tmp_path / "sregym_agents" / "agents.yaml")


def test_four_problem_pipeline_is_source_backed_and_chains_one_hotel_workspace() -> None:
    root = Path(__file__).resolve().parents[4]
    config = load_pipeline_config(root / "sregym_agents" / "experiments" / "sdo_codex_four_e2e.toml")
    expected = [
        "readiness_probe_misconfiguration_hotel_reservation",
        "missing_configmap_hotel_reservation",
        "wrong_service_selector_hotel_reservation",
        "network_policy_block",
    ]

    resolved = [merge_stage_config(config.defaults, stage.runner_overrides) for stage in config.stages]
    assert [stage.problems[0] for stage in resolved] == expected
    assert all(stage.agent == "sdo_codex" for stage in resolved)
    assert all(stage.app_filter == "hotel_reservation" for stage in resolved)
    assert all(stage.deploy_from_source for stage in resolved)
    assert all(stage.application_workspace == "persistent" for stage in resolved)
    assert [stage.chain_application_workspace for stage in config.stages] == [False, True, True, True]


def test_runner_honors_temporary_sregym_checkout(monkeypatch, tmp_path: Path) -> None:
    import sregym_agents.run_sregym as runner

    monkeypatch.setenv("SDO_SREGYM_DIR", str(tmp_path))
    try:
        reloaded = importlib.reload(runner)
        assert tmp_path.resolve() == reloaded._SREGYM_DIR
    finally:
        monkeypatch.delenv("SDO_SREGYM_DIR", raising=False)
        importlib.reload(runner)
