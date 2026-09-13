# SDO experiment evidence reference

Use this reference to correlate durable production evidence with SREGym artifacts. Field sets may grow; inspect the run's schema/version and repository models before assuming optional fields.

## Evidence hierarchy

For production-lifecycle claims, use this order:

1. strict production receipt;
2. controller-owned outcomes and durable runtime state;
3. broker incident ledger and attributable Git commits;
4. lifecycle provenance and validated operational memory;
5. controller, responder, validator, and harness logs;
6. agent-specific trajectories.

A benchmark result can establish the harness verdict. It cannot by itself establish memory ownership, independent verification, reflection, rollout, or cleanup.

## Lifecycle provenance

Path: `.sdo/lifecycle-provenance.yaml`

Expected sections:

- `deployer`: session ID, source commit, topology fingerprint, resource inventory, and architecture summary.
- `active_topology` (when supplied by the deployment adapter): sorted kind/name references for the deployed source variant and required non-optional ConfigMap dependencies.
- `health_judge`: the accepted final detector artifact.
- `health_judge_rounds`: ordered structured attempts with distinct session IDs, round numbers, objective digest, covered resources, source, and tests.

Correlate the deployer source commit with Git and `arch.md`. Correlate the objective digest with the exact text in `goal.md`. When `active_topology` is present, verify that `covered_resources` contains no inactive source variants. A reused lifecycle is credible only when current source topology, active topology, and detector validation still match.

## Outcome records

Path: `.sdo/outcomes.jsonl`

Each line is one controller-owned record. Important fields include:

- `incident_id`, `source_commit`, `deployed_commit`;
- detector history, findings, and final health-detector state;
- surfaced, inspected, confirmed, rejected, and applied playbooks;
- confirmed root causes;
- `classification`;
- repair and memory commits, plus structured live repair-action receipts;
- responder backend/model and transport-reported usage, including cached input tokens and provider cost when available;
- detected, dispatched, mitigated, verified, and completed timestamps.

The controller runtime ConfigMap can also contain `detector_review_required`, `detector_review_required_at`, and `detector_review_reason`. These fields mean a responder completed but independent health findings did not clear within the bounded verification window; do not interpret that state as a verified incident closure.

Useful queries:

```bash
jq -s 'length' .sdo/outcomes.jsonl
jq -s 'map({incident_id, classification, repair_commit, repair_actions, memory_commit, timestamps})' .sdo/outcomes.jsonl
jq -s 'group_by(.classification) | map({classification: .[0].classification, count: length})' .sdo/outcomes.jsonl
```

## Broker incident ledger

Broker state is stored under the repository's Git common directory, normally in an `sdo-broker/` subtree. Resolve the common directory with:

```bash
git -C <application-worktree> rev-parse --git-common-dir
```

Ledger fields can include proposal processing state; optional proposal commit; mandatory outcome, reflection, and validator-evidence commits; responder session ID; accepted detector paths; closure and acknowledgement state; network-policy canaries; topology fingerprints; and controller-update rollout records. Under `recorded-actions`, a missing proposal commit is valid only when the result and outcome contain a successful structured repair action. Require incident IDs and commit hashes to agree with the receipt and outcome.

## Strict production receipt

Path in an SREGym problem run: `agent/sdo_production_receipt_strict.json`

The current receipt schema is `sdo.production-receipt/v1`. It summarizes durable evidence including:

- incident, namespace, controller/responder/validator images;
- production job dispatch and responder-job correlation;
- repair policy and structured repair actions; either a proposal commit or, under `recorded-actions`, at least one successful action;
- outcome, reflection, and validator-evidence commits;
- responder usage plus phase timings that separate operational recovery from post-recovery learning and receipt work;
- compact memory-reuse evidence: candidate count, match reasons, applied-playbook count, and warm-path status;
- driver timings for conductor readiness, inventory/lifecycle work, production runtime, and benchmark submission, plus whether lifecycle memory was reused;
- whether executable detector validation ran; unchanged diagnostics may skip the Kubernetes validator and carry `validator_skipped_reason=unchanged-diagnostics` with no fresh canaries;
- same-session reflection and independent verification;
- final detector clearing and network-policy canaries;
- acknowledgement, cleanup, and remaining worktrees;
- accepted detector paths and any correlated controller-update rollout;
- lifecycle provenance and topology freshness.

Use `benchmarks/sregym/protocol/receipt.py` as the schema source of truth. A malformed or incomplete receipt is a failed production-evidence chain even if the benchmark process exits zero.

## SREGym result tree

Typical paths:

```text
third_party/sregym/logs/<run-or-pipeline>/
├── experiment_config.toml or pipeline_config.toml
├── pipeline_state.json                     # pipelines
├── lifecycle_seed_stage<N>/                # SDO pipeline reuse evidence
└── stage_<N>_<name>/ or run root
    ├── experiment_config.toml
    ├── application_workspace/
    └── problem_runs/<problem-id>/
        ├── results_*.csv
        ├── agent/
        │   └── sdo_production_receipt_strict.json
        └── <harness and agent logs>
```

Result CSVs may flatten stage results into fields such as `Diagnosis.success` and `Mitigation.success`. Require explicit true values and inspect `agent_error`; missing or malformed values are not success.

## Agent trajectories

Crucible and generic CLI benchmark agents may emit JSON or JSONL trajectories under run-specific `agent/` or `trajectories/` directories. Their schema is agent-specific. Start by inspecting keys and timestamps rather than applying a historical fixed schema.

Use trajectories to explain hypotheses, commands, tool failures, and detours. Do not use them as substitutes for controller outcomes, broker commits, independent verification, or strict receipts.
