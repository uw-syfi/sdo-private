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

A catalog may also carry composite families (several faults injected at once). A composite family
names the single-fault families it is made of (``requires``), and its first occurrence has the extra kind:

``composite``
    The first occurrence of a composite family. Every component class has already appeared as a single
    fault, so memory from the singles is all there is to compose from. Later occurrences of a composite
    are ``exact`` (same composite) or ``variant`` (same family, another target mix).
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

IncidentKind = Literal["first", "novel", "exact", "variant", "composite"]
#: Kinds the generator shuffles. ``exact_c`` and ``variant_c`` are repeats and variants of composites.
_Label = Literal["first", "novel", "exact", "variant", "composite", "exact_c", "variant_c"]

STREAM_SEED = 20260930
STREAM_LENGTH = 24
PILOT_LENGTH = 8
NOVEL_COUNT = 2
#: Fraction of the incidents that are parameter variants (capped by the catalog supply).
VARIANT_SHARE = 1 / 3
_MAX_ATTEMPTS = 20000

MIXED_SEED = 20261002
MIXED_LENGTH = 24
#: The mixed pilot prefix is longer than the single-fault one: a composite can only start after all four core firsts.
MIXED_PILOT_LENGTH = 10
COMPOSITE_EXACT_COUNT = 4
COMPOSITE_VARIANT_COUNT = 2


@dataclass(frozen=True)
class FaultFamily:
    """One root-cause class: a base problem and its parameter variants."""

    name: str
    problem_id: str
    variants: tuple[str, ...]
    #: For a composite: the single-fault families whose first occurrence must precede its own.
    requires: tuple[str, ...] = ()

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
    composite: tuple[FaultFamily, ...] = ()

    def __post_init__(self) -> None:
        ids = [p for family in (*self.core, *self.novel, *self.composite) for p in family.all_problem_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("catalog problem ids must be unique across families")
        if len(self.novel) < NOVEL_COUNT:
            raise ValueError(f"catalog needs at least {NOVEL_COUNT} novel families")
        core_names = {family.name for family in self.core}
        for family in self.composite:
            if not family.requires or not set(family.requires) <= core_names:
                raise ValueError(f"composite family {family.name!r} requires core families, got {family.requires}")

    @property
    def variant_supply(self) -> int:
        return sum(len(family.variants) for family in self.core)

    @property
    def composite_variant_supply(self) -> int:
        return sum(len(family.variants) for family in self.composite)

    @property
    def all_families(self) -> tuple[FaultFamily, ...]:
        return (*self.core, *self.novel, *self.composite)


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
    ),
)

_SINGLE_CLASSES = ("readiness-probe", "missing-configmap", "network-policy-block")

#: The mixed catalog adds the hand-registered composites of the SREGym ``sdo`` branch (``composed_failures.py``).
#: ``composite3``/``3b``/``3c`` share three fault classes on different targets (one family: C1, C2, C4);
#: ``composite4`` adds the frontend selector (its own family: C5). ``composite5`` (C3) is left out: it contains an
#: oversized resource request, which has no usable single fault (destructive on recovery), so its first occurrence
#: could not be composed from singles.
MIXED_CATALOG = Catalog(
    core=HOTEL_CATALOG.core,
    novel=HOTEL_CATALOG.novel,
    composite=(
        FaultFamily(
            "composite-3-class",
            "composite3_hotel_geo_rate_recommendation",
            (
                "composite3b_hotel_profile_mongodb_geo_recommendation",
                "composite3c_hotel_rate_mongodb_geo_user",
            ),
            requires=_SINGLE_CLASSES,
        ),
        FaultFamily(
            "composite-4-class",
            "composite4_hotel_profile_rate_recommendation_frontend",
            (),
            requires=(*_SINGLE_CLASSES, "wrong-service-selector"),
        ),
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
    labels: list[_Label],
    rng: random.Random,
    catalog: Catalog,
    core_order: list[FaultFamily],
    novel_order: list[FaultFamily],
    opening: tuple[str, ...] = (),
    composite_order: list[FaultFamily] | None = None,
) -> list[StreamIncident] | None:
    """Turn a shuffled kind sequence into incidents, or None when it cannot be realised.

    ``opening`` pins the first incidents to given problem ids; ``labels`` then covers only the rest.
    """

    family_of = {p: f.name for f in catalog.all_families for p in f.all_problem_ids}
    core_iter, novel_iter = iter(core_order), iter(novel_order)
    composite_iter = iter(composite_order or [])
    requires = {f.name: set(f.requires) for f in catalog.composite}
    incidents: list[StreamIncident] = []
    seen: list[str] = []
    seen_families: set[str] = set()
    unused_variants: dict[str, list[str]] = {f.name: list(f.variants) for f in catalog.core}
    unused_composite_variants: dict[str, list[str]] = {f.name: list(f.variants) for f in catalog.composite}
    core_names = {f.name for f in catalog.core}
    pinned: list[IncidentKind] = [
        _pinned_kind(problem_id, family_of, core_names, seen, seen_families, requires) for problem_id in opening
    ]
    for index, (problem_id, kind) in enumerate(zip(opening, pinned, strict=True)):
        for unused in (unused_variants, unused_composite_variants):
            if problem_id in unused.get(family_of[problem_id], []):
                unused[family_of[problem_id]].remove(problem_id)
        incidents.append(StreamIncident(index, problem_id, kind, family_of[problem_id]))
    for offset, label in enumerate(labels):
        index = len(opening) + offset
        previous = incidents[-1].problem_id if incidents else None
        kind: IncidentKind
        if label == "first":
            kind, problem_id = "first", next(core_iter).problem_id
        elif label == "novel":
            kind, problem_id = "novel", next(novel_iter).problem_id
        elif label == "composite":
            family = next(composite_iter)
            if not requires[family.name] <= seen_families:
                return None
            kind, problem_id = "composite", family.problem_id
        elif label in ("variant", "variant_c"):
            pool = unused_variants if label == "variant" else unused_composite_variants
            candidates = [v for family in seen_families for v in pool.get(family, [])]
            if not candidates:
                return None
            kind, problem_id = "variant", rng.choice(sorted(candidates))
            pool[family_of[problem_id]].remove(problem_id)
        else:
            composites_only = label == "exact_c"
            candidates = [p for p in seen if p != previous and (family_of[p] in requires) == composites_only]
            if not candidates:
                return None
            kind, problem_id = "exact", rng.choice(candidates)
        if problem_id == previous:
            return None
        incidents.append(StreamIncident(index, problem_id, kind, family_of[problem_id]))
        if problem_id not in seen:
            seen.append(problem_id)
        seen_families.add(family_of[problem_id])
    return incidents


def _repeat(kind: _Label, count: int) -> list[_Label]:
    return [kind] * count


def _pinned_kind(
    problem_id: str,
    family_of: dict[str, str],
    core_names: set[str],
    seen: list[str],
    seen_families: set[str],
    composite_requires: dict[str, set[str]] | None = None,
) -> IncidentKind:
    family = family_of[problem_id]
    composite_requires = composite_requires or {}
    if family not in seen_families:
        if family in composite_requires:
            missing = composite_requires[family] - seen_families
            if missing:
                raise ValueError(f"composite {problem_id!r} precedes its component classes {sorted(missing)}")
            kind: IncidentKind = "composite"
        else:
            kind = "first" if family in core_names else "novel"
    else:
        kind = "exact" if problem_id in seen else "variant"
    if problem_id not in seen:
        seen.append(problem_id)
    seen_families.add(family)
    return kind


def _is_learnable(incidents: list[StreamIncident], catalog: Catalog) -> bool:
    """The pilot prefix holds every core first plus a novel, a repeat and a variant; every core fault recurs."""

    pilot_kinds = [i.kind for i in incidents[:PILOT_LENGTH]]
    repeated = {i.problem_id for i in incidents if i.kind == "exact"}
    return (
        pilot_kinds.count("first") == len(catalog.core)
        and {"novel", "exact", "variant"} <= set(pilot_kinds)
        and all(family.problem_id in repeated for family in catalog.core)
    )


def _is_mixed_learnable(incidents: list[StreamIncident], catalog: Catalog) -> bool:
    """The mixed pilot prefix holds every core first, a composite, a repeat and a variant."""

    pilot_kinds = [i.kind for i in incidents[:MIXED_PILOT_LENGTH]]
    return pilot_kinds.count("first") == len(catalog.core) and {"composite", "exact", "variant"} <= set(pilot_kinds)


#: The first four incidents of the stream, pinned. The first attempt sampled them from the seed, then had to
#: replace a novel fault that namespace-scoped SDO can neither observe nor repair (see the decisions log);
#: its four completed incidents are kept as this opening so they need not be rerun.
STREAM_OPENING = (
    "network_policy_block",
    "missing_configmap_hotel_reservation",
    "readiness_probe_misconfiguration_hotel_reservation",
    "network_policy_block",
)


def generate_stream(
    seed: int, length: int, catalog: Catalog = HOTEL_CATALOG, opening: tuple[str, ...] = ()
) -> list[StreamIncident]:
    """The incident stream for ``seed``: same seed, length and opening give the same incidents."""

    if catalog.composite:
        return _generate_mixed(seed, length, catalog, opening)
    variants = _variant_count(length, catalog)
    exact = length - len(catalog.core) - NOVEL_COUNT - variants
    if exact < 1 or length < PILOT_LENGTH:
        raise ValueError(f"stream length {length} is too short for {len(catalog.core)} core and {NOVEL_COUNT} novel")
    family_of = {p: f.name for f in (*catalog.core, *catalog.novel) for p in f.all_problem_ids}
    if any(problem_id not in family_of for problem_id in opening):
        raise ValueError("opening names a problem that is not in the catalog")
    rng = random.Random(seed)
    opened = {family_of[p] for p in opening}
    core_order = [f for f in rng.sample(list(catalog.core), len(catalog.core)) if f.name not in opened]
    novel_order = [f for f in rng.sample(list(catalog.novel), NOVEL_COUNT) if f.name not in opened]
    kinds: list[_Label] = [
        *_repeat("first", len(core_order)),
        *_repeat("novel", len(novel_order)),
        *_repeat("variant", variants),
        *_repeat("exact", exact),
    ]
    pinned = _fill([], rng, catalog, [], [], opening) or []
    for pinned_incident in pinned:
        if pinned_incident.kind in {"variant", "exact"}:
            kinds.remove(pinned_incident.kind)
    for _ in range(_MAX_ATTEMPTS):
        labels = list(kinds)
        rng.shuffle(labels)
        incidents = _fill(labels, rng, catalog, core_order, novel_order, opening)
        if incidents is not None and _is_learnable(incidents, catalog):
            return incidents
    raise ValueError(f"no valid stream found for seed {seed} and length {length}")


def _generate_mixed(seed: int, length: int, catalog: Catalog, opening: tuple[str, ...]) -> list[StreamIncident]:
    """A stream of single faults and composites: composites start once every component class has appeared."""

    family_of = {p: f.name for f in catalog.all_families for p in f.all_problem_ids}
    if any(problem_id not in family_of for problem_id in opening):
        raise ValueError("opening names a problem that is not in the catalog")
    composite_variants = min(catalog.composite_variant_supply, COMPOSITE_VARIANT_COUNT)
    variants = min(catalog.variant_supply, round(length * VARIANT_SHARE) - composite_variants)
    rng = random.Random(seed)
    opened = {family_of[p] for p in opening}
    core_order = [f for f in rng.sample(list(catalog.core), len(catalog.core)) if f.name not in opened]
    novel_order = [f for f in rng.sample(list(catalog.novel), NOVEL_COUNT) if f.name not in opened]
    composite_order = [f for f in rng.sample(list(catalog.composite), len(catalog.composite)) if f.name not in opened]
    kinds: list[_Label] = [
        *_repeat("first", len(core_order)),
        *_repeat("novel", len(novel_order)),
        *_repeat("composite", len(composite_order)),
        *_repeat("variant", variants),
        *_repeat("variant_c", composite_variants),
        *_repeat("exact_c", COMPOSITE_EXACT_COUNT),
    ]
    exact = length - len(opening) - len(kinds)
    if exact < 1 or length < MIXED_PILOT_LENGTH:
        raise ValueError(f"stream length {length} is too short for the mixed catalog")
    kinds.extend(_repeat("exact", exact))
    pinned = _fill([], rng, catalog, [], [], opening, []) or []
    composite_names = {f.name for f in catalog.composite}
    for pinned_incident in pinned:
        if pinned_incident.kind in {"variant", "exact"}:
            of_composite = pinned_incident.family in composite_names
            repeat_label: _Label = (
                ("exact_c" if of_composite else "exact")
                if pinned_incident.kind == "exact"
                else ("variant_c" if of_composite else "variant")
            )
            kinds.remove(repeat_label)
    for _ in range(_MAX_ATTEMPTS):
        labels = list(kinds)
        rng.shuffle(labels)
        incidents = _fill(labels, rng, catalog, core_order, novel_order, opening, composite_order)
        if incidents is not None and _is_mixed_learnable(incidents, catalog):
            return incidents
    raise ValueError(f"no valid mixed stream found for seed {seed} and length {length}")


#: Three singles, then a composite, its exact repeat, and a variant of it (another target mix).
MINI_OPENING = (
    "network_policy_block",
    "missing_configmap_hotel_reservation",
    "readiness_probe_misconfiguration_hotel_reservation",
    "composite3c_hotel_rate_mongodb_geo_user",
    "composite3c_hotel_rate_mongodb_geo_user",
    "composite3_hotel_geo_rate_recommendation",
)


def mini_stream() -> list[StreamIncident]:
    """The six-incident quick-validation stream: singles, then composition, repeat and variant of a composite."""

    incidents = _fill([], random.Random(MIXED_SEED), MIXED_CATALOG, [], [], MINI_OPENING, [])
    assert incidents is not None
    return incidents


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
    lines = "\n".join(f"#   {i.index + 1:>2}  {i.kind:<8} {i.problem_id}" for i in incidents)
    if any(i.kind == "composite" for i in incidents):
        lines += (
            "\n#\n# composite = first occurrence of a multi-fault composite; every component class has already"
            "\n# appeared as a single fault. Its repeats are exact, its other target mixes are variants."
        )
    return lines


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
allow_failed_verdicts = true  # a stream must continue past an incident graded as failed

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

    stream = generate_stream(seed, length, opening=STREAM_OPENING)
    pilot, rest = stream[:PILOT_LENGTH], stream[PILOT_LENGTH:]
    files = {
        "sdo_codex_luna_stream.toml": render_sdo_pipeline_toml(stream, name="sdo_codex_luna_stream", seed=seed),
        "sdo_codex_luna_stream_pilot.toml": render_sdo_pipeline_toml(
            pilot, name="sdo_codex_luna_stream_pilot", seed=seed
        ),
        "codex_luna_stream_baseline_1_8.toml": render_baseline_toml(pilot, label="incidents 1-8", seed=seed),
        "codex_luna_stream_baseline_5_8.toml": render_baseline_toml(
            stream[4:PILOT_LENGTH], label="incidents 5-8", seed=seed
        ),
        "codex_luna_stream_baseline_9_24.toml": render_baseline_toml(rest, label="incidents 9-24", seed=seed),
        "stream_learning_curve_manifest.json": render_manifest(stream, seed),
    }
    written: list[Path] = []
    for name, text in files.items():
        path = directory / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def write_mixed_configs(directory: Path) -> list[Path]:
    """Write the committed mixed-stream (singles and composites) configs and manifest into ``directory``."""

    stream = generate_stream(MIXED_SEED, MIXED_LENGTH, MIXED_CATALOG)
    mini = mini_stream()
    files = {
        "sdo_codex_luna_mixed_stream.toml": render_sdo_pipeline_toml(
            stream, name="sdo_codex_luna_mixed_stream", seed=MIXED_SEED
        ),
        "sdo_codex_luna_mixed_pilot.toml": render_sdo_pipeline_toml(
            stream[:MIXED_PILOT_LENGTH], name="sdo_codex_luna_mixed_pilot", seed=MIXED_SEED
        ),
        "sdo_codex_luna_mixed_mini.toml": render_sdo_pipeline_toml(
            mini, name="sdo_codex_luna_mixed_mini", seed=MIXED_SEED
        ),
        "codex_luna_mixed_baseline.toml": render_baseline_toml(stream, label="mixed stream", seed=MIXED_SEED),
        "codex_luna_mixed_baseline_mini.toml": render_baseline_toml(mini, label="mixed mini stream", seed=MIXED_SEED),
        "mixed_stream_manifest.json": render_manifest(stream, MIXED_SEED),
    }
    written: list[Path] = []
    for name, text in files.items():
        path = directory / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    experiments = Path(__file__).resolve().parents[1] / "experiments"
    for written_path in (*write_configs(experiments), *write_mixed_configs(experiments)):
        print(written_path)
