"""Contract tests for ``sdo incident status``, the responder's verify-before-submit check."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from sdo.__main__ import main
from sdo.agent_runtime.responder.incident_status import (
    EXIT_HEALTHY,
    EXIT_UNAVAILABLE,
    EXIT_UNHEALTHY,
    IncidentStatusReport,
    IncidentStatusState,
    incident_status,
)
from sdo.contracts import IncidentRequest

if TYPE_CHECKING:
    from collections.abc import Iterator

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "sdo" / "contracts"


def _verdict(scenario: str, *, healthy: bool, qualified: bool = True, failure: str = "") -> dict[str, Any]:
    verdict: dict[str, Any] = {
        "scenario": scenario,
        "healthy": healthy,
        "qualified": qualified,
        "evaluated": True,
        "samples": 12,
        "errorRate": 0.0 if healthy else 1.0,
        "latencyMs": 4 if healthy else 0,
    }
    if not healthy:
        verdict["violations"] = ["error rate 1.00 > 0.50"]
        verdict["statusCounts"] = {"connection refused": 12}
        verdict["recentFailures"] = [failure or "iteration 7 step search GET /hotels: connection refused"]
    return verdict


def _burst(*verdicts: dict[str, Any]) -> dict[str, Any]:
    healthy = all(verdict["healthy"] or not verdict["qualified"] for verdict in verdicts)
    return {
        "workload": "verify",
        "healthy": healthy,
        "startedAt": "2026-09-27T10:00:00Z",
        "durationNs": 3_000_000_000,
        "verdicts": list(verdicts),
        "window": {"ignored": True},
    }


class _FakeProber:
    def __init__(self, response: dict[str, Any], status: int = 200) -> None:
        self.response = response
        self.status = status
        self.requests: list[dict[str, Any]] = []


@pytest.fixture
def prober() -> Iterator[tuple[_FakeProber, str]]:
    fake = _FakeProber(_burst(_verdict("search-hotels", healthy=True)))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            fake.requests.append({"path": self.path, "body": json.loads(self.rfile.read(length) or b"{}")})
            body = json.dumps(fake.response).encode()
            self.send_response(fake.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _traffic_request(tmp_path: Path) -> Path:
    request = IncidentRequest.model_validate_json((FIXTURE_DIR / "incident_request.json").read_text(encoding="utf-8"))
    finding = request.findings[0].model_copy(
        update={"detector_id": "traffic-health", "rule_id": "scenario-slo.search-hotels"}
    )
    path = tmp_path / "incident-request.json"
    path.write_text(request.model_copy(update={"findings": [finding]}).model_dump_json(), encoding="utf-8")
    return path


def test_status_is_unavailable_without_a_prober(tmp_path: Path) -> None:
    status = incident_status(prober_url=None, request_path=tmp_path / "missing.json")

    assert status.state == IncidentStatusState.UNAVAILABLE
    assert status.exit_code == EXIT_UNAVAILABLE
    assert "own verification" in status.render()


def test_healthy_burst_passes_verification(prober: tuple[_FakeProber, str], tmp_path: Path) -> None:
    fake, url = prober

    status = incident_status(prober_url=url, request_path=_traffic_request(tmp_path))

    assert status.state == IncidentStatusState.HEALTHY
    assert status.exit_code == EXIT_HEALTHY
    assert fake.requests == [{"path": "/v1/bursts", "body": {}}]
    assert "search-hotels" in status.render()


def test_unhealthy_burst_names_failing_routes_and_incident_scenarios(
    prober: tuple[_FakeProber, str], tmp_path: Path
) -> None:
    fake, url = prober
    fake.response = _burst(_verdict("search-hotels", healthy=False), _verdict("login", healthy=True))

    status = incident_status(prober_url=url, request_path=_traffic_request(tmp_path))

    assert status.state == IncidentStatusState.UNHEALTHY
    assert status.exit_code == EXIT_UNHEALTHY
    assert status.incident_scenarios == ("search-hotels",)
    text = status.render()
    assert "UNHEALTHY" in text
    assert "GET /hotels: connection refused" in text
    assert "search-hotels (triggered this incident)" in text


def test_unqualified_scenarios_are_reported_but_do_not_block(prober: tuple[_FakeProber, str], tmp_path: Path) -> None:
    fake, url = prober
    fake.response = _burst(_verdict("search-hotels", healthy=True), _verdict("login", healthy=False, qualified=False))

    status = incident_status(prober_url=url, request_path=tmp_path / "missing.json")

    assert status.state == IncidentStatusState.HEALTHY
    assert "login" in status.render()
    assert "never passed" in status.render()


def test_scenarios_and_workload_are_forwarded(prober: tuple[_FakeProber, str], tmp_path: Path) -> None:
    fake, url = prober

    incident_status(
        prober_url=url, request_path=tmp_path / "missing.json", workload="journey", scenarios=("search-hotels",)
    )

    assert fake.requests[0]["body"] == {"workload": "journey", "scenarios": ["search-hotels"]}


def test_prober_error_is_unavailable_not_healthy(prober: tuple[_FakeProber, str], tmp_path: Path) -> None:
    fake, url = prober
    fake.status = 400
    fake.response = {"error": "no verify-burst workload is defined"}

    status = incident_status(prober_url=url, request_path=tmp_path / "missing.json")

    assert status.state == IncidentStatusState.UNAVAILABLE
    assert "no verify-burst workload is defined" in status.render()


def test_cli_prints_json_and_exits_with_the_verdict(
    prober: tuple[_FakeProber, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake, url = prober
    fake.response = _burst(_verdict("search-hotels", healthy=False))
    monkeypatch.setenv("SDO_PROBER_URL", url)
    monkeypatch.setenv("SDO_REQUEST_PATH", str(_traffic_request(tmp_path)))

    exit_code = main(["incident", "status", "--json"])

    assert exit_code == EXIT_UNHEALTHY
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unhealthy"
    assert payload["incident_scenarios"] == ["search-hotels"]
    assert payload["burst"]["verdicts"][0]["recent_failures"]


def test_status_model_rejects_a_healthy_state_without_a_burst() -> None:
    with pytest.raises(ValueError, match="burst"):
        IncidentStatusReport(state=IncidentStatusState.HEALTHY, burst=None, detail="")
