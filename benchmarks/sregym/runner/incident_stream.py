"""Deterministic Hotel Reservation incident stream for the learning-curve experiment.

SREGym's own ``--sequence-len`` samples problem ids uniformly with replacement
and never mixes in parameter variants, so it cannot produce a stream where
memory has something to transfer. This module builds the stream from a seed
instead and renders it as pipeline and baseline configurations, so both arms
replay the identical incidents.

Incident kinds:

``first``
    The first occurrence of a core fault class. Nothing in memory applies.
``novel``
    The first occurrence of a held-out fault class that is unrelated to every
    earlier incident. Nothing in memory applies either.
``exact``
    A repeat of an earlier incident (same problem id).
``variant``
    The same root-cause class as an earlier incident with different
    parameters (another service or ConfigMap), never seen before.

Every repeat and variant follows the first occurrence of its family.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

IncidentKind = Literal["first", "novel", "exact", "variant"]

STREAM_SEED = 20260930
STREAM_LENGTH = 24
PILOT_LENGTH = 8
NOVEL_COUNT = 3
#: Fraction of the incidents that are parameter variants (capped by the catalog supply).
VARIANT_SHARE = 1 / 3
_MAX_ATTEMPTS = 20000


@dataclass(frozen=True)
class FaultFamily:
    """One root-cause class: a base problem and its parameter variants."""

    name: str
    problem_id: str
    variants: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("FaultFamily.name must not be empty")
        if not self.problem_id:
            raise ValueError(f"FaultFamily {self.name!r}: problem_id must not be empty")
        if len({self.problem_id, *self.variants}) != 1 + len(self.variants):
            raise ValueError(f"FaultFamily {self.name!r}: problem ids must be distinct")

    @property
    def all_problem_ids(self) -> tuple[str, ...]:
        return (self.problem_id, *self.variants)


@dataclass(frozen=True)
class Catalog:
    """Core families (repeated and varied over the stream) and a pool of one-off novel families."""

    core: tuple[FaultFamily, ...]
    novel: tuple[FaultFamily, ...]

    def __post_init__(self) -> None:
        ids = [p for family in (*self.core, *self.novel) for p in family.all_problem_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("catalog problem ids must be unique across families")
        if len(self.novel) < NOVEL_COUNT:
            raise ValueError(f"catalog needs at least {NOVEL_COUNT} novel families")

    @property
    def variant_supply(self) -> int:
        return sum(len(family.variants) for family in self.core)


_HOTEL = "hotel_reservation"

#: Verified source-deployable and injectable on a Hotel Reservation kind cluster (see the decisions log).
HOTEL_CATALOG = Catalog(
    core=(
        FaultFamily(
            "readiness-probe",
            "readiness_probe_misconfiguration_hotel_reservation",
            tuple(
                f"readiness_probe_misconfiguration__v_{_HOTEL}_{s}"
                for s in ("geo", "search", "user", "profile", "recommendation", "reservation")
            ),
        ),
        FaultFamily(
            "missing-configmap",
            "missing_configmap_hotel_reservation",
            (
                "missing_configmap_mongodb_rate_hotel_reservation",
                "missing_configmap_mongodb_geo_rate_hotel_reservation",
            ),
        ),
        # Its generated variants target non-frontend services, but the mitigation oracle always probes the
        # frontend port, so they can never pass; the base problem is the only usable member.
        FaultFamily("wrong-service-selector", "wrong_service_selector_hotel_reservation", ()),
        FaultFamily("network-policy-block", "network_policy_block", ()),
    ),
    novel=(
        FaultFamily("wrong-dns-policy", "wrong_dns_policy_hotel_reservation", ()),
        FaultFamily("misconfig-app", "misconfig_app_hotel_res", ()),
        FaultFamily("service-dns-resolution", f"service_dns_resolution_failure__v_{_HOTEL}_frontend", ()),
    ),
)


@dataclass(frozen=True)
class StreamIncident:
    index: int
    problem_id: str
    kind: IncidentKind
    family: str


def _variant_count(length: int, catalog: Catalog) -> int:
    return min(catalog.variant_supply, round(length * VARIANT_SHARE))


def _fill(
    labels: list[IncidentKind],
    rng: random.Random,
    catalog: Catalog,
    core_order: list[FaultFamily],
    novel_order: list[FaultFamily],
) -> list[StreamIncident] | None:
    """Turn a shuffled kind sequence into incidents, or None when it cannot be realised."""

    family_of = {p: f.name for f in (*catalog.core, *catalog.novel) for p in f.all_problem_ids}
    core_iter, novel_iter = iter(core_order), iter(novel_order)
    incidents: list[StreamIncident] = []
    seen: list[str] = []
    seen_families: set[str] = set()
    unused_variants: dict[str, list[str]] = {}
    for index, kind in enumerate(labels):
        previous = incidents[-1].problem_id if incidents else None
        if kind == "first":
            family = next(core_iter)
            problem_id = family.problem_id
            unused_variants[family.name] = list(family.variants)
        elif kind == "novel":
            problem_id = next(novel_iter).problem_id
        elif kind == "variant":
            candidates = [v for family in seen_families for v in unused_variants.get(family, [])]
            if not candidates:
                return None
            problem_id = rng.choice(sorted(candidates))
            unused_variants[family_of[problem_id]].remove(problem_id)
        else:
            candidates = [p for p in seen if p != previous]
            if not candidates:
                return None
            problem_id = rng.choice(candidates)
        if problem_id == previous:
            return None
        incidents.append(StreamIncident(index, problem_id, kind, family_of[problem_id]))
        if problem_id not in seen:
            seen.append(problem_id)
        seen_families.add(family_of[problem_id])
    return incidents


def _is_learnable(incidents: list[StreamIncident], catalog: Catalog) -> bool:
    """The pilot prefix holds every core first plus a novel, a repeat and a variant; every core fault recurs."""

    pilot_kinds = [i.kind for i in incidents[:PILOT_LENGTH]]
    repeated = {i.problem_id for i in incidents if i.kind == "exact"}
    return (
        pilot_kinds.count("first") == len(catalog.core)
        and {"novel", "exact", "variant"} <= set(pilot_kinds)
        and all(family.problem_id in repeated for family in catalog.core)
    )


def generate_stream(seed: int, length: int, catalog: Catalog = HOTEL_CATALOG) -> list[StreamIncident]:
    """The incident stream for ``seed``: same seed and length, same incidents."""

    variants = _variant_count(length, catalog)
    exact = length - len(catalog.core) - NOVEL_COUNT - variants
    if exact < 1 or length < PILOT_LENGTH:
        raise ValueError(f"stream length {length} is too short for {len(catalog.core)} core and {NOVEL_COUNT} novel")
    rng = random.Random(seed)
    core_order = rng.sample(list(catalog.core), len(catalog.core))
    novel_order = rng.sample(list(catalog.novel), NOVEL_COUNT)
    kinds: list[IncidentKind] = (
        ["first"] * len(catalog.core) + ["novel"] * NOVEL_COUNT + ["variant"] * variants + ["exact"] * exact
    )
    for _ in range(_MAX_ATTEMPTS):
        labels = list(kinds)
        rng.shuffle(labels)
        incidents = _fill(labels, rng, catalog, core_order, novel_order)
        if incidents is not None and _is_learnable(incidents, catalog):
            return incidents
    raise ValueError(f"no valid stream found for seed {seed} and length {length}")


def render_manifest(incidents: list[StreamIncident], seed: int = STREAM_SEED) -> str:
    return (
        json.dumps(
            {
                "seed": seed,
                "length": len(incidents),
                "incidents": [
                    {"index": i.index, "problem_id": i.problem_id, "kind": i.kind, "family": i.family}
                    for i in incidents
                ],
            },
            indent=2,
        )
        + "\n"
    )


def _incident_lines(incidents: list[StreamIncident]) -> str:
    return "\n".join(f"#   {i.index + 1:>2}  {i.kind:<8} {i.problem_id}" for i in incidents)


_SDO_HEADER = """\
# Learning-curve stream, SDO arm: {count} Hotel Reservation incidents from seed {seed},
# answered by ONE long-running SDO controller (Codex gpt-6-luna) in
# hotel-reservation-sdo. Stage 0 starts from a fresh application workspace (cold
# lifecycle); every later stage chains the workspace, so each incident sees the
# detectors, playbooks and outcomes learned before it. Strict receipts are
# required and are written after each incident's reflection drains.
# Generated by benchmarks.sregym.runner.incident_stream; do not edit by hand.
# Settings otherwise copied from sdo_codex_luna_persistent.toml; images are the
# private `stream1` build so the shared v0.1.0 tags stay untouched.
# Baseline (identical incidents): codex_luna_stream_baseline_*.toml.
#
# Stream (kind: first/novel = no applicable memory, exact = repeat, variant = same
# root cause with other parameters):
{stream}
"""

_SDO_DEFAULTS = """\

[pipeline]
name = "{name}"

[defaults]
agent = "sdo_codex"
model = "gpt-6-luna"
reasoning_effort = "medium"  # explicit on both arms; SDO pins its incident agents to medium
parallel = 1
agent_timeout = 3600
app_filter = "hotel_reservation"
deploy_from_source = true
application_workspace = "persistent"
require_strict_receipt = true

[defaults.env]
judge_model_id = "codex-gpt-6-luna"  # Codex CLI judge; reasoning effort defaults to xhigh
worker_cpu_limit = "3"
kind_worker_nodes = 1  # 1 control plane + 1 worker per lane (topology measurement, 2026-09-28)
reuse_cluster = true
force_recreate_cluster = false
preserve_infrastructure = true
fast_namespace_teardown = true  # skips pod termination grace between incidents; outside every measured window
cleanup_defer_timeout_seconds = 4200
docker_builder = "sdo-example"

[defaults.variants]
enabled = false

[defaults.agent_config.sdo_codex]
backend = "agent-cli"
provider = "codex"
model = "gpt-6-luna"
timeout_sec = 3600
controller_image = "sdo-controller:stream1"
responder_image = "sdo-sregym-responder:stream1"
validator_image = "sdo-detector-validator:stream1"
credentials_secret = "sdo-agent-credentials"
persistent_controller = true
"""


def render_sdo_pipeline_toml(incidents: list[StreamIncident], *, name: str, seed: int = STREAM_SEED) -> str:
    parts = [_SDO_HEADER.format(count=len(incidents), seed=seed, stream=_incident_lines(incidents))]
    parts.append(_SDO_DEFAULTS.format(name=name.replace("_", "-")))
    for incident in incidents:
        chained = "false" if incident.index == 0 else "true"
        parts.append(
            f"""
[[stages]]
name = "i{incident.index + 1:02d}-{incident.kind}-{incident.problem_id.replace("_", "-")}"
chain_kb = false
chain_application_workspace = {chained}

[stages.runner]
problems = ["{incident.problem_id}"]
"""
        )
    return "".join(parts)


_BASELINE_HEADER = """\
# Memoryless stock Codex baseline for the learning-curve stream ({label}). Lists the
# same incidents, in the same order, as sdo_codex_luna_stream.toml (seed {seed}).
# Codex keeps no memory between problems, so each run is an independent sample and
# splitting the stream over two configs changes nothing. Settings copied from
# codex_luna_sequence_baseline.toml (NOT the verify variant).
# Generated by benchmarks.sregym.runner.incident_stream; do not edit by hand.
#
{stream}

[runner]
agent = "codex"
model = "gpt-6-luna"
reasoning_effort = "medium"  # explicit on both arms; SDO pins its incident agents to medium
parallel = 1
agent_timeout = 3600
app_filter = "hotel_reservation"
deploy_from_source = true
enable_summary = false
no_inject_summary = true
problems = [
{problems}
]

[runner.env]
judge_model_id = "codex-gpt-6-luna"  # Codex CLI judge; reasoning effort defaults to xhigh
worker_cpu_limit = "3"
kind_worker_nodes = 1  # 1 control plane + 1 worker per lane (topology measurement, 2026-09-28)
reuse_cluster = true
force_recreate_cluster = false
preserve_infrastructure = true
fast_namespace_teardown = true  # skips pod termination grace between incidents; outside every measured window
docker_builder = "sdo-example"
"""


def render_baseline_toml(incidents: list[StreamIncident], *, label: str = "", seed: int = STREAM_SEED) -> str:
    problems = "\n".join(f'    "{i.problem_id}",' for i in incidents)
    return _BASELINE_HEADER.format(
        label=label or f"{len(incidents)} incidents", seed=seed, stream=_incident_lines(incidents), problems=problems
    )


def write_configs(directory: Path, *, seed: int = STREAM_SEED, length: int = STREAM_LENGTH) -> list[Path]:
    """Write the committed stream configs and manifest into ``directory``."""

    stream = generate_stream(seed, length)
    pilot, rest = stream[:PILOT_LENGTH], stream[PILOT_LENGTH:]
    files = {
        "sdo_codex_luna_stream.toml": render_sdo_pipeline_toml(stream, name="sdo_codex_luna_stream", seed=seed),
        "sdo_codex_luna_stream_pilot.toml": render_sdo_pipeline_toml(
            pilot, name="sdo_codex_luna_stream_pilot", seed=seed
        ),
        "codex_luna_stream_baseline_1_8.toml": render_baseline_toml(pilot, label="incidents 1-8", seed=seed),
        "codex_luna_stream_baseline_9_24.toml": render_baseline_toml(rest, label="incidents 9-24", seed=seed),
        "stream_learning_curve_manifest.json": render_manifest(stream, seed),
    }
    written = []
    for name, text in files.items():
        path = directory / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in write_configs(Path(__file__).resolve().parents[1] / "experiments"):
        print(written_path)
