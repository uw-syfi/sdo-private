# pyright: reportPrivateUsage=false
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import pytest

from benchmarks.sregym.adapter.submission_relay import (
    RelayRequestError,
    TargetResolver,
    _default_gateway,
    _target_candidates,
    forward_request,
)

if TYPE_CHECKING:
    from pathlib import Path


class FakeResponse:
    def __init__(self, payload: dict[str, object], *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_relay_forwards_only_autonomous_submission_endpoints() -> None:
    calls: list[tuple[urllib.request.Request, int]] = []

    def opener(request: urllib.request.Request, timeout: int) -> FakeResponse:
        calls.append((request, timeout))
        return FakeResponse({"status": "acknowledged"})

    response = forward_request(
        "/submit_diagnosis",
        b'{"solution":"network policy blocks frontend"}',
        target_base="http://host.docker.internal:8000",
        opener=opener,
    )

    request, timeout = calls[0]
    assert request.full_url == "http://host.docker.internal:8000/submit_diagnosis"
    assert request.get_method() == "POST"
    assert request.data == b'{"solution":"network policy blocks frontend"}'
    assert timeout == 300
    assert response.status == 200
    assert json.loads(response.body) == {"status": "acknowledged"}


@pytest.mark.parametrize("path", ["/submit", "/cleanup", "/anything"])
def test_relay_rejects_non_autonomous_paths(path: str) -> None:
    with pytest.raises(RelayRequestError, match="not allowed"):
        forward_request(path, b"{}", target_base="http://host.docker.internal:8000")


def test_default_gateway_reads_linux_route_table(tmp_path: Path) -> None:
    routes = tmp_path / "route"
    routes.write_text(
        "Iface\tDestination\tGateway\tFlags\neth0\t00000000\t010013AC\t0003\n",
        encoding="utf-8",
    )

    assert _default_gateway(routes) == "172.19.0.1"


def test_local_target_candidates_include_desktop_and_linux_gateways() -> None:
    assert _target_candidates("http://localhost:8123/api", gateway="172.19.0.1") == (
        "http://host.docker.internal:8123/api",
        "http://172.19.0.1:8123/api",
    )


def test_target_resolver_probes_with_get_then_caches_one_post_target() -> None:
    calls: list[tuple[str, str]] = []

    def opener(request: urllib.request.Request, timeout: int) -> FakeResponse:
        del timeout
        calls.append((request.get_method(), request.full_url))
        if request.full_url.startswith("http://host.docker.internal"):
            raise urllib.error.URLError("unresolvable")
        return FakeResponse({"status": "ok"})

    resolver = TargetResolver(
        "http://localhost:8000",
        gateway="172.19.0.1",
        opener=opener,
    )

    selected = resolver.resolve()
    response = forward_request("/submit_done", b"{}", target_base=resolver.resolve(), opener=opener)

    assert selected == "http://172.19.0.1:8000"
    assert calls == [
        ("GET", "http://host.docker.internal:8000/status"),
        ("GET", "http://172.19.0.1:8000/status"),
        ("POST", "http://172.19.0.1:8000/submit_done"),
    ]
    assert response.status == 200
