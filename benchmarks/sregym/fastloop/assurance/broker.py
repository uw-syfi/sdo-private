"""The production commit broker with reflection turned off, for LLM-free assurance runs.

It speaks the controller's broker protocol (``prepare``, ``process``, ``ack``
on stdin) through the production ``BrokerService`` and ``CommitBroker``, so
incident worktrees, the outcome record, diagnosis verification, commit
ownership checks and acknowledgement are all real. Only the reflector is
absent: ``BrokerService`` skips reflection when it has none, so no model is
ever called.

Run by the controller as ``python -m benchmarks.sregym.fastloop.assurance.broker``;
``run.go`` appends ``--repository``, ``--worktree-root`` and ``--repair-policy``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sdo.operational_memory import (
    BrokerClosure,
    BrokerService,
    BrokerServiceError,
    ClosureReceipt,
    CommitBroker,
    LocalSandboxRunner,
    MemoryValidator,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assurance-broker")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--worktree-root", type=Path, required=True)
    parser.add_argument("--repair-policy", choices=("commit", "recorded-actions"), default="recorded-actions")
    args = parser.parse_args(argv)
    broker = CommitBroker(
        args.repository, validator=MemoryValidator(sandbox_runner=LocalSandboxRunner(timeout_seconds=300))
    )
    service = BrokerService(
        args.repository,
        args.worktree_root,
        broker=broker,
        responder_backend="assurance-scripted",
        responder_model="none",
        repair_policy=args.repair_policy,
        reflector=None,
    )
    try:
        payload = json.load(sys.stdin)
        operation = payload.get("operation")
        if operation == "prepare":
            workspace = service.prepare_incident(str(payload["incident_id"]))
            response: dict[str, object] = {
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
    print(json.dumps(response, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
