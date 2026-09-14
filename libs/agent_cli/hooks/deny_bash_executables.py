#!/usr/bin/env python3
"""Deny direct invocation of selected executables in Claude Bash tool calls."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


def main() -> int:
    denied = {name for name in sys.argv[1:] if name}
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    tool_input: dict[str, Any] = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str):
        return 0
    invoked = next(
        (
            executable
            for executable in sorted(denied)
            if re.search(rf"(?:^|[;&|()\s]){re.escape(executable)}(?:\s|$)", command)
        ),
        None,
    )
    if invoked is None:
        return 0
    decision = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Direct {invoked} execution is disabled in this authoring session. "
                "Use the controller-provided validation gateway instead."
            ),
        }
    }
    json.dump(decision, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
