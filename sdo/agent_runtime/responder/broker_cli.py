"""Production durable broker CLI with mandatory reflection.

The first reflection attempt resumes the responder session unless
``--reflection-session fresh`` opts into a fresh session from an incident brief.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

from sdo.agent_runtime.responder import (
    INCIDENT_REASONING_EFFORT,
    ClaudeSessionBackend,
    CodexSessionBackend,
    SessionReflector,
    prepare_claude_home,
    prepare_codex_home,
)
from sdo.operational_memory import (
    REFLECTION_GUIDANCE_MODES,
    REFLECTION_SESSION_MODES,
    BrokerClosure,
    BrokerService,
    BrokerServiceError,
    ClosureReceipt,
    CommandProposalValidator,
    CommitBroker,
    KubernetesJobSandboxRunner,
    LocalSandboxRunner,
    MemoryValidator,
    ReflectionGuidance,
)


def _production_reflector(
    *,
    provider: str = "codex",
    model: str | None,
    reasoning_effort: str,
    timeout_seconds: int,
    responder_turn_log: Path | None = None,
    guidance: ReflectionGuidance = "baseline",
) -> SessionReflector:
    backend_type = ClaudeSessionBackend if provider == "claude" else CodexSessionBackend
    return SessionReflector(
        backend_type(
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
        ),
        responder_turn_log=responder_turn_log,
        guidance=guidance,
    )


def _memory_validator(
    mode: str,
    *,
    namespace: str | None = None,
    image: str | None = None,
    repository_pvc: str | None = None,
    repository_mount_path: Path = Path("/workspace"),
) -> MemoryValidator:
    if mode == "local":
        return MemoryValidator(sandbox_runner=LocalSandboxRunner(timeout_seconds=300))
    if mode == "kubernetes":
        if not namespace or not image or not repository_pvc:
            raise ValueError("Kubernetes validator mode requires namespace, image, and repository PVC")
        return MemoryValidator(
            sandbox_runner=KubernetesJobSandboxRunner(
                namespace=namespace,
                image=image,
                repository_pvc=repository_pvc,
                repository_mount_path=repository_mount_path,
            )
        )
    return MemoryValidator()


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sdo-broker-service")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--worktree-root", type=Path, required=True)
    parser.add_argument("--proposal-command", action="append", default=[])
    parser.add_argument("--responder-backend", default="codex")
    parser.add_argument("--responder-model", default="unknown")
    parser.add_argument("--agent-provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--repair-policy", choices=("commit", "recorded-actions"), default="commit")
    parser.add_argument("--reflection-model")
    parser.add_argument("--reflection-reasoning-effort", default=INCIDENT_REASONING_EFFORT)
    parser.add_argument("--reflection-timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--reflection-session",
        choices=REFLECTION_SESSION_MODES,
        default="resume",
        help="first reflection attempt: resume the responder session (default) or start a fresh session "
        "from a compact incident brief",
    )
    parser.add_argument(
        "--reflection-guidance",
        choices=REFLECTION_GUIDANCE_MODES,
        default="baseline",
        help="reflection learning guidance: per-cause (default) or generalize one incident detector and playbook "
        "across parameter variants of the same root-cause class",
    )
    parser.add_argument(
        "--responder-turn-log",
        type=Path,
        help="responder per-turn usage log; a fresh reflection brief quotes the responder's shell commands from it",
    )
    parser.add_argument("--validator-mode", choices=("container", "kubernetes", "local"), default="container")
    parser.add_argument("--validator-namespace")
    parser.add_argument("--validator-image")
    parser.add_argument("--validator-repository-pvc")
    parser.add_argument("--validator-repository-mount-path", type=Path, default=Path("/workspace"))
    return parser


def main(argv: list[str] | None = None) -> int:
    prepare_codex_home()
    prepare_claude_home()
    args = _argument_parser().parse_args(argv)
    commands = [shlex.split(command) for command in args.proposal_command]
    broker = CommitBroker(
        args.repository,
        validator=_memory_validator(
            args.validator_mode,
            namespace=args.validator_namespace,
            image=args.validator_image,
            repository_pvc=args.validator_repository_pvc,
            repository_mount_path=args.validator_repository_mount_path,
        ),
        proposal_validator=CommandProposalValidator(commands) if commands else None,
    )
    service = BrokerService(
        args.repository,
        args.worktree_root,
        broker=broker,
        responder_backend=args.responder_backend,
        responder_model=args.responder_model,
        repair_policy=args.repair_policy,
        reflector=_production_reflector(
            provider=args.agent_provider,
            model=args.reflection_model,
            reasoning_effort=args.reflection_reasoning_effort,
            timeout_seconds=args.reflection_timeout_seconds,
            responder_turn_log=args.responder_turn_log,
            guidance=args.reflection_guidance,
        ),
        reflection_session=args.reflection_session,
    )
    try:
        payload = json.load(sys.stdin)
        operation = payload.get("operation")
        if operation == "prepare":
            workspace = service.prepare_incident(str(payload["incident_id"]))
            response = {
                "incident_id": workspace.incident_id,
                "worktree": str(workspace.path),
                "base_commit": workspace.base_commit,
            }
        elif operation == "process":
            response = service.process_closure(BrokerClosure.model_validate(payload["closure"])).model_dump()
        elif operation == "ack":
            service.acknowledge(ClosureReceipt.model_validate(payload["receipt"]))
            response = {"acknowledged": True}
        else:
            raise BrokerServiceError(f"unsupported operation {operation!r}")
    except (KeyError, OSError, ValueError, BrokerServiceError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
