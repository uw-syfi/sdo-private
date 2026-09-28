"""A scripted, LLM-free local-mode responder for the assurance suite.

The production controller runs this as its ``--dispatcher-mode local``
responder. It reads the incident request from stdin, publishes it in the
spool as ``<incident>.request.json``, and waits for the suite to write
``<incident>.result.json``, which it prints as the incident result. The suite
does the repair work (wrong fixes, the correct fix, helper objects) while this
process is the open responder, exactly where an agent would.

Run: ``python -m benchmarks.sregym.fastloop.assurance.responder --spool DIR``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

POLL_SECONDS = 0.1


def spool_name(incident_id: str) -> str:
    """A file-name-safe form of an incident id."""

    return "".join(character if character.isalnum() or character in "-_." else "_" for character in incident_id)


def request_path(spool: Path, incident_id: str) -> Path:
    return spool / f"{spool_name(incident_id)}.request.json"


def result_path(spool: Path, incident_id: str) -> Path:
    return spool / f"{spool_name(incident_id)}.result.json"


def exited_path(spool: Path, incident_id: str) -> Path:
    return spool / f"{spool_name(incident_id)}.exited"


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _deadline(request: dict[str, object]) -> float:
    raw = request.get("response_deadline")
    if isinstance(raw, str) and raw:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return time.monotonic() + max(0.0, (deadline - datetime.now(timezone.utc)).total_seconds())
    return time.monotonic() + 3600.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="assurance-responder")
    parser.add_argument("--spool", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = sys.stdin.read()
    request = json.loads(payload)
    incident_id = str(request["incident_id"])
    args.spool.mkdir(parents=True, exist_ok=True)
    _atomic_write(request_path(args.spool, incident_id), payload)
    deadline = _deadline(request)
    result = result_path(args.spool, incident_id)
    try:
        while not result.is_file():
            if time.monotonic() >= deadline:
                print(f"no scripted result for {incident_id} before the response deadline", file=sys.stderr)
                return 1
            time.sleep(POLL_SECONDS)
        sys.stdout.write(result.read_text(encoding="utf-8").strip() + "\n")
        sys.stdout.flush()
        return 0
    finally:
        _atomic_write(exited_path(args.spool, incident_id), datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    raise SystemExit(main())
