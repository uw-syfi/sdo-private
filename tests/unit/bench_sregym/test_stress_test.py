from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"
STRESS_PATH = BENCH_ROOT / "stress_test.py"


def _import_stress() -> Any:
    if str(BENCH_ROOT) not in sys.path:
        sys.path.insert(0, str(BENCH_ROOT))
    if "stress_test" in sys.modules:
        del sys.modules["stress_test"]
    spec = importlib.util.spec_from_file_location("stress_test", STRESS_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["stress_test"] = mod
    spec.loader.exec_module(mod)
    return cast("Any", mod)


# --- Pod-diff pure logic ---------------------------------------------------


def test_compute_pod_diff_detects_added_removed_changed():
    st = _import_stress()
    pre = st.PodSnapshot(
        pods={
            ("social-net", "a"): st.PodState(phase="Running", ready=True, restarts=0),
            ("social-net", "b"): st.PodState(phase="Running", ready=True, restarts=0),
        }
    )
    post = st.PodSnapshot(
        pods={
            ("social-net", "a"): st.PodState(phase="CrashLoopBackOff", ready=False, restarts=3),
            ("social-net", "c"): st.PodState(phase="Running", ready=True, restarts=0),
        }
    )

    diff = st.compute_pod_diff(pre, post)

    assert [p[1] for p in diff.added] == ["c"]
    assert [p[1] for p in diff.removed] == ["b"]
    assert len(diff.changed) == 1
    ns, name, before, after = diff.changed[0]
    assert (ns, name) == ("social-net", "a")
    assert before.ready is True
    assert after.ready is False


def test_compute_pod_diff_ignores_infra_namespaces():
    st = _import_stress()
    pre = st.PodSnapshot(
        pods={
            ("kube-system", "coredns"): st.PodState("Running", True, 0),
            ("openebs", "provisioner"): st.PodState("Running", True, 0),
            ("social-net", "user-service-1"): st.PodState("Running", True, 0),
        }
    )
    post = st.PodSnapshot(
        pods={
            ("kube-system", "coredns"): st.PodState("Running", True, 5),  # restart churn
            ("openebs", "provisioner-2"): st.PodState("Running", True, 0),  # pod renamed
            ("social-net", "user-service-1"): st.PodState("Running", True, 0),
        }
    )

    diff = st.compute_pod_diff(pre, post)

    assert diff.added == []
    assert diff.removed == []
    assert diff.changed == []


# --- Verdict ---------------------------------------------------------------


def _build_result(st, **kwargs):
    defaults = {
        "problem_id": "revoke_auth_mongodb-1",
        "worker_id": 0,
        "stages": [],
    }
    defaults.update(kwargs)
    return st.ProblemResult(**defaults)


def test_verdict_fail_when_error_stage_set():
    st = _import_stress()
    result = _build_result(st, error_stage="inject")
    assert st.compute_verdict(result) == st.Verdict.FAIL


def test_verdict_skip_when_skip_reason_set():
    st = _import_stress()
    result = _build_result(st, skip_reason="khaos required on emulated cluster")
    assert st.compute_verdict(result) == st.Verdict.SKIP


def test_verdict_pass_when_loadgen_degrades_after_injection():
    st = _import_stress()
    result = _build_result(
        st,
        pre_probe=st.ProbeResult(rounds=30, ok_rounds=29, success_rate=29 / 30),
        post_probe=st.ProbeResult(rounds=30, ok_rounds=5, success_rate=5 / 30),
    )
    assert st.compute_verdict(result) == st.Verdict.PASS


def test_verdict_yellow_when_loadgen_unaffected():
    st = _import_stress()
    result = _build_result(
        st,
        pre_probe=st.ProbeResult(rounds=30, ok_rounds=30, success_rate=1.0),
        post_probe=st.ProbeResult(rounds=30, ok_rounds=30, success_rate=1.0),
    )
    verdict = st.compute_verdict(result)
    assert verdict == st.Verdict.YELLOW
    assert any("unaffected" in r for r in result.yellow_reasons)


def test_verdict_yellow_when_baseline_unhealthy():
    st = _import_stress()
    result = _build_result(
        st,
        pre_probe=st.ProbeResult(rounds=30, ok_rounds=5, success_rate=5 / 30),
        post_probe=st.ProbeResult(rounds=30, ok_rounds=0, success_rate=0.0),
    )
    verdict = st.compute_verdict(result)
    assert verdict == st.Verdict.YELLOW
    assert any("baseline" in r.lower() for r in result.yellow_reasons)


def test_verdict_pass_when_probe_unsupported_and_no_errors():
    st = _import_stress()
    result = _build_result(
        st,
        pre_probe=st.ProbeResult(unsupported=True),
        post_probe=st.ProbeResult(unsupported=True),
    )
    assert st.compute_verdict(result) == st.Verdict.PASS


# --- sample_loadgen --------------------------------------------------------


def test_sample_loadgen_returns_unsupported_when_app_has_no_wrk():
    st = _import_stress()
    app = SimpleNamespace()  # no `wrk` attribute
    result = st.sample_loadgen(app, duration_s=0, sleep_fn=lambda _: None)
    assert result.unsupported is True


def test_sample_loadgen_aggregates_entries():
    st = _import_stress()
    entries = [
        SimpleNamespace(ok=True, number=10),
        SimpleNamespace(ok=True, number=10),
        SimpleNamespace(ok=False, number=10),
        SimpleNamespace(ok=True, number=10),
    ]
    wrk = SimpleNamespace(retrievelog=lambda start_time=None: entries)
    app = SimpleNamespace(wrk=wrk)
    result = st.sample_loadgen(app, duration_s=0, sleep_fn=lambda _: None)
    assert result.rounds == 4
    assert result.ok_rounds == 3
    assert abs(result.success_rate - 0.75) < 1e-9


def test_sample_loadgen_records_error_when_retrieve_raises():
    st = _import_stress()

    def _raise(start_time=None):
        raise RuntimeError("kubelet 500")

    wrk = SimpleNamespace(retrievelog=_raise)
    app = SimpleNamespace(wrk=wrk)
    result = st.sample_loadgen(app, duration_s=0, sleep_fn=lambda _: None)
    assert "kubelet 500" in (result.error or "")


# --- run_stress_problem orchestration -------------------------------------


class _StubProblem:
    """Minimal stand-in for a registered Problem instance."""

    def __init__(
        self, namespace="social-net", require_khaos=False, inject_raises=None, verify_raises=None, recover_raises=None
    ):
        self.namespace = namespace
        self._require_khaos = require_khaos
        self._inject_raises = inject_raises
        self._verify_raises = verify_raises
        self._recover_raises = recover_raises
        self.inject_called = False
        self.verify_called = False
        self.recover_called = False
        self.app = SimpleNamespace(name="social_network", namespace=namespace)

    def requires_khaos(self):
        return self._require_khaos

    def inject_fault(self):
        self.inject_called = True
        if self._inject_raises:
            raise self._inject_raises

    def verify_fault_applied(self):
        self.verify_called = True
        if self._verify_raises:
            raise self._verify_raises

    def recover_fault(self):
        self.recover_called = True
        if self._recover_raises:
            raise self._recover_raises


class _StubConductor:
    """Minimal Conductor surface used by run_stress_problem."""

    def __init__(self, problem, emulated=True):
        self.problem = None
        self.app = None
        self.problem_id = None
        self._baseline_captured = False
        self._preloaded_problem = problem
        self._emulated = emulated
        self.problems = SimpleNamespace(get_problem_instance=lambda pid: problem)
        self.kubectl = SimpleNamespace(is_emulated_cluster=lambda: emulated)
        self.cluster_state = SimpleNamespace(reconcile_to_baseline=dict)
        self.fix_kubernetes_called = 0
        self.deploy_called = 0
        self.undeploy_called = 0

    def fix_kubernetes(self):
        self.fix_kubernetes_called += 1

    def deploy_app(self):
        self.deploy_called += 1

    def undeploy_app(self):
        self.undeploy_called += 1


def test_run_stress_problem_skip_on_khaos_emulated():
    st = _import_stress()
    conductor = _StubConductor(_StubProblem(require_khaos=True), emulated=True)
    result = st.run_stress_problem(
        conductor,
        "kubelet_crash",
        worker_id=0,
        probe_enabled=False,
        snapshot_fn=lambda _kc: st.PodSnapshot(pods={}),
        sleep_fn=lambda _s: None,
    )
    assert result.verdict == st.Verdict.SKIP
    assert "khaos" in result.skip_reason.lower()
    assert conductor.deploy_called == 0


def test_run_stress_problem_failure_in_inject_still_recovers():
    st = _import_stress()
    problem = _StubProblem(inject_raises=RuntimeError("inject boom"))
    conductor = _StubConductor(problem, emulated=True)
    result = st.run_stress_problem(
        conductor,
        "pid",
        worker_id=1,
        probe_enabled=False,
        snapshot_fn=lambda _kc: st.PodSnapshot(pods={}),
        sleep_fn=lambda _s: None,
    )
    assert result.verdict == st.Verdict.FAIL
    assert result.error_stage == "inject"
    assert problem.recover_called is True  # best-effort recovery
    assert conductor.undeploy_called >= 1


def test_run_stress_problem_pass_happy_path_with_probes_disabled():
    st = _import_stress()
    problem = _StubProblem()
    conductor = _StubConductor(problem, emulated=True)
    snap = st.PodSnapshot(pods={("social-net", "x"): st.PodState("Running", True, 0)})
    result = st.run_stress_problem(
        conductor,
        "pid",
        worker_id=0,
        probe_enabled=False,
        snapshot_fn=lambda _kc: snap,
        sleep_fn=lambda _s: None,
    )
    assert result.verdict == st.Verdict.PASS
    assert problem.inject_called
    assert problem.verify_called
    assert problem.recover_called
    assert any(s.stage == "deploy" and s.ok for s in result.stages)
    assert any(s.stage == "inject" and s.ok for s in result.stages)
    assert any(s.stage == "verify" and s.ok for s in result.stages)
    assert any(s.stage == "recover" and s.ok for s in result.stages)


# --- Report writer ---------------------------------------------------------


def test_write_report_emits_valid_json(tmp_path):
    st = _import_stress()
    results = [
        _build_result(st, problem_id="p1", verdict=st.Verdict.PASS),
        _build_result(st, problem_id="p2", verdict=st.Verdict.FAIL, error_stage="deploy"),
    ]
    out_path = tmp_path / "report.json"
    st.write_report(results, out_path)
    data = json.loads(out_path.read_text())
    assert data["total"] == 2
    assert data["counts"]["pass"] == 1
    assert data["counts"]["fail"] == 1
    pid_to_verdict = {p["problem_id"]: p["verdict"] for p in data["problems"]}
    assert pid_to_verdict == {"p1": "pass", "p2": "fail"}


# --- Agent verifier integration -------------------------------------------


def _build_av(st, **kwargs):
    defaults = {
        "fault_confirmed": True,
        "other_faults": [],
        "reasoning": "ok",
        "raw_output": "",
        "parse_error": None,
        "elapsed_s": 1.0,
    }
    defaults.update(kwargs)
    return st.AgentVerification(**defaults)


def test_run_stress_problem_invokes_agent_verifier_after_verify():
    st = _import_stress()
    problem = _StubProblem()
    conductor = _StubConductor(problem, emulated=True)
    snap = st.PodSnapshot(pods={("social-net", "x"): st.PodState("Running", True, 0)})
    calls: list[tuple[Any, int]] = []

    def fake_av(*, problem, worker_id):
        calls.append((problem, worker_id))
        return _build_av(st, fault_confirmed=True, reasoning="confirmed")

    result = st.run_stress_problem(
        conductor,
        "pid",
        worker_id=3,
        probe_enabled=False,
        snapshot_fn=lambda _kc: snap,
        sleep_fn=lambda _s: None,
        agent_verifier_fn=fake_av,
    )
    assert len(calls) == 1
    _p, wid = calls[0]
    assert wid == 3
    assert result.agent_verification is not None
    assert result.agent_verification.fault_confirmed is True
    assert any(s.stage == "agent_verify" and s.ok for s in result.stages)


def test_run_stress_problem_skips_agent_verifier_on_inject_failure():
    st = _import_stress()
    problem = _StubProblem(inject_raises=RuntimeError("boom"))
    conductor = _StubConductor(problem, emulated=True)
    calls: list[Any] = []

    def fake_av(**kw):
        calls.append(kw)
        return _build_av(st)

    result = st.run_stress_problem(
        conductor,
        "pid",
        worker_id=0,
        probe_enabled=False,
        snapshot_fn=lambda _kc: st.PodSnapshot(pods={}),
        sleep_fn=lambda _s: None,
        agent_verifier_fn=fake_av,
    )
    assert calls == []
    assert result.agent_verification is None
    assert result.verdict == st.Verdict.FAIL


def test_run_stress_problem_skips_agent_verifier_on_verify_failure():
    st = _import_stress()
    problem = _StubProblem(verify_raises=RuntimeError("nope"))
    conductor = _StubConductor(problem, emulated=True)
    calls: list[Any] = []

    result = st.run_stress_problem(
        conductor,
        "pid",
        worker_id=0,
        probe_enabled=False,
        snapshot_fn=lambda _kc: st.PodSnapshot(pods={}),
        sleep_fn=lambda _s: None,
        agent_verifier_fn=lambda **kw: calls.append(kw) or _build_av(st),
    )
    assert calls == []
    assert result.agent_verification is None


def test_run_stress_problem_records_agent_verify_stage_failure_on_exception():
    st = _import_stress()
    problem = _StubProblem()
    conductor = _StubConductor(problem, emulated=True)

    def boom(**kw):
        raise RuntimeError("verifier crashed")

    result = st.run_stress_problem(
        conductor,
        "pid",
        worker_id=0,
        probe_enabled=False,
        snapshot_fn=lambda _kc: st.PodSnapshot(pods={}),
        sleep_fn=lambda _s: None,
        agent_verifier_fn=boom,
    )
    # The verifier crashing should NOT fail the run — it's an augmentation.
    # It gets recorded as a not-ok agent_verify stage without setting error_stage.
    av_stages = [s for s in result.stages if s.stage == "agent_verify"]
    assert len(av_stages) == 1
    assert av_stages[0].ok is False
    assert "verifier crashed" in (av_stages[0].error or "")
    assert result.error_stage is None  # not treated as a failure
    assert result.agent_verification is None


def test_verdict_downgrades_to_yellow_when_agent_reports_fault_not_confirmed():
    st = _import_stress()
    result = _build_result(st)
    result.agent_verification = _build_av(st, fault_confirmed=False, reasoning="no policy found")
    verdict = st.compute_verdict(result)
    assert verdict == st.Verdict.YELLOW
    assert any("not confirmed" in r.lower() for r in result.yellow_reasons)


def test_verdict_downgrades_to_yellow_when_agent_reports_other_faults():
    st = _import_stress()
    result = _build_result(st)
    result.agent_verification = _build_av(st, fault_confirmed=True, other_faults=["unexpected crashloop in svc X"])
    verdict = st.compute_verdict(result)
    assert verdict == st.Verdict.YELLOW
    assert any("unexpected crashloop" in r for r in result.yellow_reasons)


def test_verdict_unchanged_when_agent_parse_error():
    st = _import_stress()
    result = _build_result(st)
    result.agent_verification = _build_av(st, fault_confirmed=None, parse_error="no json block")
    verdict = st.compute_verdict(result)
    # No loadgen probe, no fail, no agent signal -> PASS.
    assert verdict == st.Verdict.PASS


def test_write_report_includes_agent_verification(tmp_path):
    st = _import_stress()
    av = _build_av(st, fault_confirmed=True, other_faults=["y"], reasoning="r", elapsed_s=2.5)
    results = [_build_result(st, problem_id="p1", verdict=st.Verdict.PASS, agent_verification=av)]
    out_path = tmp_path / "report.json"
    st.write_report(results, out_path)
    data = json.loads(out_path.read_text())
    av_json = data["problems"][0]["agent_verification"]
    assert av_json["fault_confirmed"] is True
    assert av_json["other_faults"] == ["y"]
    assert av_json["reasoning"] == "r"
    assert av_json["elapsed_s"] == 2.5
