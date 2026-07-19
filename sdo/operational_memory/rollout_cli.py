"""Narrow process boundary for SDO controller rollout evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from sdo.operational_memory.broker_service import BrokerService, BrokerServiceError, ControllerRolloutRecord


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pending", "record"))
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--worktree-root", type=Path, required=True)
    parser.add_argument("--reflection-commit")
    args = parser.parse_args()
    try:
        service = BrokerService(args.repository, args.worktree_root)
        if args.action == "pending":
            if not args.reflection_commit:
                parser.error("pending requires --reflection-commit")
            expectation = service.pending_controller_rollout(args.reflection_commit)
            print("null" if expectation is None else expectation.model_dump_json())
        else:
            record = ControllerRolloutRecord.model_validate_json(sys.stdin.read())
            service.record_controller_rollout(record)
    except (BrokerServiceError, ValidationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
