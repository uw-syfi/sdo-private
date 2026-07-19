"""Narrow SREGym submission transport for an in-cluster responder Job.

This module forwards responder-authored answers to the conductor. It neither
launches the responder nor converts benchmark grading into health state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Literal


class SubmissionBridgeError(RuntimeError):
    """Raised when the benchmark is not ready to receive a submission."""


SUBMISSION_TIMEOUT_SECONDS = 300


Opener = Callable[..., Any]


def submit_solution(
    solution: str,
    *,
    phase: Literal["diagnosis", "mitigation"],
    api_base: str,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, Any]:
    base = api_base.rstrip("/")
    payload = json.dumps({"solution": solution}).encode()
    phase_request = urllib.request.Request(
        f"{base}/submit_{phase}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(phase_request, timeout=SUBMISSION_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if phase != "mitigation" or exc.code != 409:
            raise
        # A lost submit_done response can make a retry observe the already-done
        # guard.  submit_done itself is idempotent, so continue to it and recover
        # the cached completion payload instead of duplicating mitigation.
        result = {"status": "already_done"}
    if not isinstance(result, dict):
        raise SubmissionBridgeError("SREGym submission response is not a JSON object")
    if phase == "diagnosis":
        if result.get("status") != "acknowledged":
            raise SubmissionBridgeError("SREGym diagnosis was not acknowledged")
        return result
    if result.get("status") not in {"acknowledged", "already_done"}:
        raise SubmissionBridgeError("SREGym mitigation was not acknowledged")

    done_request = urllib.request.Request(f"{base}/submit_done", method="POST")
    with opener(done_request, timeout=SUBMISSION_TIMEOUT_SECONDS) as response:
        done = json.loads(response.read())
    if not isinstance(done, dict) or done.get("status") != "done":
        raise SubmissionBridgeError("SREGym autonomous run did not report done")
    return {"mitigation": result, "done": done}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("diagnosis", "mitigation"))
    parser.add_argument("solution")
    args = parser.parse_args(argv)
    api_base = os.getenv("SDO_SREGYM_API_BASE", "").strip()
    if not api_base:
        print("SDO_SREGYM_API_BASE is required", file=sys.stderr)
        return 2
    try:
        result = submit_solution(args.solution, phase=args.phase, api_base=api_base)
    except (OSError, ValueError, SubmissionBridgeError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
