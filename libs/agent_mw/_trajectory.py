"""Middleware that appends a JSON trajectory record per agent run to a JSONL file."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from pydantic_ai.messages import ModelMessagesTypeAdapter

from libs.pydantic_agent import AgentMiddleware


class TrajectoryMiddleware(AgentMiddleware):
    """Appends one JSON record per agent run to a JSONL file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def after_run(self, result: Any, run_ctx: dict[str, Any] | None = None) -> None:
        u = result.usage()
        record = {
            "agent_name": self._agent.agent_name,
            "timestamp": datetime.now().isoformat(),
            "run_ctx": run_ctx,
            "messages": ModelMessagesTypeAdapter.dump_python(result.all_messages(), mode="json"),
            "usage": {
                "input_tokens": u.input_tokens or 0,
                "output_tokens": u.output_tokens or 0,
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")
