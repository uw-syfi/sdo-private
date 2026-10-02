# SDO contract protos

Single source of truth for the structured contracts that cross SDO's seams
(controller↔responder messages, firing telemetry records, controller config).
See [`docs/seam-contracts-decisions.md`](../docs/seam-contracts-decisions.md) for the
rationale and the per-seam plan.

## Layout

```
proto/sdo/contracts/v1alpha1/   # versioned contract messages
```

Generated code is written to:

- Go:     `controller/contracts/gen/`  (module `sdo.dev/controller/contracts`)
- Python: `sdo/contracts/_gen/`

Generated files are committed so builds and tests do not require the toolchain at
compile time; CI regenerates and asserts no diff.

## Regenerate

```bash
buf lint
buf generate
```

Toolchain: `buf`, `protoc-gen-go` (`go install google.golang.org/protobuf/cmd/protoc-gen-go@latest`),
and the Python plugins referenced in `buf.gen.yaml`.

## Rules

- These messages are serialized as **protojson**, never binary, on the wire.
- Semantic invariants (ordered timestamps, token arithmetic, unique IDs) are expressed
  with **protovalidate** (CEL), so validation is single-source too.
- Do **not** add gRPC services here — SDO keeps its file/PVC/git/HTTP transports.
- `.sdo/` git memory, detector Go source, and JSONL log formats are out of scope.
