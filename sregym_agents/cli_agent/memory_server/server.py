"""Minimal stdio MCP server (JSON-RPC 2.0) for incident memory.

Exposes two tools:
- ``recall_incident(query)``  — retrieve the closest past incident
- ``store_incident(...)``     — save a resolved incident; merges with duplicate if found

Designed to run as a subprocess launched by the cli_agent driver via
``StdioMcpServer``.  The LLM merge path (``--merge-model``) is the only
part that requires external dependencies (litellm via libs.agent_cli).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from .store import IncidentCase, IncidentStore, compute_embedding

logger = logging.getLogger(__name__)

_DUPLICATE_THRESHOLD = 0.7

_MERGE_SYSTEM = (
    "You are merging two incident reports for the same type of fault into one concise record. "
    "Keep the most informative content from each field. "
    "Be concise — prefer one to two sentences per field."
)

_MERGE_USER = """\
Existing incident:
  key_checks: {existing_key_checks}
  root_causes: {existing_root_causes}
  fix: {existing_fix}
  lesson: {existing_lesson}

New incident:
  key_checks: {new_key_checks}
  root_causes: {new_root_causes}
  fix: {new_fix}
  lesson: {new_lesson}

Reply with a JSON object with exactly these keys: key_checks, root_causes, fix, lesson.
No explanation, no markdown fences — raw JSON only.\
"""

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "recall_incident",
        "description": (
            "Retrieve the most similar past incident from memory. "
            "Pass a brief description of current symptoms or the output of an initial "
            "health check (`kubectl get pods`, `kubectl get events`). "
            "Returns a formatted prior incident for reference, or a message if none found. "
            "The retrieved case is reference material only — your cluster may differ."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Current cluster symptoms or initial health-check output.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "store_incident",
        "description": "Record a resolved incident in memory so it can be recalled in future runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": "Application name (e.g. astronomy-shop).",
                },
                "symptoms": {
                    "type": "string",
                    "description": "Observed symptoms that triggered the investigation.",
                },
                "key_checks": {
                    "type": "string",
                    "description": "Key kubectl/bash commands run and their notable output.",
                },
                "root_causes": {
                    "type": "string",
                    "description": "Root cause(s) identified.",
                },
                "fix": {
                    "type": "string",
                    "description": "Actions taken to resolve the fault.",
                },
                "lesson": {
                    "type": "string",
                    "description": "One-line heuristic for future investigations of similar symptoms.",
                },
            },
            "required": ["app", "symptoms", "key_checks", "root_causes", "fix", "lesson"],
        },
    },
]


def _format_incident(case: IncidentCase) -> str:
    return (
        "Prior incident on a similar cluster (for reference only — your cluster may differ):\n"
        f"  App: {case.app}\n"
        f"  Symptoms: {case.symptoms}\n"
        f"  Key checks: {case.key_checks}\n"
        f"  Root causes: {case.root_causes}\n"
        f"  Fix: {case.fix}\n"
        f"  Lesson: {case.lesson}"
    )


def _llm_merge(existing: IncidentCase, new: dict[str, Any], model: str) -> dict[str, Any] | None:
    """Call the LLM to synthesize two incident records; returns merged fields or None on failure."""
    from libs.agent_cli.llm_client import LiteLLMClient

    client = LiteLLMClient(model=model)
    prompt = _MERGE_USER.format(
        existing_key_checks=existing.key_checks,
        existing_root_causes=existing.root_causes,
        existing_fix=existing.fix,
        existing_lesson=existing.lesson,
        new_key_checks=new.get("key_checks", ""),
        new_root_causes=new.get("root_causes", ""),
        new_fix=new.get("fix", ""),
        new_lesson=new.get("lesson", ""),
    )
    try:
        raw = client.complete(
            [
                {"role": "system", "content": _MERGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            label="incident merge",
        )
        merged = json.loads(raw)
        required = {"key_checks", "root_causes", "fix", "lesson"}
        if not required.issubset(merged.keys()):
            logger.warning("LLM merge response missing keys: %s", merged)
            return None
        return merged
    except Exception as exc:
        logger.warning("LLM merge failed (%s); keeping existing record", exc)
        return None


class MemoryMCPServer:
    def __init__(self, store: IncidentStore, merge_model: str | None = None) -> None:
        self._store = store
        self._merge_model = merge_model

    def run(self) -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}})
                continue
            resp = self.dispatch(req)
            if resp is not None:
                self._write(resp)

    def _write(self, obj: Any) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def dispatch(self, req: dict[str, Any], *, store_only: bool = False) -> dict[str, Any] | None:
        req_id = req.get("id")
        method = req.get("method", "")
        params: dict[str, Any] = req.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "incident_memory", "version": "0.1.0"},
                },
            }
        if method in ("notifications/initialized", "initialized"):
            return None  # notification — no response expected
        if method == "tools/list":
            tools = [t for t in _TOOLS if not store_only or t["name"] != "recall_incident"]
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        if method == "tools/call":
            return self._call_tool(req_id, params, store_only=store_only)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    def _call_tool(self, req_id: Any, params: dict[str, Any], *, store_only: bool = False) -> dict[str, Any]:
        tool_name = params.get("name", "")
        if store_only and tool_name == "recall_incident":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "recall_incident is not available in store-only mode"},
            }
        args: dict[str, Any] = params.get("arguments") or {}
        try:
            if tool_name == "recall_incident":
                text = self._recall(args.get("query", ""))
            elif tool_name == "store_incident":
                text = self._store_incident(args)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
                }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(exc)},
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }

    def _recall(self, query: str) -> str:
        case = self._store.retrieve(query)
        if case is None:
            return "No similar past incident found in memory."
        return _format_incident(case)

    def _store_incident(self, args: dict[str, Any]) -> str:
        app = args.get("app", "")
        symptoms = args.get("symptoms", "")
        key_checks = args.get("key_checks", "")
        root_causes = args.get("root_causes", "")
        fix = args.get("fix", "")
        lesson = args.get("lesson", "")

        embedding = compute_embedding(app, symptoms, key_checks, root_causes)
        existing = self._store.find_duplicate(embedding, threshold=_DUPLICATE_THRESHOLD)

        if existing is not None:
            merged = _llm_merge(existing, args, self._merge_model) if self._merge_model else None
            if merged is not None:
                self._store.update(
                    existing.id,
                    app=existing.app,
                    symptoms=existing.symptoms,
                    key_checks=merged["key_checks"],
                    root_causes=merged["root_causes"],
                    fix=merged["fix"],
                    lesson=merged["lesson"],
                )
                return f"Merged with existing incident (id={existing.id})."
            # No merge model or merge failed — keep existing record as-is
            return f"Similar incident already in memory (id={existing.id}); skipped."

        incident_id = self._store.store(
            app=app,
            symptoms=symptoms,
            key_checks=key_checks,
            root_causes=root_causes,
            fix=fix,
            lesson=lesson,
        )
        return f"Incident stored (id={incident_id})."
