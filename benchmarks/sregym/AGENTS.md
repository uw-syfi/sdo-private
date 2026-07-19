# SREGym benchmark boundary

All first-party SREGym integration code lives here. This package may depend on public production APIs under `sdo/`; production packages must never import this package or the external harness.

## Structure

- `adapter/` — translates SREGym execution into production SDO lifecycle/controller/responder APIs and persists benchmark transport evidence
- `protocol/` — benchmark conductor, HTTP, MCP submission, and strict-receipt evidence contracts
- `runner/` — experiment and pipeline configuration plus harness process orchestration
- `experiments/` — checked-in benchmark configurations
- `analysis/` — benchmark result summarization
- `agents/` — benchmark competitors that are not production SDO components
- `registry.yaml` — agent registry consumed by the benchmark runner
- `run.py` — benchmark experiment entry point

The external SREGym implementation is a separately pinned Git submodule at `third_party/sregym/`.

## Participants

- **sdo_codex** — the adapter-backed benchmark entry for the production SDO design
- **crucible** — `uv run python -m benchmarks.sregym.agents.crucible.driver`; a legacy benchmark agent retained for historical comparisons

Crucible's orchestrator, judge, benchmark-oracle recovery, and private knowledge-base formats are benchmark agent logic. They must not be imported by production SDO code or treated as the implementation of the paper's responder and repository-backed operational memory.

## Boundary rules

- Put SREGym transport, conductor polling, submissions, receipts, experiment orchestration, and result analysis in this package.
- Put reusable production lifecycle, responder, contracts, and operational-memory behavior under `sdo/`.
- Keep benchmark verdicts, oracle answers, hidden fault identity, and injection metadata out of production requests, detectors, memory, and prompts.
- Keep changes to the external harness in `third_party/sregym/`; do not copy its implementation into first-party packages.
