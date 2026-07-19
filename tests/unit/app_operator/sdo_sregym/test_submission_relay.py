from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from app_operator.sdo_sregym.submission_relay import (
    RelayRequestError,
    forward_request,
)

if TYPE_CHECKING:
    import urllib.request


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
