"""Narrow node-network relay for autonomous SREGym submissions.

Kind pods cannot route directly to Docker Desktop's host gateway, while Kind
nodes can. This relay is the only host-networked component and exposes only
the SREGym submission and status endpoints needed by the responder.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SUBMISSION_TIMEOUT_SECONDS = 300
HEALTH_TIMEOUT_SECONDS = 5
MAX_REQUEST_BYTES = 1024 * 1024
ALLOWED_PATHS = frozenset({"/submit"})


class RelayRequestError(ValueError):
    """Raised when a request falls outside the relay's narrow contract."""


Opener = Callable[..., Any]


@dataclass(frozen=True)
class RelayResponse:
    status: int
    body: bytes
    content_type: str


def _default_gateway(route_path: Path = Path("/proc/net/route")) -> str | None:
    """Return the default IPv4 gateway visible from the relay's network namespace."""

    try:
        lines = route_path.read_text(encoding="utf-8").splitlines()[1:]
    except (OSError, UnicodeError):
        return None
    for line in lines:
        fields = line.split()
        if len(fields) < 4 or fields[1] != "00000000":
            continue
        try:
            flags = int(fields[3], 16)
            gateway_bytes = bytes.fromhex(fields[2])
        except ValueError:
            continue
        if flags & 0x3 != 0x3 or len(gateway_bytes) != 4:
            continue
        return socket.inet_ntoa(gateway_bytes[::-1])
    return None


def _target_candidates(target_base: str, *, gateway: str | None) -> tuple[str, ...]:
    """Map a host-loopback target to safe, GET-probed host-network candidates."""

    parsed = urlsplit(target_base)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return (target_base.rstrip("/"),)

    def replace_host(host: str) -> str:
        rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        if parsed.port is not None:
            rendered_host = f"{rendered_host}:{parsed.port}"
        return urlunsplit((parsed.scheme or "http", rendered_host, parsed.path.rstrip("/"), "", ""))

    candidates = [replace_host("host.docker.internal")]
    if gateway:
        candidates.append(replace_host(gateway))
    return tuple(dict.fromkeys(candidates))


class TargetResolver:
    """Health-probe and cache exactly one upstream before any POST is forwarded."""

    def __init__(
        self,
        target_base: str,
        *,
        gateway: str | None = None,
        opener: Opener = urllib.request.urlopen,
    ) -> None:
        self._candidates = _target_candidates(
            target_base,
            gateway=gateway if gateway is not None else _default_gateway(),
        )
        self._opener = opener
        self._selected: str | None = None
        self._lock = threading.Lock()

    def resolve(self) -> str:
        if self._selected is not None:
            return self._selected
        with self._lock:
            if self._selected is not None:
                return self._selected
            for candidate in self._candidates:
                if _target_is_healthy(candidate, opener=self._opener):
                    self._selected = candidate
                    return candidate
        raise RelayRequestError("no reachable SREGym relay target")


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


def forward_status(
    *,
    target_base: str,
    opener: Opener = urllib.request.urlopen,
) -> RelayResponse:
    """Forward the single read-only status request used for stage polling."""

    request = urllib.request.Request(f"{target_base.rstrip('/')}/status", method="GET")
    try:
        response = opener(request, timeout=HEALTH_TIMEOUT_SECONDS)
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
    resolver = TargetResolver(target_base)

    class SubmissionRelayHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/healthz", "/status"}:
                self.send_error(404)
                return
            try:
                selected_target = resolver.resolve()
            except RelayRequestError:
                self.send_error(503, "SREGym target is unavailable")
                return
            if self.path == "/healthz":
                response = RelayResponse(200, b'{"status":"ok"}', "application/json")
            else:
                response = forward_status(target_base=selected_target)
            body = response.body
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
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
                selected_target = resolver.resolve()
                response = forward_request(self.path, body, target_base=selected_target)
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
