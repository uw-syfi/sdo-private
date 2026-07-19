"""Launch SREGym incidents through the production long-running SDO controller."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app_operator.lifecycle import reuse_initial_lifecycle_if_valid, run_initial_lifecycle
from app_operator.sdo_sregym.runtime import RuntimeConfig, run_production_runtime
from libs.sregym_lib.conductor import get_api_base, get_app_info, poll_stage_sync, signal_cleanup
from libs.sregym_lib.schema import READY_STAGES

logger = logging.getLogger(__name__)


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


def _configuration() -> dict[str, Any]:
    raw = os.getenv("SREGYM_EXPERIMENT_AGENT_CONFIG", "").strip()
    if not raw:
        return {}
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("SREGYM_EXPERIMENT_AGENT_CONFIG must contain a JSON object")
    return decoded


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    config = _configuration()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=config.get("model", os.getenv("MODEL_ID", "gpt-5.4")))
    parser.add_argument("--timeout-sec", type=int, default=int(config.get("timeout_sec", 1800)))
    parser.add_argument("--controller-image", default=config.get("controller_image", "sdo-controller:v0.1.0"))
    parser.add_argument("--responder-image", default=config.get("responder_image", "sdo-responder:v0.1.0"))
    parser.add_argument(
        "--validator-image",
        default=config.get("validator_image", "sdo-observer-validator:v0.1.0"),
    )
    parser.add_argument("--repository-pvc", default=config.get("repository_pvc", "sdo-application-repository"))
    parser.add_argument("--credentials-secret", default=config.get("credentials_secret", "sdo-codex-credentials"))
    parser.add_argument("--logs-dir", default=None, help=argparse.SUPPRESS)
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
    host = "host.docker.internal"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme or "http", host, parsed.path.rstrip("/"), "", ""))


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.getenv("SREGYM_DEFER_CLEANUP", "").strip() != "1":
        raise RuntimeError("sdo_codex requires defer_cleanup: true in the SREGym agent registry")
    api_base = get_api_base()
    poll_stage_sync(api_base, wait_for=READY_STAGES, timeout=300, on_timeout="raise")
    app_info = get_app_info(api_base)
    repository = _application_repository()
    application = str(app_info.get("app_name") or repository.name)
    namespace = str(app_info.get("namespace") or "default")
    health_objective = (
        "All source-backed Deployments remain available, all selected Services have ready endpoints, "
        "and representative requests succeed."
    )
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
    receipt_dir = Path(args.logs_dir) if args.logs_dir else None
    if receipt_dir is not None:
        persist_production_receipt(receipt, receipt_dir)
    signal_cleanup(api_base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
