"""Narrow node-network relay for autonomous SREGym submissions.

Kind pods cannot route directly to Docker Desktop's host gateway, while Kind
nodes can.  This relay is the only host-networked component and exposes only
the three autonomous submission endpoints needed by the responder.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SUBMISSION_TIMEOUT_SECONDS = 300
HEALTH_TIMEOUT_SECONDS = 5
MAX_REQUEST_BYTES = 1024 * 1024
ALLOWED_PATHS = frozenset({"/submit_diagnosis", "/submit_mitigation", "/submit_done"})


class RelayRequestError(ValueError):
    """Raised when a request falls outside the relay's narrow contract."""


Opener = Callable[..., Any]


@dataclass(frozen=True)
class RelayResponse:
    status: int
    body: bytes
    content_type: str


def forward_request(
    path: str,
    body: bytes,
    *,
    target_base: str,
    opener: Opener = urllib.request.urlopen,
) -> RelayResponse:
    """Forward one explicitly allowed autonomous submission request."""

    if path not in ALLOWED_PATHS:
        raise RelayRequestError(f"relay path is not allowed: {path}")
    request = urllib.request.Request(
        f"{target_base.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        response = opener(request, timeout=SUBMISSION_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        status = response.status
        if status is None:
            raise RelayRequestError("relay target response has no HTTP status")
        return RelayResponse(
            status=int(status),
            body=response.read(),
            content_type=str(response.headers.get("Content-Type", "application/json")),
        )


def _target_is_healthy(
    target_base: str,
    *,
    opener: Opener = urllib.request.urlopen,
) -> bool:
    request = urllib.request.Request(f"{target_base.rstrip('/')}/status", method="GET")
    try:
        with opener(request, timeout=HEALTH_TIMEOUT_SECONDS) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError):
        return False


def _handler(target_base: str) -> type[BaseHTTPRequestHandler]:
    class SubmissionRelayHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/healthz":
                self.send_error(404)
                return
            if not _target_is_healthy(target_base):
                self.send_error(503, "SREGym target is unavailable")
                return
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400, "invalid Content-Length")
                return
            if content_length < 0 or content_length > MAX_REQUEST_BYTES:
                self.send_error(413, "submission payload is too large")
                return
            body = self.rfile.read(content_length)
            try:
                response = forward_request(self.path, body, target_base=target_base)
            except RelayRequestError as exc:
                self.send_error(404, str(exc))
                return
            except (OSError, urllib.error.URLError) as exc:
                self.send_error(502, str(exc))
                return
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

    return SubmissionRelayHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-port", type=int, default=18000)
    parser.add_argument("--target-base", required=True)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(("0.0.0.0", args.listen_port), _handler(args.target_base))
    print(json.dumps({"listen_port": args.listen_port, "target_base": args.target_base}), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
