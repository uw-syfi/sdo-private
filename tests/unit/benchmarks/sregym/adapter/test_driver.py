from __future__ import annotations

import importlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from benchmarks.sregym.adapter.driver import (
    _cleanup_defer_timeout_seconds,
    _deployed_health_objective,
    _deployed_lifecycle_context,
    _in_cluster_api_base,
    _receipt_directory,
    _relay_target_api_base,
    _remove_sdo_jobs_before_benchmark_grading,
    _submit_recorded_result,
    persist_lifecycle_seed,
    persist_production_receipt,
)
from benchmarks.sregym.adapter.runtime import (
    RuntimeConfig,
    _controller_update_rollout_succeeded,
    _load_incident_ledger,
    _memory_reuse_summary,
    _phase_timings,
    _resolve_responder_dispatch,
    _validated_controller_rollout_record,
    run_production_runtime,
    runtime_resources,
    validate_production_receipt,
)
from benchmarks.sregym.runner.experiment import ExperimentConfig, config_to_env
from benchmarks.sregym.runner.pipeline import load_pipeline_config, merge_stage_config
from sdo.controller_install.kubernetes import (
    ControllerInstallError,
    _controller_job_logs,
    _delete_repository_sync,
    _job_state,
    _pod_is_ready,
    _tar_environment,
    _wait_for_controller_job,
)
from sdo.operational_memory import ControllerRolloutExpectation, ControllerRolloutRecord


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
    assert command[command.index("--verification-timeout") + 1] == "120s"
    assert "--repository-pvc" in command
    assert "--credentials-secret" in command
    assert "/workspace/worktrees" in command
    assert any(value.endswith("sdo.agent_runtime.responder.broker_cli") for value in command)
    assert "evaluate-once" not in command
    assert "--responder-env=SDO_SREGYM_API_BASE=http://sdo-sregym-bridge:8000" in command
    assert "--responder-env=CODEX_HOME=/workspace/.sdo-runtime/codex" in command
    assert "--responder-env=SDO_RESPONDER_MODEL=gpt-5" in command
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
    import benchmarks.sregym.adapter.runtime as runtime

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

    monkeypatch.setattr(runtime, "install_controller", fake_run)

    assert run_production_runtime(config) == {"completed": True}
    assert calls[0][0] is config
    responder_instruction = calls[0][1].controller_args(config)[-1]
    assert responder_instruction.startswith("--responder-env=SDO_RESPONDER_EXTRA_INSTRUCTIONS=")
    assert "benchmarks.sregym.adapter.submission diagnosis" in responder_instruction


def test_remote_conductor_address_is_not_rewritten() -> None:
    assert _in_cluster_api_base("http://conductor.internal:8123/") == "http://conductor.internal:8123"
    assert _relay_target_api_base("http://conductor.internal:8123/") is None


def test_local_conductor_is_routed_through_narrow_in_cluster_relay() -> None:
    assert _in_cluster_api_base("http://localhost:8123/") == "http://sdo-sregym-bridge:8000"
    assert _relay_target_api_base("http://localhost:8123/") == "http://localhost:8123"


def test_wildcard_bound_conductor_is_local_and_routed_through_relay() -> None:
    """SREGym's main.py defaults API_HOSTNAME to the 0.0.0.0 bind address."""
    assert _in_cluster_api_base("http://0.0.0.0:8000") == "http://sdo-sregym-bridge:8000"
    assert _relay_target_api_base("http://0.0.0.0:8000") == "http://0.0.0.0:8000"


def test_health_objective_names_only_resources_deployed_in_the_runtime_namespace() -> None:
    payload = {
        "items": [
            {"kind": "Service", "metadata": {"name": "frontend"}},
            {"kind": "Deployment", "metadata": {"name": "mongodb-geo"}},
            {
                "kind": "Deployment",
                "metadata": {"name": "frontend"},
                "spec": {
                    "template": {
                        "spec": {
                            "volumes": [
                                {"name": "config", "configMap": {"name": "frontend-config"}},
                                {"name": "optional", "configMap": {"name": "optional-config", "optional": True}},
                            ]
                        }
                    }
                },
            },
            {"kind": "Route", "metadata": {"name": "ignored-openshift-variant"}},
        ]
    }
    calls: list[list[str]] = []

    def fake_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    context = _deployed_lifecycle_context("hotel-reservation", command_runner=fake_runner)
    objective = context.health_objective

    assert calls == [
        [
            "kubectl",
            "--namespace",
            "hotel-reservation",
            "get",
            "deployments,services,configmaps,networkpolicies",
            "--output=json",
        ]
    ]
    assert objective == (
        "The deployed Deployments named frontend, mongodb-geo remain available; "
        "the deployed Services named frontend expose ready endpoints; required non-optional ConfigMap volume "
        "references remain present; and representative requests succeed."
    )
    assert "source-backed" not in objective
    assert "ignored-openshift-variant" not in objective
    assert [(resource.kind, resource.name) for resource in context.active_resources] == [
        ("ConfigMap", "frontend-config"),
        ("Deployment", "frontend"),
        ("Deployment", "mongodb-geo"),
        ("Service", "frontend"),
    ]


def test_health_objective_requires_a_live_deployment_inventory() -> None:
    def fake_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, '{"items": []}', "")

    with pytest.raises(RuntimeError, match="no deployed Deployments"):
        _deployed_health_objective("demo", command_runner=fake_runner)


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


def test_adapter_removes_sdo_jobs_before_benchmark_grades_application_pods() -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "deleted", "")

    _remove_sdo_jobs_before_benchmark_grading(
        {
            "controller_workload": "batch/v1 Job/sdo-controller-run",
            "responder_jobs": ["sdo-incident-deadbeef"],
        },
        "demo",
        command_runner=fake_runner,
    )

    assert calls == [
        [
            "kubectl",
            "--namespace",
            "demo",
            "delete",
            "job/sdo-controller-run",
            "job/sdo-incident-deadbeef",
            "--ignore-not-found=true",
            "--wait=true",
        ]
    ]


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


def test_sregym_passes_run_artifact_directory_to_sdo_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    import benchmarks.sregym.adapter.driver as driver

    monkeypatch.setenv("AGENT_LOGS_DIR", "/logs/problem-run/agent")

    assert driver._parse_args([]).logs_dir == "/logs/problem-run/agent"


def test_sregym_adapter_passes_configured_model_to_initial_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import benchmarks.sregym.adapter.driver as driver

    captured: list[str | None] = []
    runtime_configs: list[RuntimeConfig] = []
    monkeypatch.setenv("SREGYM_DEFER_CLEANUP", "1")
    monkeypatch.setattr(driver, "get_api_base", lambda: "http://localhost:8000")
    monkeypatch.setattr(driver, "poll_stage_sync", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        driver,
        "get_app_info",
        lambda *_args, **_kwargs: {"app_name": "demo", "namespace": "demo"},
    )
    monkeypatch.setattr(driver, "_application_repository", lambda: tmp_path)
    monkeypatch.setattr(
        driver,
        "_deployed_lifecycle_context",
        lambda *_args, **_kwargs: driver.DeployedLifecycleContext(health_objective="healthy", active_resources=[]),
    )
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_args, **_kwargs: False)

    def fake_lifecycle(*_args: object, **kwargs: object) -> str:
        captured.append(kwargs["backend"].model)
        return "commit"

    monkeypatch.setattr(driver, "run_initial_lifecycle", fake_lifecycle)

    def fake_runtime(config: RuntimeConfig) -> dict[str, bool]:
        runtime_configs.append(config)
        return {
            "completed": True,
            "phase_timings_seconds": {
                "operational_recovery": 12.5,
                "post_recovery_learning_and_receipt": 4.0,
            },
        }

    monkeypatch.setattr(driver, "run_production_runtime", fake_runtime)

    result = driver._run(driver._parse_args(["--model", "gpt-5.5"]))
    assert result["completed"] is True
    assert result["lifecycle_reused"] is False
    assert set(result["driver_phase_timings_seconds"]) == {
        "conductor_wait",
        "inventory_and_lifecycle",
        "production_runtime",
        "driver_total_before_submission",
    }
    assert captured == ["gpt-5.5"]
    assert runtime_configs[0].repair_policy == "recorded-actions"
    assert runtime_configs[0].reflection_session == "resume"
    assert driver._run(driver._parse_args(["--reflection-session", "fresh"]))["completed"] is True
    assert runtime_configs[1].reflection_session == "fresh"
    assert result["incident_resolution_seconds"] == 12.5
    assert result["incident_resolution_scope"] == "detected_to_independently_verified_health"
    assert result["excluded_from_incident_resolution_seconds"] == {
        "pre_incident_inventory_and_lifecycle": pytest.approx(
            result["driver_phase_timings_seconds"]["inventory_and_lifecycle"]
        ),
        "post_recovery_learning_and_receipt": 4.0,
    }


def test_sregym_adapter_uses_trusted_worker_kubeconfig_for_controller_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import benchmarks.sregym.adapter.driver as driver

    trusted_kubeconfig = tmp_path / "worker.kubeconfig"
    monkeypatch.setenv("KUBECONFIG", "/tmp/sregym-agent-kubeconfig")
    monkeypatch.setenv("SREGYM_BASE_KUBECONFIG", str(trusted_kubeconfig))
    monkeypatch.setenv("SREGYM_DEFER_CLEANUP", "1")
    monkeypatch.setattr(driver, "get_api_base", lambda: "http://localhost:8000")
    monkeypatch.setattr(driver, "poll_stage_sync", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        driver,
        "get_app_info",
        lambda *_args, **_kwargs: {"app_name": "demo", "namespace": "demo"},
    )
    monkeypatch.setattr(driver, "_application_repository", lambda: tmp_path)
    monkeypatch.setattr(
        driver,
        "_deployed_lifecycle_context",
        lambda *_args, **_kwargs: driver.DeployedLifecycleContext(health_objective="healthy", active_resources=[]),
    )
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_args, **_kwargs: True)

    def fake_runtime(_config: RuntimeConfig) -> dict[str, bool]:
        assert os.environ["KUBECONFIG"] == str(trusted_kubeconfig)
        return {"completed": True}

    monkeypatch.setattr(driver, "run_production_runtime", fake_runtime)

    result = driver._run(driver._parse_args([]))
    assert result["completed"] is True
    assert result["lifecycle_reused"] is True


def test_receipt_falls_back_to_experiment_directory_for_registry_agents(tmp_path: Path) -> None:
    repository = tmp_path / "experiment" / "application_workspace"

    assert _receipt_directory(None, repository) == tmp_path / "experiment"
    assert _receipt_directory("/logs/problem-run/agent", repository) == Path("/logs/problem-run/agent")


def test_receipt_telemetry_separates_recovery_from_post_recovery_learning() -> None:
    closure = {
        "detected_at": "2026-09-13T21:51:04+00:00",
        "dispatched_at": "2026-09-13T21:51:05+00:00",
        "responder_completed_at": "2026-09-13T21:55:13+00:00",
        "verified_at": "2026-09-13T21:55:43+00:00",
    }

    timings = _phase_timings(closure, datetime.fromisoformat("2026-09-13T22:01:18+00:00"))

    assert timings == {
        "detection_to_dispatch": 1.0,
        "responder": 248.0,
        "verification": 30.0,
        "operational_recovery": 279.0,
        "post_recovery_learning_and_receipt": 335.0,
        "total": 614.0,
    }


def test_receipt_telemetry_identifies_deterministically_retrieved_warm_path() -> None:
    closure = {
        "request": {
            "relevant_outcomes": [
                {"match_reason": "exact-fingerprint"},
                {"match_reason": "detector-rule-resource-kind"},
            ]
        }
    }
    result = {"applied_playbooks": [{"path": ".sdo/playbooks/missing.md"}]}

    assert _memory_reuse_summary(closure, result) == {
        "candidate_count": 2,
        "match_reasons": ["detector-rule-resource-kind", "exact-fingerprint"],
        "applied_playbook_count": 1,
        "warm_path": True,
    }


def test_submit_recorded_result_finishes_benchmark_when_responder_omitted_transport() -> None:
    submissions: list[tuple[str, str]] = []
    receipt = {
        "confirmed_root_causes": [{"summary": "required ConfigMap was missing"}],
        "repair_actions": [{"summary": "created the missing ConfigMap"}],
    }

    _submit_recorded_result(
        receipt,
        "http://localhost:8000",
        current_stage=lambda _api_base: "diagnosis",
        submitter=lambda solution, *, phase, api_base: submissions.append((phase, solution)) or {},
    )

    assert submissions == [
        ("diagnosis", "required ConfigMap was missing"),
        ("mitigation", "created the missing ConfigMap"),
    ]


def test_submit_recorded_result_does_not_duplicate_completed_transport() -> None:
    submissions: list[tuple[str, str]] = []

    _submit_recorded_result(
        {},
        "http://localhost:8000",
        current_stage=lambda _api_base: "awaiting_cleanup",
        submitter=lambda solution, *, phase, api_base: submissions.append((phase, solution)) or {},
    )

    assert submissions == []


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
    import sdo.controller_install.kubernetes as runtime

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

    import sdo.controller_install.kubernetes as runtime

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

    with pytest.raises(ControllerInstallError, match="request/result ConfigMap pair"):
        _resolve_responder_dispatch(incident_id=incident_id, jobs=[], configmaps=configmaps)


def test_repository_sync_cleanup_does_not_wait_on_filtered_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    import sdo.controller_install.kubernetes as runtime

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
        "repair_policy": "commit",
        "repair_actions": [],
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
        with pytest.raises(ControllerInstallError, match=field):
            validate_production_receipt(invalid)
    with pytest.raises(ControllerInstallError, match="detector_clear"):
        validate_production_receipt({**receipt, "detector_clear": [{"status": "clear", "fingerprints": ["stale"]}]})
    with pytest.raises(ControllerInstallError, match="validator_evidence_commit=reflection_commit"):
        validate_production_receipt({**receipt, "validator_evidence_commit": "outcome"})

    test_double_receipt = {**receipt, "lifecycle_provenance": False}
    with pytest.raises(ControllerInstallError, match="lifecycle_provenance"):
        validate_production_receipt(test_double_receipt)
    validate_production_receipt(test_double_receipt, allow_test_lifecycle=True)

    # An opted-in fresh first reflection is recorded, not passed off as same-session.
    fresh = {**receipt, "same_session_reflection": False, "reflection_session_mode": "fresh"}
    validate_production_receipt(fresh)
    validate_production_receipt({**receipt, "reflection_session_mode": "resume"})
    with pytest.raises(ControllerInstallError, match="same_session_reflection"):
        validate_production_receipt({**fresh, "reflection_session_mode": "resume"})
    with pytest.raises(ControllerInstallError, match="same_session_reflection"):
        validate_production_receipt({**receipt, "reflection_session_mode": "fresh"})
    with pytest.raises(ControllerInstallError, match="reflection_session_mode"):
        validate_production_receipt({**receipt, "reflection_session_mode": "transcript"})


def test_recorded_actions_receipt_accepts_actions_without_proposal_commit() -> None:
    receipt = {
        "schema_version": "sdo.production-receipt/v1",
        "pre_cutover": False,
        "validator_mode": "kubernetes-job",
        "lifecycle_provenance": True,
        "production_job_dispatch": True,
        "completed": True,
        "repair_policy": "recorded-actions",
        "proposal_commit": None,
        "repair_actions": [
            {
                "action_id": "action-1",
                "kind": "kubernetes_patch",
                "target": "Deployment/frontend",
                "summary": "Restored service availability",
                "details": "Patched the live deployment",
                "started_at": "2026-07-10T12:00:00+00:00",
                "completed_at": "2026-07-10T12:00:01+00:00",
                "success": True,
                "reversible": True,
            }
        ],
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
                "job_name": "allow-job",
                "observed_at": "2026-07-10T12:00:00+00:00",
                "details": "positive control passed",
            },
            {
                "mode": "deny",
                "passed": True,
                "job_name": "deny-job",
                "observed_at": "2026-07-10T12:00:01+00:00",
                "details": "isolation control passed",
            },
        ],
        "acknowledged": True,
        "cleaned": True,
        "remaining_worktrees": [],
        "responder_jobs": ["sdo-incident-job"],
        "controller_update_required": False,
    }

    validate_production_receipt(receipt)
    with pytest.raises(ControllerInstallError, match="repair_actions"):
        validate_production_receipt({**receipt, "repair_actions": []})


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
        "repair_policy": "commit",
        "repair_actions": [],
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
    with pytest.raises(ControllerInstallError, match="validator_network_policy_canaries"):
        validate_production_receipt(
            {key: value for key, value in receipt.items() if key != "validator_network_policy_canaries"}
        )
    with pytest.raises(ControllerInstallError, match="passed=true"):
        validate_production_receipt(
            {
                **receipt,
                "validator_network_policy_canaries": [
                    valid_canary,
                    {**valid_canary, "mode": "deny", "passed": False, "job_name": "deny-job"},
                ],
            }
        )
    with pytest.raises(ControllerInstallError, match="exactly one allow and one deny"):
        validate_production_receipt(
            {
                **receipt,
                "validator_network_policy_canaries": [valid_canary, {**valid_canary, "job_name": "allow-job-2"}],
            }
        )


def test_strict_receipt_accepts_skipped_executable_validation_for_unchanged_diagnostics() -> None:
    receipt = {
        "schema_version": "sdo.production-receipt/v1",
        "pre_cutover": False,
        "validator_mode": "kubernetes-job",
        "validator_execution_required": False,
        "validator_skipped_reason": "unchanged-diagnostics",
        "lifecycle_provenance": True,
        "production_job_dispatch": True,
        "completed": True,
        "repair_policy": "commit",
        "repair_actions": [],
        "proposal_commit": "proposal",
        "outcome_commit": "outcome",
        "reflection_commit": "reflection",
        "validator_evidence_commit": "reflection",
        "same_session_reflection": True,
        "detector_clear": [{"status": "clear", "fingerprints": []}],
        "independent_verification": [{"passed": True}],
        "validator_network_policy_canaries": [],
        "acknowledged": True,
        "cleaned": True,
        "remaining_worktrees": [],
        "responder_jobs": ["sdo-incident-job"],
        "controller_update_required": False,
    }

    validate_production_receipt(receipt)


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
    with pytest.raises(ControllerInstallError, match="no durable broker ledger"):
        _load_incident_ledger(state_root, "incident-1")
    for name in ("one.json", "two.json"):
        (state_root / name).write_text(json.dumps(_rollout_ledger()), encoding="utf-8")
    with pytest.raises(ControllerInstallError, match="multiple durable broker ledgers"):
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
        with pytest.raises(ControllerInstallError, match=message):
            _validated_controller_rollout_record({**valid, **mutation})

    with pytest.raises(ControllerInstallError, match="returncode"):
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
    root = Path(__file__).resolve().parents[5]
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
    root = Path(__file__).resolve().parents[5]
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
    root = Path(__file__).resolve().parents[5]
    source = (root / "benchmarks" / "sregym" / "adapter" / "driver.py").read_text(encoding="utf-8")

    assert "run_production_runtime" in source
    assert "CodingAgent" not in source
    assert "HealthJudgeVerdict" not in source
    assert "evaluate-once" not in source
    assert "run_initial_lifecycle" in source


def test_sdo_codex_is_an_external_deferred_cleanup_agent(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[5]
    registry_path = root / "benchmarks" / "sregym" / "registry.yaml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    entry = next(agent for agent in registry["agents"] if agent["name"] == "sdo_codex")

    assert entry["defer_cleanup"] is True
    assert entry["wait_for_natural_exit"] is True
    assert entry["kickoff_command"] == "uv run python -m benchmarks.sregym.adapter.driver"
    env = config_to_env(ExperimentConfig(agent="sdo_codex"), project_root=tmp_path)
    assert env["SREGYM_AGENT_REGISTRY"] == str(tmp_path / "benchmarks" / "sregym" / "registry.yaml")


def test_four_problem_pipeline_is_source_backed_and_chains_one_hotel_workspace() -> None:
    root = Path(__file__).resolve().parents[5]
    config = load_pipeline_config(root / "benchmarks" / "sregym" / "experiments" / "sdo_codex_four_e2e.toml")
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
    assert all(stage.require_strict_receipt for stage in resolved)
    assert [stage.chain_application_workspace for stage in config.stages] == [False, True, True, True]


def test_runner_honors_temporary_sregym_checkout(monkeypatch, tmp_path: Path) -> None:
    import benchmarks.sregym.run as runner

    monkeypatch.setenv("SDO_SREGYM_DIR", str(tmp_path))
    try:
        reloaded = importlib.reload(runner)
        assert tmp_path.resolve() == reloaded._SREGYM_DIR
    finally:
        monkeypatch.delenv("SDO_SREGYM_DIR", raising=False)
        importlib.reload(runner)


def test_deferred_problem_starts_fault_gate_after_lifecycle_and_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import benchmarks.sregym.adapter.driver as driver

    events: list[str] = []

    class FakeGate:
        timings = {"controller_baseline_wait": 30.0, "fault_injection_request": 1.0}

        def __init__(self, namespace: str, api_base: str) -> None:
            assert (namespace, api_base) == ("demo", "http://localhost:8000")

        def start(self) -> None:
            events.append("gate-start")

        def join(self) -> None:
            events.append("gate-join")

    monkeypatch.setenv("SREGYM_DEFER_CLEANUP", "1")
    monkeypatch.setattr(driver, "get_api_base", lambda: "http://localhost:8000")
    monkeypatch.setattr(driver, "poll_stage_sync", lambda *_args, **_kwargs: "awaiting_fault_injection")
    monkeypatch.setattr(driver, "get_app_info", lambda *_args: {"app_name": "demo", "namespace": "demo"})
    monkeypatch.setattr(driver, "_application_repository", lambda: tmp_path)
    monkeypatch.setattr(
        driver,
        "_deployed_lifecycle_context",
        lambda *_args, **_kwargs: driver.DeployedLifecycleContext(health_objective="healthy", active_resources=[]),
    )
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(driver, "run_initial_lifecycle", lambda *_args, **_kwargs: events.append("lifecycle"))
    monkeypatch.setattr(driver, "_FaultGate", FakeGate)
    monkeypatch.setattr(driver, "run_production_runtime", lambda _config: events.append("runtime") or {})

    result = driver._run(driver._parse_args([]))

    assert events == ["lifecycle", "gate-start", "runtime", "gate-join"]
    assert result["fault_injection_deferred"] is True
    assert result["fault_gate_timings_seconds"] == FakeGate.timings


def test_driver_records_host_turn_usage_beside_run_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import benchmarks.sregym.adapter.driver as driver

    monkeypatch.delenv("SDO_TURN_USAGE_LOG", raising=False)
    driver._configure_turn_usage_log(str(tmp_path))
    assert os.environ["SDO_TURN_USAGE_LOG"] == str(tmp_path / "sdo_turn_usage.jsonl")

    monkeypatch.setenv("SDO_TURN_USAGE_LOG", "/elsewhere.jsonl")
    driver._configure_turn_usage_log(str(tmp_path))
    assert os.environ["SDO_TURN_USAGE_LOG"] == "/elsewhere.jsonl"


def _pod_runtime_archive(tmp_path: Path) -> bytes:
    import tarfile

    source = tmp_path / "pod-runtime"
    (source / "usage").mkdir(parents=True)
    (source / "usage" / "controller-turns.jsonl").write_text('{"model_requests": 3}\n', encoding="utf-8")
    rollout = source / "codex" / "sessions" / "2026" / "09" / "27" / "rollout-2026-09-27T00-00-00-s-1.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text('{"type": "session_meta"}\n', encoding="utf-8")
    archive = tmp_path / "runtime.tar"
    with tarfile.open(archive, "w") as bundle:
        bundle.add(source / "usage", arcname="usage")
        bundle.add(source / "codex" / "sessions", arcname="codex/sessions")
    return archive.read_bytes()


def test_runtime_exports_pod_usage_logs_and_codex_rollouts_into_run_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import benchmarks.sregym.adapter.runtime as runtime

    archive = _pod_runtime_archive(tmp_path)
    real_run = subprocess.run
    pod_commands: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if args[0] == "kubectl":
            pod_commands.append(args)
            return subprocess.CompletedProcess(args, 0, archive, b"")
        return real_run(args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    artifacts = tmp_path / "results"

    summary = runtime._export_runtime_artifacts("demo", artifacts)

    exported = artifacts / "sdo_runtime"
    assert summary == {"directory": str(exported), "error": None}
    assert (exported / "usage" / "controller-turns.jsonl").read_text(encoding="utf-8") == '{"model_requests": 3}\n'
    assert (exported / "codex" / "sessions" / "2026" / "09" / "27" / "rollout-2026-09-27T00-00-00-s-1.jsonl").is_file()
    (command,) = pod_commands
    assert command[:6] == ["kubectl", "--namespace", "demo", "exec", "sdo-repository-sync", "--"]
    script = command[-1]
    assert "/workspace/.sdo-runtime" in script
    assert "usage" in script
    assert "codex/sessions" in script
    # Credentials in the Codex and Claude homes are never exported.
    assert "auth.json" not in script
    assert ".credentials" not in script


def test_runtime_artifact_export_failure_never_fails_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import benchmarks.sregym.adapter.runtime as runtime

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args, 1, b"", b"pod not found")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)

    summary = runtime._export_runtime_artifacts("demo", tmp_path)

    assert summary["directory"] is None
    assert "pod not found" in str(summary["error"])


def test_runtime_artifact_export_without_a_destination_is_skipped(tmp_path: Path) -> None:
    import benchmarks.sregym.adapter.runtime as runtime

    assert runtime._export_runtime_artifacts("demo", None) == {"directory": None, "error": "no artifacts directory"}


def test_receipt_reflection_telemetry_distinguishes_fresh_retries_from_same_session() -> None:
    import benchmarks.sregym.adapter.runtime as runtime

    telemetry = runtime._reflection_telemetry(
        {"responder_session_id": "s-1", "reflection_attempts": 2, "reflection_fresh_retry_attempts": 1}
    )
    assert telemetry == {
        "reflection_attempts": 2,
        "reflection_fresh_retry_attempts": 1,
        "reflection_skipped_reason": None,
        "reflection_session_mode": None,
    }
    assert runtime._reflection_telemetry({}) == {
        "reflection_attempts": 0,
        "reflection_fresh_retry_attempts": 0,
        "reflection_skipped_reason": None,
        "reflection_session_mode": None,
    }


def test_receipt_same_session_reflection_holds_only_when_the_first_attempt_resumed() -> None:
    import benchmarks.sregym.adapter.runtime as runtime

    resumed = {"responder_session_id": "s-1", "reflection_commit": "r", "reflection_session_mode": "resume"}
    fresh = {**resumed, "reflection_session_mode": "fresh"}

    assert runtime._same_session_reflection(resumed) is True
    # Older ledgers, and deterministic no-op reflections, carry no mode.
    assert runtime._same_session_reflection({**resumed, "reflection_session_mode": None}) is True
    assert runtime._same_session_reflection(fresh) is False
    assert runtime._same_session_reflection({**resumed, "reflection_commit": None}) is False
    assert runtime._reflection_telemetry(fresh)["reflection_session_mode"] == "fresh"


def test_sregym_agent_config_selects_the_reflection_session_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import benchmarks.sregym.adapter.driver as driver

    monkeypatch.delenv("SREGYM_EXPERIMENT_AGENT_CONFIG", raising=False)
    assert driver._parse_args([]).reflection_session == "resume"
    monkeypatch.setenv("SREGYM_EXPERIMENT_AGENT_CONFIG", json.dumps({"reflection_session": "fresh"}))
    assert driver._parse_args([]).reflection_session == "fresh"
    with pytest.raises(SystemExit):
        driver._parse_args(["--reflection-session", "transcript"])


def test_receipt_reports_a_deterministically_skipped_reflection() -> None:
    import benchmarks.sregym.adapter.runtime as runtime

    telemetry = runtime._reflection_telemetry(
        {"reflection_attempts": 0, "reflection_skipped_reason": "repeated exact-match success: ..."}
    )

    assert telemetry["reflection_attempts"] == 0
    assert telemetry["reflection_skipped_reason"] == "repeated exact-match success: ..."


def test_driver_exports_runtime_artifacts_beside_the_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import benchmarks.sregym.adapter.driver as driver

    configs: list[RuntimeConfig] = []
    monkeypatch.setenv("SREGYM_DEFER_CLEANUP", "1")
    monkeypatch.setattr(driver, "get_api_base", lambda: "http://localhost:8000")
    monkeypatch.setattr(driver, "poll_stage_sync", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(driver, "get_app_info", lambda *_args, **_kwargs: {"app_name": "demo", "namespace": "demo"})
    monkeypatch.setattr(driver, "_application_repository", lambda: tmp_path / "application")
    monkeypatch.setattr(
        driver,
        "_deployed_lifecycle_context",
        lambda *_args, **_kwargs: driver.DeployedLifecycleContext(health_objective="healthy", active_resources=[]),
    )
    monkeypatch.setattr(driver, "reuse_initial_lifecycle_if_valid", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(driver, "persist_lifecycle_seed", lambda *_args, **_kwargs: None)

    def fake_runtime(config: RuntimeConfig) -> dict[str, bool]:
        configs.append(config)
        return {"completed": True}

    monkeypatch.setattr(driver, "run_production_runtime", fake_runtime)
    logs = tmp_path / "logs"

    driver._run(driver._parse_args(["--logs-dir", str(logs)]))

    assert configs[0].artifacts_dir == _receipt_directory(str(logs), tmp_path / "application")


def test_health_objective_never_requires_endpoints_for_external_name_services() -> None:
    payload = {
        "items": [
            {"kind": "Deployment", "metadata": {"name": "frontend"}},
            {"kind": "Service", "metadata": {"name": "frontend"}, "spec": {"selector": {"app": "frontend"}}},
            {"kind": "Service", "metadata": {"name": "jaeger"}, "spec": {"type": "ExternalName"}},
        ]
    }

    def fake_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    context = _deployed_lifecycle_context("hotel-reservation", command_runner=fake_runner)

    assert "the deployed Services named frontend expose ready endpoints" in context.health_objective
    assert "Services named frontend, jaeger" not in context.health_objective
    assert (
        "the ExternalName Services named jaeger are DNS aliases with no endpoints and must not be required to "
        "have ready endpoints" in context.health_objective
    )
    assert ("Service", "jaeger") in [(resource.kind, resource.name) for resource in context.active_resources]


def test_deployed_lifecycle_fingerprint_ignores_resource_order_and_tracks_topology() -> None:
    from benchmarks.sregym.adapter.driver import deployed_lifecycle

    def runner_for(items: list[dict[str, object]]):
        def fake_runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args, 0, json.dumps({"items": items}), "")

        return fake_runner

    frontend = {"kind": "Deployment", "metadata": {"name": "frontend"}}
    geo = {"kind": "Deployment", "metadata": {"name": "mongodb-geo"}}

    first = deployed_lifecycle("hotel-reservation", command_runner=runner_for([frontend, geo]))
    reordered = deployed_lifecycle("hotel-reservation", command_runner=runner_for([geo, frontend]))
    shrunk = deployed_lifecycle("hotel-reservation", command_runner=runner_for([frontend]))

    assert first.context == _deployed_lifecycle_context("hotel-reservation", command_runner=runner_for([frontend, geo]))
    assert first.fingerprint == reordered.fingerprint
    assert first.fingerprint != shrunk.fingerprint


def test_exported_runtime_artifacts_cover_responder_sessions_and_usage_logs() -> None:
    import posixpath

    import benchmarks.sregym.adapter.runtime as runtime
    from sdo.controller_install import CODEX_HOME_PATH, RUNTIME_STATE_ROOT
    from sdo.controller_install.kubernetes import RESPONDER_TURN_USAGE_LOG

    exported = set(runtime._EXPORTED_RUNTIME_PATHS)
    # Responder Jobs run Codex with CODEX_HOME on the workspace PVC, so their
    # session rollouts (every shell command and model request) are exported.
    assert posixpath.relpath(f"{CODEX_HOME_PATH}/sessions", RUNTIME_STATE_ROOT) in exported
    assert posixpath.relpath(posixpath.dirname(RESPONDER_TURN_USAGE_LOG), RUNTIME_STATE_ROOT) in exported


@pytest.mark.parametrize("reflection_session", ["resume", "fresh"])
def test_persistent_driver_reports_resolution_without_strict_receipt_or_job_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reflection_session: str,
) -> None:
    import benchmarks.sregym.adapter.driver as driver

    logs_dir = tmp_path / "pipeline" / "stage_1_reused-incident" / "problem" / "agent"
    repository = tmp_path / "application"
    repository.mkdir()
    state_path = tmp_path / "pipeline" / "sdo_persistent_controller.json"
    events: list[str] = []
    captured: dict[str, object] = {}

    def fake_stage(inputs: object, *, ops: object, run_lifecycle: object, inject: object) -> dict[str, object]:
        captured["inputs"] = inputs
        captured["kubeconfig"] = os.environ.get("KUBECONFIG")
        events.append("stage")
        return {
            "incident_id": "incident-1",
            "confirmed_root_causes": [{"summary": "missing ConfigMap"}],
            "repair_actions": [{"summary": "restored ConfigMap"}],
            "driver_phase_timings_seconds": {},
        }

    monkeypatch.setenv("SREGYM_DEFER_CLEANUP", "1")
    monkeypatch.setenv("SDO_PERSISTENT_CONTROLLER_STATE", str(state_path))
    monkeypatch.setenv("SREGYM_BASE_KUBECONFIG", "/trusted/kubeconfig")
    monkeypatch.setenv("KUBECONFIG", "/proxy/kubeconfig")
    monkeypatch.setattr(driver, "get_api_base", lambda: "http://localhost:8000")
    monkeypatch.setattr(driver, "poll_stage_sync", lambda *_args, **_kwargs: "awaiting_fault_injection")
    monkeypatch.setattr(driver, "get_app_info", lambda *_args: {"app_name": "Demo", "namespace": "demo"})
    monkeypatch.setattr(driver, "_application_repository", lambda: repository)
    monkeypatch.setattr(
        driver,
        "_deployed_lifecycle_context",
        lambda *_args, **_kwargs: driver.DeployedLifecycleContext(health_objective="healthy", active_resources=[]),
    )
    monkeypatch.setattr(driver, "run_persistent_stage", fake_stage)
    monkeypatch.setattr(driver, "_submit_recorded_result", lambda *_args: events.append("submit"))
    monkeypatch.setattr(driver, "signal_cleanup", lambda *_args: events.append("cleanup"))
    monkeypatch.setattr(
        driver, "_remove_sdo_jobs_before_benchmark_grading", lambda *_args, **_kwargs: pytest.fail("no job cleanup")
    )

    assert (
        driver.main(
            ["--persistent-controller", "--reflection-session", reflection_session, "--logs-dir", str(logs_dir)]
        )
        == 0
    )

    inputs = captured["inputs"]
    assert inputs.runtime_config.control_namespace == "demo-sdo"  # type: ignore[attr-defined]
    assert inputs.runtime_config.persistent is True  # type: ignore[attr-defined]
    assert inputs.runtime_config.reflection_session == reflection_session  # type: ignore[attr-defined]
    assert inputs.stage_label == "stage_1_reused-incident"  # type: ignore[attr-defined]
    assert inputs.state_path == state_path  # type: ignore[attr-defined]
    assert captured["kubeconfig"] == "/trusted/kubeconfig"
    assert os.environ["KUBECONFIG"] == "/proxy/kubeconfig"
    assert events == ["stage", "submit", "cleanup"]
    assert (logs_dir / "sdo_incident_resolution.json").is_file()
    assert not list(logs_dir.glob("sdo_production_receipt_*.json"))


def test_persistent_mode_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import benchmarks.sregym.adapter.driver as driver

    monkeypatch.delenv("SREGYM_EXPERIMENT_AGENT_CONFIG", raising=False)
    assert driver._parse_args([]).persistent_controller is False
    monkeypatch.setenv("SREGYM_EXPERIMENT_AGENT_CONFIG", json.dumps({"persistent_controller": True}))
    assert driver._parse_args([]).persistent_controller is True
