"""HTTP/SSE MCP daemon for incident memory.

Implements the MCP SSE transport (2024-11-05):
- GET /sse          → opens a per-client SSE stream; sends endpoint URL
- GET /health       → liveness probe
- POST /messages    → receives JSON-RPC 2.0 request; delivers response via SSE
"""

from __future__ import annotations

import http.server
import json
import logging
import queue as queue_module
import uuid
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .server import MemoryMCPServer

logger = logging.getLogger(__name__)

_KEEPALIVE_TIMEOUT_S = 25.0


class _Session:
    def __init__(self, store_only: bool = False) -> None:
        self.id: str = str(uuid.uuid4())
        self.queue: queue_module.Queue[dict[str, Any] | None] = queue_module.Queue()
        self.store_only: bool = store_only


class _MCPHandler(http.server.BaseHTTPRequestHandler):
    # HTTP/1.1 is required so urllib3/requests reads SSE chunks as they arrive
    # rather than buffering the entire body until connection close.
    protocol_version = "HTTP/1.1"

    @property
    def _server(self) -> _MCPHTTPServer:
        return cast("_MCPHTTPServer", self.server)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/sse":
            self._handle_sse()
        elif parsed.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path.startswith("/messages"):
            self._handle_message()
        else:
            self.send_error(404)

    def _send_chunk(self, data: bytes) -> None:
        """Write one HTTP/1.1 chunked-encoding frame and flush."""
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _handle_sse(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        store_only = qs.get("store_only", ["0"])[0] == "1"
        session = _Session(store_only=store_only)
        self._server.sessions[session.id] = session
        logger.debug("SSE session opened: %s", session.id)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            endpoint = f"/messages?sessionId={session.id}"
            self._send_chunk(f"event: endpoint\ndata: {endpoint}\n\n".encode())

            while True:
                try:
                    msg = session.queue.get(timeout=_KEEPALIVE_TIMEOUT_S)
                    if msg is None:
                        break
                    self._send_chunk(f"event: message\ndata: {json.dumps(msg)}\n\n".encode())
                except queue_module.Empty:
                    self._send_chunk(b": keepalive\n\n")
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("SSE session %s disconnected", session.id)
        finally:
            self._server.sessions.pop(session.id, None)

    def _handle_message(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        session_id = (params.get("sessionId") or [None])[0]
        session = self._server.sessions.get(session_id) if session_id else None

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        resp = self._server.mcp_server.dispatch(req, store_only=session.store_only if session else False)

        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

        if resp is not None and session is not None:
            session.queue.put(resp)


class _MCPHTTPServer(http.server.ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], mcp_server: MemoryMCPServer) -> None:
        self.mcp_server = mcp_server
        self.sessions: dict[str, _Session] = {}
        super().__init__(address, _MCPHandler)


class MemoryHTTPDaemon:
    """HTTP/SSE MCP server wrapping a MemoryMCPServer.

    Bind on construction (port 0 → OS-assigned); call run() to block.
    """

    def __init__(self, mcp_server: MemoryMCPServer, host: str = "127.0.0.1", port: int = 0) -> None:
        self._httpd = _MCPHTTPServer((host, port), mcp_server)
        # server_address can be AF_INET6 4-tuple; we only use host/port
        addr = self._httpd.server_address
        self.host = str(addr[0])
        self.port = int(addr[1])

    def run(self) -> None:
        logger.info("Memory MCP HTTP daemon on %s:%d", self.host, self.port)
        try:
            self._httpd.serve_forever()
        finally:
            self._httpd.server_close()

    def stop(self) -> None:
        self._httpd.shutdown()
