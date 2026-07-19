# Benchmark fault injection

Fault injection is retained only as part of SRE Gym benchmark execution. It is not a production SDO lifecycle phase and production packages must not consume fault labels, benchmark verdicts, or injection metadata.

The benchmark implementation and its authoritative documentation live in the `bench/sregym/` submodule and `sregym_agents/`. Experiment definitions are under `sregym_agents/experiments/`.

When evaluating the production adapter, preserve this boundary:

- the benchmark may prepare a fault and expose normal Kubernetes observations;
- SDO detectors and responders may use only those ordinary observations and repository memory;
- submission bridges and receipts remain under `benchmarks/sregym/adapter/`;
- no hidden fault identity or judge verdict may enter lifecycle provenance, detectors, controller requests, or responder prompts.

Use the benchmark's own CLI and documentation for available faults and task selection. The removed standalone Compose fault injector is not a supported repository feature.
