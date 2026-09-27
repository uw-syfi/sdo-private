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

## Grading

- Every experiment config uses the SREGym judge `judge_model_id = "codex-gpt-6-luna"`. The Codex CLI judge backend (`third_party/sregym/llm_backend/codex_cli_backend.py`) runs it at `xhigh` reasoning by default (`JUDGE_REASONING_EFFORT`). Use the same judge for every arm of a comparison.

## Timing metrics

Report timing without judge time, the same way for every arm (`benchmarks.sregym.analysis.incident_cost` computes all of these):

- **Time to diagnosis (TTD):** `diagnosis_submitted_at - fault_injected_at`.
- **Time to mitigation (TTM, the headline):** `mitigation_submitted_at - fault_injected_at`, minus the diagnosis-grading wait `fault_injected_at + TTL - diagnosis_submitted_at`, and never less than when the agent's last state-changing command before its mitigation POST completed, taken from the exported Codex rollout. The floor matters because an agent that keeps repairing while the judge grades its diagnosis would otherwise be undercounted. TTM is unknown when the CSV lacks `diagnosis_submitted_at` or `TTL`.
- **Supplementary only:** the raw `mitigation_submitted_at - fault_injected_at` (`raw_incl_judge_s`, which includes diagnosis grading), and the CSV's `TTM` column (which also includes the mitigation oracle). Do not present either as a headline number.
- None of these times includes SDO reflection, lifecycle or controller installation.
