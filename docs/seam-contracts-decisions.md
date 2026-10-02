# Seam contracts: single source of truth

Status: in progress (tracked in the single-source-of-truth refactor PR).

## Problem

Every integration bug we hit while running the luna experiments lived at a **seam** —
a boundary where two components hand data across by convention — not in any component's
internal logic. The components are strongly typed *inside*; the seams between them were
stringly-typed and hand-synchronised.

Representative bugs, all one root cause ("the same thing defined twice"):

- `--closeout-state-gate`: the Python launcher passed a flag the Go controller binary
  did not define, so every controller pod died with "unrecognized arguments".
- Stale validator image: `ContainerSandboxRunner` silently fell back to an old image
  whose SDK schema lacked the `links` field, so `links.yaml` was dropped.
- The Go runtime contract structs are **hand-mirrored** from the `sdo/contracts/`
  Pydantic models (the model docstrings literally say "mirror the Go runtime's
  `omitempty`"). Nothing enforces the mirror.
- The manifest / `links.yaml` schema is defined in Go's `DisallowUnknownFields` but
  authored on the Python / LLM side.

## Principle

**Type the seams like the interiors.** Each cross-boundary contract gets one source of
truth with generated consumers, and a test that makes drift a build failure instead of a
runtime failure ten minutes into a run.

The best way to keep two definitions in sync is to not have two definitions.

## Decision: proto/buf as the IDL — not gRPC

- **Adopt the IDL + codegen half of proto.** `.proto` is the single source; `buf generate`
  produces Go and Python types. A field added on one side and missing on the other
  becomes a build error.
- **Do not adopt gRPC transport.** SDO's controller↔responder boundary is a one-shot
  Kubernetes Job reading a request and writing a result through files / the repository
  volume, not two long-lived services exchanging RPCs. Keep the existing transports:
  files, PVC, git, HTTP, ConfigMaps.
- **protojson on the wire, not binary.** Contracts stay human- and LLM-readable and
  git-diffable; proto governs the *schema*, not the encoding.
- **protovalidate (CEL) for semantic invariants** (ordered timestamps, token arithmetic,
  unique IDs) so validation is single-source too, rather than re-homed to a hand layer.

### Rejected alternatives

- **Pydantic-as-source → JSON Schema → Go**: ~80% of the win with no new toolchain, but
  the team chose the more rigorous bidirectional codegen.
- **Drift-detection tests only, no codegen**: catches drift but leaves the duplication —
  still two places to edit per change.
- **gRPC service mesh**: wrong topology (one-shot Jobs, not services).
- **Collapse the Python/Go split**: rejected by invariant — detectors must be
  deterministic LLM-free Go; the agent plane needs Python/LLM tooling.

## Per-seam plan

| # | Contract | Today (two definitions) | Single source | Drift caught by |
|---|---|---|---|---|
| 1 | Messages (`IncidentRequest`/`IncidentResult`, nested) | `sdo/contracts/models.py` + hand-written Go structs | `.proto` → Go + Python | round-trip encode/decode test |
| 2 | Telemetry record (`detector-firings.jsonl`, `detector_timeline`) | Go emitter + Python reader | `.proto` (file stays newline-JSON) | round-trip test |
| 3 | Controller config | launcher argv flags + Go flag registration | **`ControllerConfig` message** the launcher populates, the controller reads | type is shared; no flag list to mirror |
| 4 | Validator image ↔ schema | image tag convention + SDK schema version | schema version/digest pinned into the image identity | startup check: image schema == artifacts schema |

(#1 and the manifest/`links` schema collapse into one source — a manifest schema *is* a
contract schema.)

### Seam 3 is a deletion, not a sync

The launcher-sync requirement is itself the smell. Rather than generate both sides of the
flag list, the controller **owns a typed `ControllerConfig` message**; the launcher
produces one instance (serialized once, mounted), the controller parses that one object.
One schema, one producer, one consumer, zero per-flag mirroring. This also clarifies
ownership: the launcher owns the *deployment shape* (manifests, RBAC, PVC, namespaces);
the controller owns its own *runtime config*.

## Out of scope

- No gRPC service mesh; no binary wire encoding.
- `.sdo/` git memory stays human-authorable YAML/MD (proto validates its structured
  subset, never replaces it).
- Detector Go *source* (code, not data) is untouched.
- JSONL log *formats* stay greppable / torn-line-repairable; only their record schema is
  proto-governed.
- The timing/state-machine and private-snapshot bug classes are separate robustness
  rungs, not this track.

## Stages

0. **Toolchain scaffold** — install `buf` + Go/Python plugins; add `proto/`, `buf.yaml`,
   `buf.gen.yaml`; wire `buf generate` + `buf lint`/`buf breaking` into the check scripts.
1. **Vertical slice** — one small message end-to-end (proto → Go + Python → protojson →
   round-trip test), extending `controller/runtime/contract_golden_test.go`. De-risks the
   pipeline before mass migration.
2. **Full message migration** — messages + telemetry schema, protovalidate for invariants.
   Pydantic kept only as the LLM output-shaping schema at the responder edge, converted to
   proto at the seam. (Cutover wants a quiet window; it touches `sdo/contracts` + the
   controller.)
3. **`ControllerConfig`** — replace the flag boundary with the typed config object; the
   `launch_contract.py` flag preflight becomes redundant.
4. **Validator image ↔ schema binding** — stale image fails loud at startup.

## Validation

- Round-trip test per message (Go-encode → Python-decode → re-encode → assert identical).
- `buf lint` + `buf breaking` in CI.
- Each previously-fixed drift bug becomes a fixture (the ratchet).
- Standard gates: `scripts/format_code.sh`, `scripts/check_errors.sh`, Go tests, `uv run pytest`.
