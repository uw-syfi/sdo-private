from __future__ import annotations

from pathlib import Path

import pytest

from app_operator.protocol.job_responder import run_job
from app_operator.protocol.models import IncidentRequest, IncidentResult

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "sdo" / "protocol"


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, IncidentResult]] = []

    def publish(self, name: str, result: IncidentResult) -> None:
        self.published.append((name, result))


def test_job_responder_executes_mounted_request_and_publishes_validated_result() -> None:
    request = IncidentRequest.model_validate_json((FIXTURE_DIR / "incident_request.json").read_text(encoding="utf-8"))
    result = IncidentResult.model_validate_json((FIXTURE_DIR / "incident_result.json").read_text(encoding="utf-8"))
    publisher = RecordingPublisher()

    returned = run_job(
        request_path=FIXTURE_DIR / "incident_request.json",
        result_name="incident-result",
        publisher=publisher,
        executor=lambda received: result if received == request else pytest.fail("request changed"),
    )

    assert returned == result
    assert publisher.published == [("incident-result", result)]


def test_job_responder_rejects_mismatched_result_before_publish() -> None:
    result = IncidentResult.model_validate_json((FIXTURE_DIR / "incident_result.json").read_text(encoding="utf-8"))
    bad_result = result.model_copy(update={"incident_id": "other"})
    publisher = RecordingPublisher()

    with pytest.raises(RuntimeError, match="incident_id"):
        run_job(
            request_path=FIXTURE_DIR / "incident_request.json",
            result_name="incident-result",
            publisher=publisher,
            executor=lambda _request: bad_result,
        )

    assert publisher.published == []
