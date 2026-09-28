from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest

from benchmarks.sregym.fastloop.assurance import responder
from benchmarks.sregym.fastloop.assurance.catalog import (
    COMPOSITES,
    MISSING_CONFIGMAP,
    NETWORK_POLICY_BLOCK,
    SINGLE_FAULTS,
    CompositeCase,
    FaultCase,
    WrongFix,
    composite,
    single_fault,
)
from benchmarks.sregym.fastloop.assurance.harness import (
    ControllerSettings,
    broker_ledger,
    controller_argv,
    prober_manifests,
)
from benchmarks.sregym.fastloop.assurance.suite import Bounds, diff_reading, scripted_result
from sdo.contracts.models import IncidentRequest, IncidentResult
from sdo.operational_memory import BrokerService, CommitBroker
from sdo.operational_memory.diagnosis import verify_diagnosis

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 28, 9, 0, 0, tzinfo=timezone.utc)


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "schema_version": "sdo.dev/v1alpha1",
        "application": "hotel-reservation",
        "namespace": "hotel-reservation",
        "incident_id": "hotel-reservation-1790000000000000000",
        "findings": [
            {
                "detector_id": "traffic-health",
                "rule_id": "scenario-slo.hotel-search",
                "status": "active",
                "severity": "critical",
                "summary": "hotel-search is failing",
                "evidence": "connection refused",
                "primary_resource": {"kind": "Service", "namespace": "hotel-reservation", "name": "frontend"},
                "fingerprint": "abc",
            }
        ],
        "detector_history": [
            {
                "detector_id": "traffic-health",
                "evaluated_at": T0.isoformat(),
                "status": "firing",
                "fingerprints": ["abc"],
            }
        ],
        "surfaced_playbooks": [],
        "relevant_outcomes": [],
        "source_commit": "a" * 40,
        "deployed_commit": "a" * 40,
        "architecture_summary_path": ".sdo/arch.md",
        "health_objective_path": ".sdo/goal.md",
        "repository_worktree": "/tmp/worktree",
        "repository_base_commit": "a" * 40,
        "response_deadline": (T0 + timedelta(minutes=20)).isoformat(),
        "cancellation_token": "cancel",
        "repair_policy": "recorded-actions",
        "state_changes": {
            "baseline_at": T0.isoformat(),
            "observed_at": (T0 + timedelta(seconds=3)).isoformat(),
            "changes": [
                {
                    "kind": "Service",
                    "name": "frontend",
                    "change": "modified",
                    "fields": [{"field": "selector", "before": "a=b", "after": "a=b,c=d"}],
                }
            ],
        },
    }
    request.update(overrides)
    return request


def test_the_diff_reading_names_missing_unexpected_and_decoy_objects() -> None:
    request = _request()
    request["state_changes"]["changes"] += [
        {"kind": "ConfigMap", "name": "failure-admin-geo", "change": "modified"},
        {"kind": "Deployment", "name": "geo", "change": "modified"},
    ]

    reading = diff_reading(request, ("Service/frontend", "NetworkPolicy/deny-all-recommendation"))

    assert reading.missing == ["NetworkPolicy/deny-all-recommendation"]
    assert reading.decoys_named == ["ConfigMap/failure-admin-geo"]
    assert reading.unexpected == ["ConfigMap/failure-admin-geo", "Deployment/geo"]
    assert diff_reading(request, ("Service/frontend",), allowed=("Deployment/geo",)).unexpected == [
        "ConfigMap/failure-admin-geo"
    ]


def test_an_incident_without_a_baseline_names_nothing() -> None:
    reading = diff_reading(_request(state_changes=None), ("Service/frontend",))

    assert reading.named == []
    assert reading.missing == ["Service/frontend"]


def test_the_scripted_result_is_a_valid_result_whose_diagnosis_verifies_as_confirmed() -> None:
    request = _request()
    result = scripted_result(
        request,
        objects=("Service/frontend",),
        summary="the frontend selector matches no pods",
        actions=[
            {
                "action_id": "correct",
                "kind": "kubectl",
                "target": "Service/frontend",
                "summary": "restore the selector",
                "details": "restore the selector",
                "started_at": T0.isoformat(),
                "completed_at": (T0 + timedelta(seconds=1)).isoformat(),
                "success": True,
                "reversible": True,
            }
        ],
        started_at=T0,
        verification=[],
    )

    parsed = IncidentResult.model_validate(result)
    request_model = IncidentRequest.model_validate(request)
    cleared = [
        {
            "detector_id": "traffic-health",
            "evaluated_at": (T0 + timedelta(minutes=1)).isoformat(),
            "status": "clear",
            "fingerprints": [],
        }
    ]
    verdicts = verify_diagnosis(
        request_model,
        parsed,
        final_detector_states=[type(request_model.detector_history[0]).model_validate(item) for item in cleared],
    )

    assert [verdict.verdict.value for verdict in verdicts] == ["confirmed"]
    assert parsed.usage.llm_calls == 0


def test_a_fault_missing_from_the_requests_diff_is_cited_as_a_live_observation_not_contradicted() -> None:
    request = _request()
    result = scripted_result(
        request,
        objects=("Service/frontend", "ConfigMap/mongo-rate-script"),
        summary="two faults",
        # The responder's own repair of both objects backs the cause (F8).
        actions=[
            {
                "action_id": "correct",
                "kind": "kubectl",
                "target": "Service/frontend,ConfigMap/mongo-rate-script",
                "summary": "recover both faults",
                "details": "recover both faults",
                "started_at": T0.isoformat(),
                "completed_at": (T0 + timedelta(seconds=1)).isoformat(),
                "success": True,
                "reversible": True,
            }
        ],
        started_at=T0,
        verification=[],
    )
    parsed = IncidentResult.model_validate(result)
    request_model = IncidentRequest.model_validate(request)
    cleared = type(request_model.detector_history[0]).model_validate(
        {"detector_id": "traffic-health", "evaluated_at": (T0 + timedelta(minutes=1)).isoformat(), "status": "clear"}
    )

    kinds = {item.source: item.kind for item in parsed.confirmed_root_causes[0].evidence}
    verdicts = verify_diagnosis(request_model, parsed, final_detector_states=[cleared])

    assert kinds["Service/frontend"] == "state-change"
    assert kinds["ConfigMap/mongo-rate-script"] == "live-observation"
    assert [verdict.verdict.value for verdict in verdicts] == ["confirmed"]


def test_every_catalog_case_is_well_formed_and_findable() -> None:
    assert {case.problem_id for case in SINGLE_FAULTS} >= {
        "wrong_service_selector_hotel_reservation",
        "missing_configmap_hotel_reservation",
        "network_policy_block",
        "readiness_probe_misconfiguration_hotel_reservation",
    }
    for case in SINGLE_FAULTS:
        assert single_fault(case.name) is case
        assert single_fault(case.problem_id) is case
    for case in COMPOSITES:
        assert composite(case.name) is case
    assert NETWORK_POLICY_BLOCK.known_gap


def test_a_composite_needs_distinct_faults_on_distinct_objects() -> None:
    with pytest.raises(ValueError, match="at least two"):
        CompositeCase(name="one", faults=(MISSING_CONFIGMAP,))
    twin = FaultCase(
        name="twin",
        problem_id="other_problem",
        faulted_objects=MISSING_CONFIGMAP.faulted_objects,
        wrong_fixes=(WrongFix("decoy-regrant"),),
        service="mongodb-geo",
    )
    with pytest.raises(ValueError, match="same object"):
        CompositeCase(name="clash", faults=(MISSING_CONFIGMAP, twin))


def test_wrong_fixes_and_bounds_validate() -> None:
    with pytest.raises(ValueError, match="names its Deployment"):
        WrongFix("restart")
    with pytest.raises(ValueError, match="must be positive"):
        Bounds(detect_seconds=0)


def test_the_prober_pod_keeps_the_production_isolation() -> None:
    policy, pod = prober_manifests(
        namespace="hotel-reservation-sdo", app_namespace="hotel-reservation", image="sdo-controller:v", digest="d" * 16
    )

    assert policy["spec"]["egress"][0]["to"][0]["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "hotel-reservation"
    }
    spec = pod["spec"]
    assert spec["automountServiceAccountToken"] is False
    container = spec["containers"][0]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["resources"]["limits"] == {"cpu": "250m", "memory": "128Mi"}
    assert container["volumeMounts"][0]["subPath"] == ".sdo-prober/" + "d" * 16


def test_the_controller_runs_the_scripted_responder_and_the_reflection_free_broker(tmp_path: Path) -> None:
    settings = ControllerSettings(
        binary=tmp_path / "sdo-controller",
        app_root=tmp_path / "app",
        namespace="hotel-reservation",
        control_namespace="hotel-reservation-sdo",
        application="hotel-reservation",
        prober_url="http://127.0.0.1:9",
        spool=tmp_path / "spool",
        worktrees=tmp_path / "worktrees",
        logs=tmp_path / "logs",
        kubeconfig=tmp_path / "kubeconfig",
    )

    argv = controller_argv(settings, python="/usr/bin/python3")

    assert argv[argv.index("--dispatcher-mode") + 1] == "local"
    assert "--dispatcher-arg=benchmarks.sregym.fastloop.assurance.responder" in argv
    assert "--broker-arg=benchmarks.sregym.fastloop.assurance.broker" in argv
    assert argv[argv.index("--repair-policy") + 1] == "recorded-actions"
    assert "sdo.agent_runtime.responder.job" not in " ".join(argv)


def test_the_responder_publishes_the_request_and_returns_the_scripted_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    request = _request(response_deadline=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(responder, "POLL_SECONDS", 0.01)
    incident = request["incident_id"]

    def answer() -> None:
        path = responder.request_path(tmp_path, incident)
        while not path.is_file():
            time.sleep(0.01)
        responder.result_path(tmp_path, incident).write_text('{"status": "completed"}', encoding="utf-8")

    thread = threading.Thread(target=answer)
    thread.start()
    code = responder.main(["--spool", str(tmp_path)])
    thread.join()

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "completed"}
    assert json.loads(responder.request_path(tmp_path, incident).read_text(encoding="utf-8")) == request
    assert responder.exited_path(tmp_path, incident).is_file()


def test_the_responder_gives_up_at_the_response_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(response_deadline=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))

    assert responder.main(["--spool", str(tmp_path)]) == 1
    assert responder.exited_path(tmp_path, request["incident_id"]).is_file()


def test_the_harness_reads_the_ledger_the_broker_service_writes(tmp_path: Path) -> None:
    repository = tmp_path / "app"
    (repository / ".sdo").mkdir(parents=True)
    (repository / ".sdo" / "outcomes.jsonl").write_text("", encoding="utf-8")
    for command in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(["git", "-C", str(repository), *command], check=True)
    service = BrokerService(
        repository,
        tmp_path / "worktrees",
        broker=CommitBroker(repository),
        responder_backend="assurance-scripted",
        responder_model="none",
        repair_policy="recorded-actions",
        reflector=None,
    )
    service.prepare_incident("hotel-reservation-1")

    ledger = broker_ledger(repository, "hotel-reservation-1")

    assert ledger is not None
    assert ledger["incident_id"] == "hotel-reservation-1"
    assert broker_ledger(repository, "hotel-reservation-2") is None


def test_spool_names_are_file_safe() -> None:
    assert responder.spool_name("Hotel Reservation/1") == "Hotel_Reservation_1"
