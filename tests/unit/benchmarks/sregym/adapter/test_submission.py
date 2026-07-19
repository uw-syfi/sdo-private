from __future__ import annotations

import json
from typing import TYPE_CHECKING, Self

import pytest

from benchmarks.sregym.adapter.submission import SubmissionBridgeError, submit_solution

if TYPE_CHECKING:
    import urllib.request
    from types import TracebackType


class Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class FalseyOpener:
    def __init__(self) -> None:
        self.requests: list[urllib.request.Request] = []

    def __bool__(self) -> bool:
        return False

    def __call__(self, request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 300
        self.requests.append(request)
        return Response({"status": "acknowledged"})


def test_submission_bridge_records_diagnosis_through_autonomous_endpoint() -> None:
    requests: list[urllib.request.Request] = []

    def opener(request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 300
        requests.append(request)
        request_data = request.data
        assert isinstance(request_data, bytes)
        assert json.loads(request_data) == {"solution": "payment pods are fully isolated"}
        return Response({"status": "acknowledged", "message": "Diagnosis recorded."})

    result = submit_solution(
        "payment pods are fully isolated",
        phase="diagnosis",
        api_base="http://conductor:8123",
        opener=opener,
    )

    assert result == {"status": "acknowledged", "message": "Diagnosis recorded."}
    assert [request.full_url for request in requests] == ["http://conductor:8123/submit_diagnosis"]


def test_submission_bridge_records_mitigation_then_completes_autonomous_run() -> None:
    requests: list[urllib.request.Request] = []

    def opener(request: urllib.request.Request, timeout: int) -> Response:
        assert timeout == 300
        requests.append(request)
        if request.full_url.endswith("/submit_mitigation"):
            request_data = request.data
            assert isinstance(request_data, bytes)
            assert json.loads(request_data) == {"solution": "policy deleted"}
            return Response({"status": "acknowledged", "message": "Mitigation recorded."})
        assert request.full_url.endswith("/submit_done")
        assert request.data is None
        return Response({"status": "done", "num_diagnosis_submissions": 1})

    result = submit_solution(
        "policy deleted",
        phase="mitigation",
        api_base="http://conductor:8123",
        opener=opener,
    )

    assert result == {
        "mitigation": {"status": "acknowledged", "message": "Mitigation recorded."},
        "done": {"status": "done", "num_diagnosis_submissions": 1},
    }
    assert [request.full_url for request in requests] == [
        "http://conductor:8123/submit_mitigation",
        "http://conductor:8123/submit_done",
    ]


def test_submission_bridge_rejects_unacknowledged_autonomous_diagnosis() -> None:
    def opener(request: urllib.request.Request, timeout: int) -> Response:
        del request
        assert timeout == 300
        return Response({"status": "error"})

    with pytest.raises(SubmissionBridgeError, match="not acknowledged"):
        submit_solution("answer", phase="diagnosis", api_base="http://conductor:8123", opener=opener)


def test_submission_bridge_uses_a_falsey_injected_opener() -> None:
    opener = FalseyOpener()

    result = submit_solution("answer", phase="diagnosis", api_base="http://conductor:8123", opener=opener)

    assert result == {"status": "acknowledged"}
    assert [request.full_url for request in opener.requests] == ["http://conductor:8123/submit_diagnosis"]
