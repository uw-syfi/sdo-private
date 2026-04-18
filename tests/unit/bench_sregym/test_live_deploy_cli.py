from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"
MAIN_PATH = BENCH_ROOT / "main.py"


def _module(name: str) -> Any:
    return cast(Any, sys.modules.setdefault(name, types.ModuleType(name)))


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
    constants_mod.StartProblemResult = types.SimpleNamespace(SUCCESS="success", SKIPPED_KHAOS_REQUIRED="skipped")
    kubeconfig_mod = _module("sregym.service.kubeconfig")
    kubeconfig_mod.require_kubeconfig_path = lambda: "/tmp/kubeconfig"

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
