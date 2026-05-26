"""Read-only ``recall`` MCP server for cli_agent memory.

Exposes a single tool, ``recall(situation)``, that filters the per-app lesson
store and returns the matching set framed as *hypotheses to verify*, never as
answers (§5). Retrieval is deliberately high-recall / low-precision: return
all lessons for the app and let the agent reason. There is no write tool —
the store is written out-of-band by the driver.

The server runs **in-process** in the driver via a background uvicorn thread
on an ephemeral port; the wrapped CLI subprocess reaches it over HTTP/SSE.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.http import create_sse_app

if TYPE_CHECKING:
    from .store import Lesson, LessonStore

logger = logging.getLogger(__name__)

_FRAMING = (
    "Past incidents on this app that may relate to what you're seeing. Treat each "
    "as a HYPOTHESIS to verify against the live cluster, not as the answer — the "
    "current fault may differ, and competing causes can share these symptoms.\n\n"
)

_NO_MATCH = "No past incidents recorded for this app yet. Investigate from first principles."


def _format_lessons(lessons: list[Lesson]) -> str:
    if not lessons:
        return _NO_MATCH
    blocks: list[str] = []
    for lesson in lessons:
        block = (
            f"- root_cause: {lesson.root_cause}\n"
            f"  situation: {lesson.situation}\n"
            f"  tell: {lesson.tell}\n"
            f"  fix: {lesson.fix}\n"
            f"  affected_resource: {lesson.affected_resource}\n"
            f"  (confirmed_by={lesson.confirmed_by}, seen {lesson.seen_count}x)"
        )
        if lesson.obvious_guess:
            block += f"\n  beware the tempting wrong guess: {lesson.obvious_guess}"
        blocks.append(block)
    return _FRAMING + "\n".join(blocks)


def build_recall_mcp(store: LessonStore, app: str) -> FastMCP:
    """Build the FastMCP server exposing ``recall`` for a single app's store."""
    mcp = FastMCP("cli_agent_memory")

    @mcp.tool(name="recall")
    def recall(situation: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Recall lessons from past incidents on this app that may relate to what
        you are currently seeing. Returns prior root causes / tells / fixes as
        hypotheses to verify against the live cluster — not as answers.

        Args:
            situation: What you are currently observing (symptoms, failing
                resources, and any recent change such as a rollout or deploy).
        """
        lessons = store.load(app)
        logger.info("recall(app=%s): returning %d lesson(s)", app, len(lessons))
        return _format_lessons(lessons)

    return mcp


class RecallServer:
    """Runs :func:`build_recall_mcp` over HTTP/SSE in a background thread.

    Binds an ephemeral port up front so ``url`` is available before the server
    finishes starting. Call :meth:`stop` to shut it down.
    """

    def __init__(self, store: LessonStore, app: str, *, host: str = "127.0.0.1") -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, 0))
        addr = self._sock.getsockname()
        self.host = str(addr[0])
        self.port = int(addr[1])

        app_asgi = create_sse_app(build_recall_mcp(store, app), "/messages/", "/sse")
        config = uvicorn.Config(app_asgi, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._serve, name="recall-server", daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/sse"

    def _serve(self) -> None:
        try:
            self._server.run(sockets=[self._sock])
        finally:
            try:
                self._sock.close()
            except OSError:
                pass

    def start(self, *, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return
            time.sleep(0.02)
        raise RuntimeError(f"RecallServer failed to start within {timeout}s")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
