"""Incident memory MCP server.

Production transports use ``FastMCP`` for both stdio and SSE. The small
``dispatch()`` / ``run()`` helpers remain as compatibility shims for the
existing unit tests and local direct-call usage.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import anyio
from fastmcp import FastMCP

from .store import IncidentCase, IncidentStore, compute_embedding

logger = logging.getLogger(__name__)

_DUPLICATE_THRESHOLD = 0.7
_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "incident_memory"
_SERVER_VERSION = "0.1.0"

_MERGE_SYSTEM = (
    "You are merging two incident reports for the same type of fault into one concise record. "
    "Keep the most informative content from each field. "
    "Be concise - prefer one to two sentences per field."
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
No explanation, no markdown fences - raw JSON only.\
"""


def _format_incident(case: IncidentCase) -> str:
    return (
        "Prior incident on a similar cluster (for reference only - your cluster may differ):\n"
        f"  App: {case.app}\n"
        f"  Symptoms: {case.symptoms}\n"
        f"  Key checks: {case.key_checks}\n"
        f"  Root causes: {case.root_causes}\n"
        f"  Fix: {case.fix}\n"
        f"  Lesson: {case.lesson}"
    )


def _llm_merge(existing: IncidentCase, new: dict[str, Any], model: str) -> dict[str, Any] | None:
    """Call the LLM to synthesize two incident records; returns merged fields or None on failure."""
    from libs.llm_rt import LiteLLMClient

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
        self._mcp = self._build_mcp(store_only=False)
        self._store_only_mcp = self._build_mcp(store_only=True)

    @property
    def mcp(self) -> FastMCP:
        return self._mcp

    @property
    def store_only_mcp(self) -> FastMCP:
        return self._store_only_mcp

    def _build_mcp(self, *, store_only: bool) -> FastMCP:
        mcp = FastMCP(_SERVER_NAME, version=_SERVER_VERSION)

        if not store_only:

            @mcp.tool(name="recall_incident")
            def recall_incident(query: str) -> str:  # pyright: ignore[reportUnusedFunction]
                """Retrieve the most similar past incident from memory.

                Args:
                    query: Current cluster symptoms or initial health-check output.
                """

                return self._recall(query)

        @mcp.tool(name="store_incident")
        def store_incident(  # pyright: ignore[reportUnusedFunction]
            app: str,
            symptoms: str,
            key_checks: str,
            root_causes: str,
            fix: str,
            lesson: str,
        ) -> str:
            """Record a resolved incident in memory so it can be recalled in future runs.

            Args:
                app: Application name (for example ``astronomy-shop``).
                symptoms: Observed symptoms that triggered the investigation.
                key_checks: Key kubectl or shell commands and their notable output.
                root_causes: Root cause or causes identified.
                fix: Actions taken to resolve the fault.
                lesson: Short heuristic for future investigations of similar symptoms.
            """

            return self._store_incident(
                {
                    "app": app,
                    "symptoms": symptoms,
                    "key_checks": key_checks,
                    "root_causes": root_causes,
                    "fix": fix,
                    "lesson": lesson,
                }
            )

        return mcp

    def run_stdio(self) -> None:
        """Run the production stdio MCP server via FastMCP."""
        self._mcp.run(transport="stdio", show_banner=False)

    def run(self) -> None:
        """Compatibility JSON-RPC loop used by the direct-dispatch unit tests."""
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc}"},
                    }
                )
                continue
            resp = self.dispatch(req)
            if resp is not None:
                self._write(resp)

    def _write(self, obj: Any) -> None:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    def _list_tools(self, *, store_only: bool = False) -> list[dict[str, Any]]:
        server = self._store_only_mcp if store_only else self._mcp

        async def _load_tools() -> Any:
            return await server.list_tools(run_middleware=False)

        tools = anyio.run(_load_tools)
        return [tool.to_mcp_tool(name=tool.name).model_dump(by_alias=True, exclude_none=True) for tool in tools]

    def dispatch(self, req: dict[str, Any], *, store_only: bool = False) -> dict[str, Any] | None:
        req_id = req.get("id")
        method = req.get("method", "")
        params: dict[str, Any] = req.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
                },
            }
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self._list_tools(store_only=store_only)}}
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
