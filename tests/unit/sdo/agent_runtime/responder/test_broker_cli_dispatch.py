"""The broker subprocess protocol dispatch the controller drives over stdin."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sdo.agent_runtime.responder.broker_cli import dispatch_broker_operation
from sdo.operational_memory.broker_service import BrokerServiceError


@dataclass
class _StubService:
    """Records the operations the dispatch routes to, standing in for BrokerService."""

    released: list[str] = field(default_factory=list)
    release_result: bool = True

    def release_incident(self, incident_id: str) -> bool:
        self.released.append(incident_id)
        return self.release_result


def test_release_operation_routes_to_release_incident() -> None:
    service = _StubService()

    response = dispatch_broker_operation(service, {"operation": "release", "incident_id": "app-123"})

    assert service.released == ["app-123"]
    assert response == {"released": True}


def test_release_operation_reports_an_idempotent_no_op() -> None:
    service = _StubService(release_result=False)

    response = dispatch_broker_operation(service, {"operation": "release", "incident_id": "app-123"})

    assert response == {"released": False}


def test_an_unknown_operation_is_rejected() -> None:
    with pytest.raises(BrokerServiceError):
        dispatch_broker_operation(_StubService(), {"operation": "wat"})
