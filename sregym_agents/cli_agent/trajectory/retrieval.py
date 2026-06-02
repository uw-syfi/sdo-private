"""Retrieval over recorded trajectories — two server-side backends.

Exposes ``search_trajectories`` + ``read_trajectory`` over HTTP/SSE via
:class:`TrajectoryRetrievalServer`, mirroring
``cli_agent.memory.recall_server.RecallServer``. Both retrieval modes are MCP
tools (so every benchmark-agent call is logged server-side and verifiable):

- ``mode="rag"`` — embedding similarity top-k (see ``embedding.py``).
- ``mode="llm"`` — an LLM reads the candidate runs and synthesises the relevant
  ones (see ``llm_search.py``; an ``agentshim`` ``call_subagent`` by default).
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

from .embedding import cosine_top_k, default_embedder
from .llm_search import default_searcher

if TYPE_CHECKING:
    from .embedding import Embedder
    from .llm_search import LLMSearcher
    from .store import TrajectoryDigest, TrajectoryStore

logger = logging.getLogger(__name__)

_FRAMING = (
    "Past investigations recorded on this app. Treat each as a HYPOTHESIS and a "
    "worked example to learn from — the current fault may differ. Use "
    "read_trajectory(path) to see the full transcript of any run that looks "
    "relevant.\n\n"
)

_NO_MATCH = "No past trajectories recorded for this app yet. Investigate from first principles."


def _format_digests(digests: list[TrajectoryDigest], *, scores: list[float] | None = None) -> str:
    if not digests:
        return _NO_MATCH
    blocks: list[str] = []
    for i, d in enumerate(digests):
        header = f"- run: {d.run_id}"
        if scores is not None:
            header += f"  (similarity={scores[i]:.3f})"
        block = (
            f"{header}\n"
            f"  outcome: {d.outcome or 'unknown'}\n"
            f"  tools: {', '.join(d.tool_calls) or 'none'}\n"
            f"  summary: {d.text}\n"
            f"  path: {d.path}"
        )
        blocks.append(block)
    return _FRAMING + "\n\n".join(blocks)


def build_trajectory_mcp(
    store: TrajectoryStore,
    app: str,
    *,
    mode: str = "rag",
    embedder: Embedder | None = None,
    searcher: LLMSearcher | None = None,
    exclude_path: str | None = None,
) -> FastMCP:
    """Build the FastMCP server exposing trajectory retrieval for one app.

    ``mode`` picks the retrieval mechanism behind ``search_trajectories``:

    - ``"rag"`` — embedding similarity top-k (``embedder``).
    - ``"llm"`` — an LLM reads the candidate runs and synthesises the relevant
      ones (``searcher``; an ``agentshim`` ``call_subagent`` by default).

    Both also expose ``read_trajectory`` for the full transcript. ``exclude_path``
    hides one trajectory file so the agent never retrieves its own in-flight run.
    """
    mcp = FastMCP("cli_agent_trajectory")
    _exclude = {exclude_path} if exclude_path else None

    if mode == "llm":
        _searcher = searcher or default_searcher()

        @mcp.tool(name="search_trajectories")
        def search_trajectories(query: str) -> str:  # pyright: ignore[reportUnusedFunction]
            """Search past investigations on this application for ones relevant to
            what you are seeing. An LLM reads the prior runs and returns the most
            relevant findings (root causes, discriminating signals) as hypotheses
            to verify; call read_trajectory(path) to read a full transcript.

            Args:
                query: What you are currently observing (symptoms, failing
                    resources, and any recent change such as a rollout or deploy).
            """
            digests = store.digests(app, exclude=_exclude)
            if not digests:
                return _NO_MATCH
            logger.info("search_trajectories(llm, app=%s): %d candidate(s)", app, len(digests))
            try:
                return _FRAMING + _searcher(query, digests, store)
            except Exception:
                # The search sub-agent's LLM call failed (creds/model/network).
                # Don't break the run — fall back to the raw candidate digests so
                # the agent still gets the prior runs to reason over.
                logger.exception("search_trajectories(llm): searcher failed; returning candidates")
                return _format_digests(digests)
    else:
        _embedder = embedder or default_embedder()

        @mcp.tool(name="search_trajectories")
        def search_trajectories(query: str, k: int = 5) -> str:  # pyright: ignore[reportUnusedFunction]
            """Retrieve the past investigations most similar to ``query`` by
            embedding similarity (RAG). Returns up to ``k`` run digests; call
            read_trajectory(path) on any that look relevant.

            Args:
                query: What you are currently seeing (symptoms, failing
                    resources, recent changes).
                k: Max number of similar past runs to return.
            """
            digests = store.digests(app, exclude=_exclude)
            if not digests:
                return _NO_MATCH
            matrix = _embedder.embed([d.text for d in digests])
            qvec = _embedder.embed([query])[0]
            ranked = cosine_top_k(qvec, matrix, k)
            picked = [digests[i] for i, _ in ranked]
            scores = [s for _, s in ranked]
            logger.info("search_trajectories(rag, app=%s, k=%d): %d hit(s)", app, k, len(picked))
            return _format_digests(picked, scores=scores)

    @mcp.tool(name="read_trajectory")
    def read_trajectory(path: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """Read the full transcript of a past run, given the ``path`` from a
        search_trajectories result.

        Args:
            path: The ``path`` field of a digest returned by search_trajectories.
        """
        text = store.read_full(path)
        return text or f"No trajectory found at {path}."

    return mcp


class TrajectoryRetrievalServer:
    """Runs :func:`build_trajectory_mcp` over HTTP/SSE in a background thread.

    Mirrors ``cli_agent.memory.recall_server.RecallServer``: binds an ephemeral
    port up front so ``url`` is available before the server finishes starting.
    """

    def __init__(
        self,
        store: TrajectoryStore,
        app: str,
        *,
        mode: str = "rag",
        embedder: Embedder | None = None,
        searcher: LLMSearcher | None = None,
        exclude_path: str | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, 0))
        addr = self._sock.getsockname()
        self.host = str(addr[0])
        self.port = int(addr[1])

        mcp = build_trajectory_mcp(
            store, app, mode=mode, embedder=embedder, searcher=searcher, exclude_path=exclude_path
        )
        app_asgi = create_sse_app(mcp, "/messages/", "/sse")
        config = uvicorn.Config(app_asgi, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._serve, name="trajectory-server", daemon=True)

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
        raise RuntimeError(f"TrajectoryRetrievalServer failed to start within {timeout}s")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)
