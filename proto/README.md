# SDO contract protos

Single source of truth for the structured contracts that cross SDO's seams
(controller↔responder messages, firing telemetry records, controller config).
See [`docs/seam-contracts-decisions.md`](../docs/seam-contracts-decisions.md) for the
rationale and the per-seam plan.

## Layout

```
proto/sdodev/contracts/v1alpha1/   # versioned contract messages
```

The proto package is `sdodev.contracts.v1alpha1` (not `sdo.contracts...`) so the
generated Python root does not shadow this repo's real top-level `sdo` package;
`sdo/contracts/proto.py` puts `sdo/contracts/_gen` on `sys.path` and re-exports
the messages under a stable import path.

Generated code is written to:

- Go:     `controller/contracts/gen/`  (module `sdo.dev/controller/contracts`)
- Python: `sdo/contracts/_gen/` (includes a vendored classic-runtime
  `buf/validate/validate_pb2.py`; see below)

Generated files are committed so builds and tests do not require the toolchain at
compile time; CI regenerates and asserts no diff.

## Regenerate

```bash
scripts/gen_proto.sh     # buf lint + buf generate
scripts/check_proto.sh   # buf lint + buf breaking + assert committed codegen matches
```

Toolchain (installed via `go install`, so they land in `~/go/bin` — add it to `PATH`):

- `buf` — `go install github.com/bufbuild/buf/cmd/buf@latest`
- `protoc-gen-go` — `go install google.golang.org/protobuf/cmd/protoc-gen-go@latest`
- Python: the remote buf plugins `buf.build/protocolbuffers/python` and `.../pyi`,
  **pinned to `v33.4`** in `buf.gen.yaml`. That pin matches the `protobuf==6.33.4`
  runtime locked in `uv.lock`; the unpinned (latest) plugin emits gencode newer than
  that runtime, which then refuses to load it. Bump the two pins together with the
  protobuf runtime. The buf registry is reachable in this environment, so no local
  `protoc --python_out` fallback is needed; if it ever becomes unreachable, swap the
  two remote plugins for `local: protoc` with `--python_out`/`--pyi_out`.

Generated Go lives in its own module `sdo.dev/controller/contracts` (`controller/contracts/`,
go_package `.../gen/...`); consumers add a `require` + relative `replace` for it.

**Follow-up (not this refactor):** the `contracts` module needs `google.golang.org/protobuf`
v1.36.x (the gencode runtime), while `sdk`/`core`/`runtime` still pin v1.33.0. Consumers
therefore carry a `require` + relative `replace` on `contracts`. The clean endgame is to
converge all controller modules onto protobuf v1.36.x so the version skew (and eventually
the `replace` directives, once the module publishes) can be dropped. Deferred to avoid a
dependency-bump ripple mid-migration.

## Rules

- These messages are serialized as **protojson**, never binary, on the wire.
- Semantic invariants (ordered timestamps, token arithmetic, unique IDs) are expressed
  with **protovalidate** (CEL), so validation is single-source too. The CEL is checked
  on the Go side with `buf.build/go/protovalidate` (see
  `controller/runtime/contract_conformance_test.go`). The one piece CEL cannot do —
  the `UsageMetrics` cache-read alias *normalization* (a mutation) — stays a thin
  hand step at the seam, noted in `messages.proto`.
- The Python gencode imports `buf.validate` for those field options. `protovalidate`
  on PyPI (v2+) ships only the newer `protobuf-py` runtime form, incompatible with the
  classic `google.protobuf` runtime this repo uses, so `scripts/gen_proto.sh` vendors
  the classic `buf/validate/validate_pb2.py` into `sdo/contracts/_gen/buf`
  (via `buf.gen.validate.yaml`). Python validation (needed only at the Stage 2b
  responder-edge conversion) will add a classic-compatible validator then.
- Do **not** add gRPC services here — SDO keeps its file/PVC/git/HTTP transports.
- `.sdo/` git memory, detector Go source, and JSONL log formats are out of scope.
