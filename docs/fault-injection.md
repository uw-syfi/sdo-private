# Benchmark fault injection

Fault injection is retained only as part of SREGym benchmark execution. It is not a production SDO lifecycle phase and production packages must not consume fault labels, benchmark verdicts, or injection metadata.

The external benchmark implementation and its authoritative documentation live in the `third_party/sregym/` submodule. All first-party integration code lives under `benchmarks/sregym/`: experiment definitions are in `experiments/`, harness orchestration in `runner/`, conductor and submission contracts in `protocol/`, and the production bridge in `adapter/`.

When evaluating the production adapter, preserve this boundary:

- the benchmark may prepare a fault and expose normal Kubernetes observations;
- SDO detectors and responders may use only those ordinary observations and repository memory;
- submission bridges and receipts remain under `benchmarks/sregym/adapter/`;
- benchmark conductor, fault-selection, and submission protocols remain under `benchmarks/sregym/protocol/` and `runner/`;
- no hidden fault identity or judge verdict may enter lifecycle provenance, detectors, controller requests, or responder prompts.

The legacy Crucible agent under `benchmarks/sregym/agents/` may consume benchmark verdicts for its own historical recovery experiments. That behavior is benchmark-only and must not be treated as SDO agent logic or imported into production code.

Use the benchmark's own CLI and documentation for available faults and task selection. The removed standalone Compose fault injector is not a supported repository feature.
