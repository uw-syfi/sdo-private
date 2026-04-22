from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"
MAIN_PATH = BENCH_ROOT / "main.py"

if not MAIN_PATH.exists():
    pytest.skip(
        "bench/sregym submodule not checked out — skipping live_deploy_cli tests",
        allow_module_level=True,
    )


def _module(name: str) -> Any:
    return cast("Any", sys.modules.setdefault(name, types.ModuleType(name)))


def _import_bench_main() -> Any:
    if str(BENCH_ROOT) not in sys.path:
        sys.path.insert(0, str(BENCH_ROOT))
    _module("psutil")
    logger_mod = _module("logger")
    logger_mod.init_logger = lambda: None
    mcp_server_pkg = _module("mcp_server")
    configs_pkg = _module("mcp_server.configs")
    load_cfg_mod = _module("mcp_server.configs.load_all_cfg")
    load_cfg_mod.mcp_server_cfg = types.SimpleNamespace(expose_server=False, mcp_server_port=9000)
    sregym_mcp_mod = _module("mcp_server.sregym_mcp_server")
    sregym_mcp_mod.app = object()
    mcp_server_pkg.configs = configs_pkg

    sregym_pkg = _module("sregym")
    conductor_pkg = _module("sregym.conductor")
    service_pkg = _module("sregym.service")
    app_workspace_mod = _module("sregym.service.app_workspace")
    app_workspace_mod.prepare_application_workspace = lambda **kwargs: Path("/tmp/app-workspace")
    app_workspace_mod.should_replay_completed_run = lambda **kwargs: False
    source_deploy_mod = _module("sregym.service.source_deploy")
    source_deploy_mod.ensure_app_supported = lambda app_name: None

    agent_launcher_mod = _module("sregym.agent_launcher")
    agent_launcher_mod.AgentLauncher = type("AgentLauncher", (), {})
    agent_exit_mod = _module("sregym.agent_exit")
    agent_exit_mod.DEFAULT_GRACEFUL_EXIT_TIMEOUT_SECONDS = 30
    agent_exit_mod.resolve_graceful_exit_timeout_seconds = lambda registration: 30
    agent_exit_mod.wait_for_process_exit = lambda *args, **kwargs: True
    agent_registry_mod = _module("sregym.agent_registry")
    agent_registry_mod.get_agent = lambda *args, **kwargs: None
    agent_registry_mod.list_agents = lambda *args, **kwargs: {}
    conductor_mod = _module("sregym.conductor.conductor")
    conductor_mod.Conductor = type("Conductor", (), {})
    conductor_api_mod = _module("sregym.conductor.conductor_api")
    conductor_api_mod.request_shutdown = lambda: None
    conductor_api_mod.run_api = lambda conductor: None
    constants_mod = _module("sregym.conductor.constants")
    constants_mod.StartProblemResult = types.SimpleNamespace(
        SUCCESS="success",
        SKIPPED_KHAOS_REQUIRED="skipped",
        SKIPPED_SOURCE_DEPLOY_UNSUPPORTED="skipped_source",
    )
    kubeconfig_mod = _module("sregym.service.kubeconfig")
    kubeconfig_mod.require_kubeconfig_path = lambda: "/tmp/kubeconfig"
    worker_infra_mod = _module("sregym.worker_infra")
    worker_infra_mod.KIND_CLUSTER_PREFIX = "sregym"
    worker_infra_mod.apply_worker_cpu_limit = lambda *args, **kwargs: None
    worker_infra_mod.create_kind_cluster = lambda *args, **kwargs: None
    worker_infra_mod.create_worker_cluster = lambda *args, **kwargs: None
    worker_infra_mod.delete_kind_cluster = lambda *args, **kwargs: None
    worker_infra_mod.delete_worker_cluster = lambda *args, **kwargs: None
    worker_infra_mod.existing_cluster_is_reusable = lambda *args, **kwargs: (False, "stubbed")
    worker_infra_mod.log_cpu_oversubscription = lambda *args, **kwargs: None
    worker_infra_mod.reuse_cluster_enabled = lambda *args, **kwargs: False
    worker_infra_mod.stable_kubeconfig_path = lambda *args, **kwargs: "/tmp/kubeconfig"
    worker_infra_mod.worker_kind_config_path = lambda *args, **kwargs: "/tmp/kind.yaml"

    sregym_pkg.conductor = conductor_pkg
    sregym_pkg.service = service_pkg

    spec = importlib.util.spec_from_file_location("bench_sregym_main", MAIN_PATH)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_live_cluster_name_is_kind_compatible():
    mod = _import_bench_main()

    cluster_name = mod._cluster_name_for_live_deployment("hotel_reservation-0418_0141")

    assert "_" not in cluster_name
    assert cluster_name == "sregym-live-hotel-reservation-0418-0141"


def test_cli_agent_launch_args_include_logs_dir_without_summary_flags():
    mod = _import_bench_main()

    extra_args = mod._build_agent_extra_args(
        agent_to_run="cli_agent",
        agent_log_dir="/tmp/problem/agent",
        agent_base_dir="/tmp/experiment/cli_agent",
        experiment_log_dir="/tmp/experiment",
        enable_summary=True,
        inject_summary=False,
        summary_model="ignored-model",
    )

    assert "--logs-dir /tmp/problem/agent" in extra_args
    assert "--no-inject-summary" in extra_args
    assert "--summary-dir" not in extra_args
    assert "--summary-model" not in extra_args


def test_benchmark_parser_accepts_deploy_from_source_flag():
    mod = _import_bench_main()

    args = mod.parse_cli_args(["--agent", "cli_agent", "--deploy-from-source"])

    assert args.deploy_from_source is True


def test_benchmark_parser_accepts_app_filter():
    mod = _import_bench_main()

    args = mod.parse_cli_args(["--agent", "cli_agent", "--app-filter", "hotel_reservation"])

    assert args.app_filter == "hotel_reservation"


def test_benchmark_parser_accepts_application_workspace_flag():
    mod = _import_bench_main()

    args = mod.parse_cli_args(
        [
            "--agent",
            "cli_agent",
            "--app-filter",
            "hotel_reservation",
            "--deploy-from-source",
            "--application-workspace",
        ]
    )

    assert args.application_workspace is True


def test_application_workspace_requires_app_filter():
    mod = _import_bench_main()

    with pytest.raises(SystemExit):
        mod.parse_cli_args(["--agent", "cli_agent", "--deploy-from-source", "--application-workspace"])


def test_application_workspace_requires_deploy_from_source():
    mod = _import_bench_main()

    with pytest.raises(SystemExit):
        mod.parse_cli_args(["--agent", "cli_agent", "--app-filter", "hotel_reservation", "--application-workspace"])


def test_application_workspace_requires_single_worker():
    mod = _import_bench_main()

    with pytest.raises(SystemExit):
        mod.parse_cli_args(
            [
                "--agent",
                "cli_agent",
                "--app-filter",
                "hotel_reservation",
                "--deploy-from-source",
                "--application-workspace",
                "--parallel",
                "2",
            ]
        )


def test_live_parser_accepts_deploy_from_source_flag():
    mod = _import_bench_main()

    args = mod.parse_cli_args(["deploy", "--app", "hotel_reservation", "--deploy-from-source"])

    assert args.deploy_from_source is True


def test_experiment_dir_has_prior_results_requires_completed_csv(tmp_path: Path):
    mod = _import_bench_main()
    problem_dir = tmp_path / "problem_runs" / "0422_0000_wrong_service_selector_hotel_reservation"
    problem_dir.mkdir(parents=True)
    (problem_dir / "results_partial.csv").write_text("problem_id\nfoo\n", encoding="utf-8")
    assert mod._experiment_dir_has_prior_results(str(tmp_path)) is False

    (problem_dir / "results_complete.csv").write_text(
        'problem_id,"Diagnosis.success","Mitigation.success"\n"foo","True","True"\n',
        encoding="utf-8",
    )
    assert mod._experiment_dir_has_prior_results(str(tmp_path)) is True


def test_count_completed_problem_results_reads_problem_run_directories(tmp_path: Path):
    mod = _import_bench_main()
    first_run = tmp_path / "problem_runs" / "0422_0000_wrong_service_selector_hotel_reservation"
    first_run.mkdir(parents=True)
    (first_run / "results_complete.csv").write_text(
        'problem_id,"Diagnosis.success","Mitigation.success"\n'
        '"wrong_service_selector_hotel_reservation","True","True"\n',
        encoding="utf-8",
    )

    replay_run = tmp_path / "problem_runs" / "0422_0100_wrong_service_selector_hotel_reservation_replay1"
    replay_run.mkdir(parents=True)
    (replay_run / "results_complete.csv").write_text(
        'problem_id,"Diagnosis.success","Mitigation.success"\n'
        '"wrong_service_selector_hotel_reservation","True","True"\n',
        encoding="utf-8",
    )

    other_problem = tmp_path / "problem_runs" / "0422_0200_wrong_dns_policy_hotel_reservation"
    other_problem.mkdir(parents=True)
    (other_problem / "results_complete.csv").write_text(
        'problem_id,"Diagnosis.success","Mitigation.success"\n"wrong_dns_policy_hotel_reservation","True","True"\n',
        encoding="utf-8",
    )

    assert mod._count_completed_problem_results(str(tmp_path), "wrong_service_selector_hotel_reservation") == 2


def test_partition_resumed_problems_uses_problem_run_results(tmp_path: Path):
    mod = _import_bench_main()
    completed_dir = tmp_path / "problem_runs" / "0422_0000_wrong_service_selector_hotel_reservation"
    completed_dir.mkdir(parents=True)
    (completed_dir / "results_complete.csv").write_text(
        'problem_id,"Diagnosis.success","Mitigation.success"\n'
        '"wrong_service_selector_hotel_reservation","True","True"\n',
        encoding="utf-8",
    )

    pending, completed = mod._partition_resumed_problems(
        str(tmp_path),
        [
            "wrong_service_selector_hotel_reservation",
            "wrong_dns_policy_hotel_reservation",
        ],
        repeat=1,
    )

    assert pending == ["wrong_dns_policy_hotel_reservation"]
    assert completed == ["wrong_service_selector_hotel_reservation"]


def test_filter_problem_ids_by_app_respects_aliases():
    mod = _import_bench_main()

    def hotel_factory():
        return None

    def social_factory():
        return None

    problem_source = types.SimpleNamespace(
        get_problem=lambda problem_id: {
            "hotel_problem": hotel_factory,
            "social_problem": social_factory,
        }[problem_id]
    )

    hotel_factory.__name__ = "hotel_factory"
    social_factory.__name__ = "social_factory"
    hotel_factory.__qualname__ = "hotel_factory"
    social_factory.__qualname__ = "social_factory"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        mod.inspect,
        "getsource",
        lambda obj: 'app_name="hotel_reservation"' if obj is hotel_factory else 'app_name="social_network"',
    )
    filtered = mod._filter_problem_ids_by_app(
        problem_source,
        ["hotel_problem", "social_problem"],
        "hotel_reservation",
    )
    monkeypatch.undo()

    assert filtered == ["hotel_problem"]


def test_infer_problem_app_name_from_signature_default():
    mod = _import_bench_main()

    class _Factory:
        def __init__(self, app_name: str = "hotel_reservation"):
            self.app_name = app_name

    assert mod._infer_problem_app_name(_Factory) == "Hotel Reservation"


def test_live_deploy_from_source_rejects_unsupported_app(tmp_path: Path, monkeypatch):
    mod = _import_bench_main()
    created_clusters: set[str] = set()
    app = _FakeApp()
    app.name = "Fleet Cast"
    problem = _FakeProblem(app)

    def fake_create_kind_cluster(cluster_name: str, kubeconfig_path: str) -> None:
        created_clusters.add(cluster_name)
        path = Path(kubeconfig_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("apiVersion: v1\n", encoding="utf-8")

    monkeypatch.setattr(mod, "_create_kind_cluster", fake_create_kind_cluster)
    monkeypatch.setattr(mod, "_existing_cluster_is_reusable", lambda *args, **kwargs: (False, "missing"))
    monkeypatch.setattr(mod, "_delete_live_cluster", lambda cluster_name: created_clusters.discard(cluster_name))
    monkeypatch.setattr(mod, "Conductor", lambda: _FakeConductor(app=app, problem=problem))
    monkeypatch.setattr(
        mod,
        "ensure_app_supported",
        lambda app_name: (_ for _ in ()).throw(RuntimeError(f"unsupported: {app_name}")),
    )
    monkeypatch.setattr(mod, "_start_frontend_port_forward", lambda **_: None)
    monkeypatch.setattr(mod, "_stop_trace_port_forward", lambda app: None)

    with pytest.raises(RuntimeError, match="unsupported: Fleet Cast"):
        mod.deploy_live_environment(
            app_name="fleet_cast",
            problem_id=None,
            deployment_name="unsupported-live",
            deployments_root=str(tmp_path),
            deploy_from_source=True,
        )


def test_live_undeploy_keeps_shared_cluster_and_reconciles(tmp_path: Path, monkeypatch):
    mod = _import_bench_main()
    created_clusters: set[str] = set()
    deleted_clusters: list[str] = []
    conductors: list[Any] = []
    app = _FakeApp()
    problem = _FakeProblem(app)

    def fake_create_kind_cluster(cluster_name: str, kubeconfig_path: str) -> None:
        created_clusters.add(cluster_name)
        path = Path(kubeconfig_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("apiVersion: v1\n", encoding="utf-8")

    def fake_existing_cluster_is_reusable(cluster_name: str, kubeconfig_path: str) -> tuple[bool, str]:
        if cluster_name in created_clusters and Path(kubeconfig_path).exists():
            return True, ""
        return False, "missing"

    def fake_delete_live_cluster(cluster_name: str) -> None:
        deleted_clusters.append(cluster_name)
        created_clusters.discard(cluster_name)

    def fake_conductor_factory() -> Any:
        conductor = _FakeConductor(app=app, problem=problem)
        conductors.append(conductor)
        return conductor

    monkeypatch.setattr(mod, "Conductor", fake_conductor_factory)
    monkeypatch.setattr(mod, "_create_kind_cluster", fake_create_kind_cluster)
    monkeypatch.setattr(mod, "_existing_cluster_is_reusable", fake_existing_cluster_is_reusable)
    monkeypatch.setattr(mod, "_delete_live_cluster", fake_delete_live_cluster)
    monkeypatch.setattr(mod, "_start_frontend_port_forward", lambda **_: None)
    monkeypatch.setattr(mod, "_stop_trace_port_forward", lambda app: None)

    state = mod.deploy_live_environment(
        app_name="hotel_reservation",
        problem_id=None,
        deployment_name="first-live",
        deployments_root=str(tmp_path),
    )
    result = mod.undeploy_live_environment(state.deployment_name, deployments_root=str(tmp_path))

    assert result.cluster_deleted is False
    assert deleted_clusters == []
    assert len(created_clusters) == 1
    assert conductors[1].cluster_state.baseline is not None
    assert conductors[1].cluster_state.reconcile_calls == 1
    assert conductors[1].undeploy_app_calls == 1
    assert app.cleanup_calls == 1

    shared_state_path = Path(mod._shared_live_cluster_state_path(str(tmp_path)))
    assert shared_state_path.exists()


def test_live_redeploy_reuses_existing_shared_cluster(tmp_path: Path, monkeypatch):
    mod = _import_bench_main()
    created_clusters: set[str] = set()
    create_calls: list[str] = []
    app = _FakeApp()
    problem = _FakeProblem(app)

    def fake_create_kind_cluster(cluster_name: str, kubeconfig_path: str) -> None:
        create_calls.append(cluster_name)
        created_clusters.add(cluster_name)
        path = Path(kubeconfig_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("apiVersion: v1\n", encoding="utf-8")

    def fake_existing_cluster_is_reusable(cluster_name: str, kubeconfig_path: str) -> tuple[bool, str]:
        if cluster_name in created_clusters and Path(kubeconfig_path).exists():
            return True, ""
        return False, "missing"

    monkeypatch.setattr(mod, "Conductor", lambda: _FakeConductor(app=app, problem=problem))
    monkeypatch.setattr(mod, "_create_kind_cluster", fake_create_kind_cluster)
    monkeypatch.setattr(mod, "_existing_cluster_is_reusable", fake_existing_cluster_is_reusable)
    monkeypatch.setattr(mod, "_delete_live_cluster", lambda cluster_name: created_clusters.discard(cluster_name))
    monkeypatch.setattr(mod, "_start_frontend_port_forward", lambda **_: None)
    monkeypatch.setattr(mod, "_stop_trace_port_forward", lambda app: None)

    first = mod.deploy_live_environment(
        app_name="hotel_reservation",
        problem_id=None,
        deployment_name="first-live",
        deployments_root=str(tmp_path),
    )
    mod.undeploy_live_environment(first.deployment_name, deployments_root=str(tmp_path))
    second = mod.deploy_live_environment(
        app_name="hotel_reservation",
        problem_id=None,
        deployment_name="second-live",
        deployments_root=str(tmp_path),
    )

    assert len(create_calls) == 1
    assert second.cluster_name == first.cluster_name
    assert second.shared_cluster is True


def test_live_undeploy_delete_cluster_flag_removes_shared_cluster(tmp_path: Path, monkeypatch):
    mod = _import_bench_main()
    created_clusters: set[str] = set()
    deleted_clusters: list[str] = []
    app = _FakeApp()
    problem = _FakeProblem(app)

    def fake_create_kind_cluster(cluster_name: str, kubeconfig_path: str) -> None:
        created_clusters.add(cluster_name)
        path = Path(kubeconfig_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("apiVersion: v1\n", encoding="utf-8")

    def fake_existing_cluster_is_reusable(cluster_name: str, kubeconfig_path: str) -> tuple[bool, str]:
        if cluster_name in created_clusters and Path(kubeconfig_path).exists():
            return True, ""
        return False, "missing"

    def fake_delete_live_cluster(cluster_name: str) -> None:
        deleted_clusters.append(cluster_name)
        created_clusters.discard(cluster_name)

    monkeypatch.setattr(mod, "Conductor", lambda: _FakeConductor(app=app, problem=problem))
    monkeypatch.setattr(mod, "_create_kind_cluster", fake_create_kind_cluster)
    monkeypatch.setattr(mod, "_existing_cluster_is_reusable", fake_existing_cluster_is_reusable)
    monkeypatch.setattr(mod, "_delete_live_cluster", fake_delete_live_cluster)
    monkeypatch.setattr(mod, "_start_frontend_port_forward", lambda **_: None)
    monkeypatch.setattr(mod, "_stop_trace_port_forward", lambda app: None)

    state = mod.deploy_live_environment(
        app_name="hotel_reservation",
        problem_id=None,
        deployment_name="first-live",
        deployments_root=str(tmp_path),
    )
    result = mod.undeploy_live_environment(
        state.deployment_name,
        deployments_root=str(tmp_path),
        delete_cluster=True,
    )

    assert result.cluster_deleted is True
    assert deleted_clusters == [state.cluster_name]
    assert not Path(mod._shared_live_cluster_state_path(str(tmp_path))).exists()


class _FakeApp:
    def __init__(self) -> None:
        self.name = "Hotel Reservation"
        self.namespace = "hotel-reservation"
        self.frontend_service = "frontend"
        self.frontend_port = 5000
        self.cleanup_calls = 0

    def cleanup(self) -> None:
        self.cleanup_calls += 1


class _FakeProblem:
    def __init__(self, app: _FakeApp) -> None:
        self.app = app
        self._inject_calls = 0
        self._recover_calls = 0

    def requires_khaos(self) -> bool:
        return False

    def inject_fault(self) -> None:
        self._inject_calls += 1

    def recover_fault(self) -> None:
        self._recover_calls += 1


class _FakeClusterState:
    def __init__(self) -> None:
        self.baseline: Any = None
        self.reconcile_calls = 0

    def reconcile_to_baseline(self) -> dict[str, list[str]]:
        self.reconcile_calls += 1
        return {"namespaces_deleted": []}


class _FakeConductor:
    def __init__(self, *, app: _FakeApp, problem: _FakeProblem) -> None:
        self.apps = types.SimpleNamespace(get_app_instance=lambda name: app)
        self.problems = types.SimpleNamespace(get_problem_instance=lambda problem_id: problem)
        self.kubectl = types.SimpleNamespace(is_emulated_cluster=lambda: False)
        self.cluster_state = _FakeClusterState()
        self.problem_id: str | None = None
        self.problem: Any = None
        self.app: Any = None
        self.deploy_app_calls = 0
        self.undeploy_app_calls = 0
        self._baseline_captured = False
        self._app = app

    def deploy_app(self) -> None:
        self.deploy_app_calls += 1
        self.cluster_state.baseline = types.SimpleNamespace(
            namespaces={"default", "openebs"},
            cluster_roles={"cluster-role"},
            cluster_role_bindings={"cluster-role-binding"},
            persistent_volumes={"pv-a"},
            storage_classes={"openebs-device"},
            crds={"example.test"},
            node_labels={"node-1": {"beta.kubernetes.io/os": "linux"}},
            node_taints={"node-1": [{"key": "dedicated", "value": "live", "effect": "NoSchedule"}]},
            coredns_configmap_data={"Corefile": ".:53 {}"},
        )

    def undeploy_app(self) -> None:
        self.undeploy_app_calls += 1
        self._app.cleanup()
