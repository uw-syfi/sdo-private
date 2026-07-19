"""Launch SREGym incidents through the production long-running SDO controller."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from benchmarks.sregym.adapter.runtime import RuntimeConfig, run_production_runtime
from benchmarks.sregym.protocol.conductor import get_api_base, get_app_info, poll_stage_sync, signal_cleanup
from benchmarks.sregym.protocol.schema import READY_STAGES
from sdo.agent_runtime.lifecycle import reuse_initial_lifecycle_if_valid, run_initial_lifecycle

logger = logging.getLogger(__name__)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _string_object_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    mapping = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast("dict[str, object]", mapping)


def _cleanup_defer_timeout_seconds() -> float:
    raw = os.getenv("SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS", "600").strip()
    timeout = float(raw)
    if timeout <= 0:
        raise ValueError("SREGYM_CLEANUP_DEFER_TIMEOUT_SECONDS must be positive")
    return timeout


def persist_lifecycle_seed(repository: Path, logs_dir: Path) -> Path | None:
    """Checkpoint validated lifecycle memory outside a resettable stage directory."""

    stage_dir = next(
        (parent for parent in logs_dir.resolve().parents if re.fullmatch(r"stage_(\d+)_.+", parent.name)),
        None,
    )
    if stage_dir is None:
        return None
    stage_index = re.fullmatch(r"stage_(\d+)_.+", stage_dir.name)
    if stage_index is None:
        return None
    target = stage_dir.parent / f"lifecycle_seed_stage{stage_index.group(1)}"
    temporary = target.with_name(target.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(repository, temporary, symlinks=True)
    if target.exists():
        shutil.rmtree(target)
    temporary.replace(target)
    return target


def persist_production_receipt(receipt: dict[str, Any], receipt_dir: Path) -> Path:
    """Persist one strict receipt atomically beside the SREGym run artifacts."""
    receipt_dir.mkdir(parents=True, exist_ok=True)
    path = receipt_dir / "sdo_production_receipt_strict.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _receipt_directory(logs_dir: str | None, repository: Path) -> Path:
    """Resolve durable receipt storage for both CLI and registry-launched agents."""

    return Path(logs_dir) if logs_dir else repository.resolve().parent


def _configuration() -> dict[str, Any]:
    raw = os.getenv("SREGYM_EXPERIMENT_AGENT_CONFIG", "").strip()
    if not raw:
        return {}
    decoded: object = json.loads(raw)
    config = _string_object_mapping(decoded)
    if config is None:
        raise ValueError("SREGYM_EXPERIMENT_AGENT_CONFIG must contain a JSON object")
    return cast("dict[str, Any]", config)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    config = _configuration()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("agent-cli",), default=config.get("backend", "agent-cli"))
    parser.add_argument("--provider", choices=("codex",), default=config.get("provider", "codex"))
    parser.add_argument("--model", default=config.get("model", os.getenv("MODEL_ID", "gpt-5.4")))
    parser.add_argument("--timeout-sec", type=int, default=int(config.get("timeout_sec", 1800)))
    parser.add_argument("--controller-image", default=config.get("controller_image", "sdo-controller:v0.1.0"))
    parser.add_argument(
        "--responder-image",
        default=config.get("responder_image", "sdo-sregym-responder:v0.1.0"),
    )
    parser.add_argument(
        "--validator-image",
        default=config.get("validator_image", "sdo-detector-validator:v0.1.0"),
    )
    parser.add_argument("--repository-pvc", default=config.get("repository_pvc", "sdo-application-repository"))
    parser.add_argument("--credentials-secret", default=config.get("credentials_secret", "sdo-codex-credentials"))
    parser.add_argument(
        "--logs-dir",
        default=config.get("logs_dir", os.getenv("AGENT_LOGS_DIR")),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--summary-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--summary-model", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--enable-summary", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-inject-summary", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _application_repository() -> Path:
    raw = os.getenv("SREGYM_AGENT_WORKDIR", "").strip()
    if not raw:
        raise RuntimeError("SREGYM_AGENT_WORKDIR is required for the SDO production adapter")
    repository = Path(raw).resolve()
    if not repository.is_dir():
        raise RuntimeError(f"SREGYM_AGENT_WORKDIR is not a directory: {repository}")
    return repository


def _in_cluster_api_base(api_base: str) -> str:
    parsed = urlsplit(api_base)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return api_base.rstrip("/")
    return urlunsplit(("http", "sdo-sregym-bridge:8000", parsed.path.rstrip("/"), "", ""))


def _relay_target_api_base(api_base: str) -> str | None:
    parsed = urlsplit(api_base)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return None
    return api_base.rstrip("/")


def _deployed_health_objective(
    namespace: str,
    *,
    command_runner: CommandRunner = subprocess.run,
) -> str:
    """Bind the benchmark objective to the source variant deployed by SREGym."""

    command = [
        "kubectl",
        "--namespace",
        namespace,
        "get",
        "deployments,services",
        "--output=json",
    ]
    completed = command_runner(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"cannot inventory deployed SREGym resources: {details}")
    try:
        payload: object = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("kubectl returned an invalid deployed-resource inventory") from exc
    inventory = _string_object_mapping(payload)
    if inventory is None:
        raise RuntimeError("kubectl deployed-resource inventory is not a JSON object")
    items = inventory.get("items")
    if not isinstance(items, list):
        raise RuntimeError("kubectl deployed-resource inventory has no items list")
    inventory_items = cast("list[object]", items)

    def names(kind: str) -> list[str]:
        return sorted(
            {
                name
                for item in inventory_items
                if (item_document := _string_object_mapping(item)) is not None and item_document.get("kind") == kind
                for metadata_value in [item_document.get("metadata")]
                if (metadata := _string_object_mapping(metadata_value)) is not None
                for name in [metadata.get("name")]
                if isinstance(name, str) and name
            }
        )

    deployments = names("Deployment")
    services = names("Service")
    if not deployments:
        raise RuntimeError(f"no deployed Deployments found in SREGym namespace {namespace!r}")
    service_clause = (
        f"the deployed Services named {', '.join(services)} expose ready endpoints"
        if services
        else "the deployed workload remains reachable through its declared interfaces"
    )
    return (
        f"The deployed Deployments named {', '.join(deployments)} remain available; {service_clause}; "
        "required non-optional ConfigMap volume references remain present; and representative requests succeed."
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.getenv("SREGYM_DEFER_CLEANUP", "").strip() != "1":
        raise RuntimeError("sdo_codex requires defer_cleanup: true in the SREGym agent registry")
    api_base = get_api_base()
    logger.info("SDO agent backend: %s/%s", args.backend, args.provider)
    poll_stage_sync(api_base, wait_for=READY_STAGES, timeout=300, on_timeout="raise")
    app_info = get_app_info(api_base)
    repository = _application_repository()
    application = str(app_info.get("app_name") or repository.name)
    namespace = str(app_info.get("namespace") or "default")
    health_objective = _deployed_health_objective(namespace)
    if not reuse_initial_lifecycle_if_valid(
        repository,
        application=application,
        health_objective=health_objective,
    ):
        run_initial_lifecycle(
            repository,
            application=application,
            health_objective=health_objective,
        )
    if args.logs_dir:
        persist_lifecycle_seed(repository, Path(args.logs_dir))
    return run_production_runtime(
        RuntimeConfig(
            repository=repository,
            namespace=namespace,
            application=application,
            controller_image=args.controller_image,
            responder_image=args.responder_image,
            validator_image=args.validator_image,
            repository_pvc=args.repository_pvc,
            credentials_secret=args.credentials_secret,
            model=args.model,
            timeout_seconds=args.timeout_sec,
            submission_api_base=_in_cluster_api_base(api_base),
            submission_relay_target_base=_relay_target_api_base(api_base),
        )
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    api_base = get_api_base()
    logger.info("cleanup deferral watchdog configured for %.0fs", _cleanup_defer_timeout_seconds())
    try:
        receipt = _run(args)
    except Exception:
        logger.exception("production SDO incident lifecycle failed")
        return 1
    persist_production_receipt(receipt, _receipt_directory(args.logs_dir, _application_repository()))
    signal_cleanup(api_base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
