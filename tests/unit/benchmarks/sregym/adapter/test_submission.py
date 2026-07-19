from __future__ import annotations

import json

import pytest

from benchmarks.sregym.adapter.submission import SubmissionBridgeError, submit_solution


class Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_submission_bridge_records_diagnosis_through_autonomous_endpoint() -> None:
    requests = []

    def opener(request, timeout: int):
        assert timeout == 300
        requests.append(request)
        assert json.loads(request.data) == {"solution": "payment pods are fully isolated"}
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
    requests = []

    def opener(request, timeout: int):
        assert timeout == 300
        requests.append(request)
        if request.full_url.endswith("/submit_mitigation"):
            assert json.loads(request.data) == {"solution": "policy deleted"}
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
    def opener(_request, timeout: int):
        assert timeout == 300
        return Response({"status": "error"})

    with pytest.raises(SubmissionBridgeError, match="not acknowledged"):
        submit_solution("answer", phase="diagnosis", api_base="http://conductor:8123", opener=opener)
