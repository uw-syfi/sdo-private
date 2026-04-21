"""HTTP/SSE MCP daemon for incident memory.

Uses FastMCP's SSE transport instead of a hand-written JSON-RPC server.
The only custom routing left is a thin compatibility layer that keeps the
existing ``/sse?store_only=1`` entrypoint and ``/health`` endpoint.
"""

from __future__ import annotations

import logging
import socket
import traceback
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs

import uvicorn
from fastmcp.server.http import create_sse_app
from starlette.responses import PlainTextResponse

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

    from .server import MemoryMCPServer

logger = logging.getLogger(__name__)


class _MemoryASGIApp:
    def __init__(self, mcp_server: MemoryMCPServer) -> None:
        self._full_app = create_sse_app(mcp_server.mcp, "/messages", "/sse")
        self._store_only_app = create_sse_app(mcp_server.store_only_mcp, "/messages-store-only", "/sse")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return

        if scope_type != "http":
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/health":
            await PlainTextResponse("ok")(scope, receive, send)
            return

        if path == "/sse":
            query_string = cast("bytes", scope.get("query_string", b""))
            query = parse_qs(query_string.decode())
            app = self._store_only_app if query.get("store_only", ["0"])[0] == "1" else self._full_app
            await app(scope, receive, send)
            return

        if path == "/messages":
            await self._full_app(self._with_path(scope, "/messages/"), receive, send)
            return

        if path == "/messages-store-only":
            await self._store_only_app(self._with_path(scope, "/messages-store-only/"), receive, send)
            return

        await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)

    @staticmethod
    def _with_path(scope: Scope, path: str) -> Scope:
        updated = dict(scope)
        updated["path"] = path
        return cast("Scope", updated)

    async def _handle_lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False
        await receive()
        try:
            async with self._full_app.router.lifespan_context(self._full_app):
                async with self._store_only_app.router.lifespan_context(self._store_only_app):
                    await send({"type": "lifespan.startup.complete"})
                    started = True
                    await receive()
        except BaseException:
            exc_text = traceback.format_exc()
            if started:
                await send({"type": "lifespan.shutdown.failed", "message": exc_text})
            else:
                await send({"type": "lifespan.startup.failed", "message": exc_text})
            raise
        else:
            await send({"type": "lifespan.shutdown.complete"})


class MemoryHTTPDaemon:
    """HTTP/SSE MCP server wrapping a MemoryMCPServer."""

    def __init__(self, mcp_server: MemoryMCPServer, host: str = "127.0.0.1", port: int = 0) -> None:
        self._app = _MemoryASGIApp(mcp_server)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, port))
        self._socket.listen(2048)

        addr = self._socket.getsockname()
        self.host = str(addr[0])
        self.port = int(addr[1])

        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

    def run(self) -> None:
        logger.info("Memory MCP HTTP daemon on %s:%d", self.host, self.port)
        try:
            self._server.run(sockets=[self._socket])
        finally:
            try:
                self._socket.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._server.should_exit = True
