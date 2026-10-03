"""Conformance ratchet for the traffic workload proto (Python side).

``proto/sdodev/contracts/v1alpha1/traffic.proto`` is the single schema SOURCE
for the ``.sdo/diagnostics/traffic/workloads/<name>.yaml`` artifact. This test
proves the generated Python proto type models the stored wire: every committed
workload fixture parses into ``TrafficWorkload`` with no unknown field (protojson
rejects one, so a field added without the proto fails here), and the host loader
-- which stands in for the Go-side protovalidate CEL, since the repo keeps no
Python CEL runtime -- rejects exactly the invariants the hand-written Pydantic
model rejected. The Go test (controller/runtime/traffic_conformance_test.go)
checks the protovalidate CEL mirrors those same invariants.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from google.protobuf import json_format

from sdo.contracts import TrafficWorkload
from sdo.operational_memory.traffic import TrafficWorkloadError, load_traffic_workload, scenario_slo

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "hotel_reservation" / "traffic" / "workloads"

_LINK_PROBE = {
    "apiVersion": "sdo.dev/v1alpha1",
    "kind": "TrafficWorkload",
    "name": "links",
    "purpose": "link-probe",
    "links": [{"from": "frontend", "to": "search", "port": 8082}],
}


@pytest.mark.parametrize("name", ["health", "journey", "verify"])
def test_proto_schema_models_every_workload_fixture(name: str) -> None:
    raw = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))
    # Raises on any field the proto schema does not model.
    message = json_format.ParseDict(raw, TrafficWorkload())
    assert message.name == name


def test_proto_schema_rejects_an_unknown_field() -> None:
    raw = yaml.safe_load((FIXTURES / "health.yaml").read_text(encoding="utf-8"))
    raw["unmodeled"] = True
    with pytest.raises(json_format.ParseError):
        json_format.ParseDict(raw, TrafficWorkload())


@pytest.mark.parametrize("name", ["health", "journey", "verify"])
def test_loader_accepts_the_valid_fixtures(name: str) -> None:
    raw = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))
    workload = load_traffic_workload(raw)
    assert workload.name == name
    # The effective SLO resolves defaults when nothing overrides them.
    if workload.scenarios:
        assert scenario_slo(workload, workload.scenarios[0].id).window >= 1


def test_loader_accepts_a_link_probe() -> None:
    workload = load_traffic_workload(dict(_LINK_PROBE))
    assert workload.purpose == "link-probe"
    assert getattr(workload.links[0], "from") == "frontend"


# The invariants the proto expresses as protovalidate CEL on the Go side; the
# Python loader must reject each one too (CEL-equivalent host checks).
@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(apiVersion="sdo.dev/v2"), id="api-version"),
        pytest.param(lambda d: d.update(kind="TrafficMix"), id="kind"),
        pytest.param(lambda d: d.update(name="Bad_Name"), id="name-dns-label"),
        pytest.param(lambda d: d.update(purpose="chaos"), id="purpose"),
        pytest.param(lambda d: d.update(arrival="burst"), id="arrival"),
        pytest.param(lambda d: d.update(ratePerSecond=100), id="rate"),
        pytest.param(lambda d: d["scenarios"].append({"id": "login"}), id="duplicate-scenario"),
        pytest.param(lambda d: d["scenarios"].append({"id": "BAD"}), id="scenario-id"),
    ],
)
def test_loader_rejects_cel_mirrored_invariants(mutate) -> None:
    raw = yaml.safe_load((FIXTURES / "health.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    with pytest.raises(TrafficWorkloadError):
        load_traffic_workload(raw)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(links=[{"from": "a", "to": "a", "port": 80}]), id="self-loop"),
        pytest.param(lambda d: d["links"].append({"from": "frontend", "to": "search", "port": 8082}), id="dup-link"),
        pytest.param(lambda d: d.update(links=[]), id="no-links"),
        pytest.param(lambda d: d.update(scenarios=[{"id": "x"}]), id="link-probe-scenarios"),
    ],
)
def test_loader_rejects_link_probe_cel_invariants(mutate) -> None:
    raw = dict(_LINK_PROBE)
    raw["links"] = [dict(link) for link in raw["links"]]
    mutate(raw)
    with pytest.raises(TrafficWorkloadError):
        load_traffic_workload(raw)
