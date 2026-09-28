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

- `deployer`: session ID, source commit, topology fingerprint, resource inventory, and architecture summary. The model returns only the commit, fingerprint, and summary; the controller attaches the resource inventory deterministically from tracked manifests, so the inventory is a controller fact rather than model output.
- `active_topology` (when supplied by the deployment adapter): sorted kind/name references for the deployed source variant and required non-optional ConfigMap dependencies.
- `health_judge`: the accepted final detector artifact.
- `health_judge_rounds`: ordered structured attempts with distinct session IDs, round numbers, objective digest, covered resources, source, and tests.
- `validation` (when the validator exposes an immutable identity): the diagnostics-tree digest, immutable validator identity, and `sdo.lifecycle-validation/v1` attestation schema. An exact match permits validation reuse without recompilation; a missing or mismatched field requires fresh isolated validation.

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
- responder backend/model and transport-reported usage (`llm_calls`, `input_tokens`, `cache_read_input_tokens`, `cache_write_input_tokens`, `cache_write_1h_input_tokens`, `uncached_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `model_requests`, and `total_cost_usd` when the provider reports it). `cached_input_tokens` is the deprecated alias of cache reads; a record without `cache_read_input_tokens` predates agentshim 0.7, and on Claude its `cached_input_tokens` also counted cache writes (subtract `cache_write_input_tokens` to get reads);
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

Ledger fields can include proposal processing state; optional proposal commit; mandatory outcome, reflection, and validator-evidence commits; responder session ID; accepted detector paths; closure and acknowledgement state; network-policy canaries; topology fingerprints; and controller-update rollout records. `reflection_attempts` counts reflection turns; `reflection_fresh_retry_attempts` (default 0 in older ledgers) counts the subset that retried a validator-rejected proposal in a fresh session instead of resuming the responder session. `reflection_session_mode` (null in older ledgers and when no LLM reflection ran) records how the first attempt ran: `resume` (the responder session) or `fresh` (a new session given a bounded incident brief of the closure findings and evidence, the responder's structured result, its shell commands from `responder-turns.jsonl`, and relevant memory excerpts; under about 8K tokens). It is fixed when the first LLM attempt starts, so a restarted broker keeps it. The rejected proposal's diff is kept beside the ledger as `<sha256(incident_id)>.reflection-rejected.diff`. `reflection_skipped_reason` (null in older ledgers) is set when the broker recorded a deterministic no-op reflection without an LLM turn: a repeated exact-match success where the warm rule held (a prior verified outcome matched by `exact-fingerprint` and surfaced a playbook listed in the `possiblePlaybooks` of a registered responder-owned incident detector, fired or not), the responder applied that playbook, every repair action and verification passed, the controller verified health, and that detector was not firing in its latest post-response evaluation (no post-response evaluation is accepted). The reason names the prior incident(s), each playbook with its detector, whether the detector had fired at dispatch, how the playbook surfaced (`active-finding`, `surfaced`, `prior-outcome-applied`, `detector-learned-from-exact-prior`), and each detector's post-response state (`clear after the response` or `not evaluated after the response`). Such a ledger has `reflection_attempts=0`, empty `reflection_usage`, `reflection_learning_decision=no_change` with the same text as `reflection_no_change_reason`, an empty attributable reflection commit, and no accepted detector paths or controller rollout. The closure's `incident_detector_states` (absent in older ledgers) holds the latest post-response evaluation of each non-health detector that raised a finding; it is learning evidence, not a closure gate. Under `recorded-actions`, a missing proposal commit is valid only when the result and outcome contain a successful structured repair action. Require incident IDs and commit hashes to agree with the receipt and outcome.

## Strict production receipt

Path in an SREGym problem run: `agent/sdo_production_receipt_strict.json`

The current receipt schema is `sdo.production-receipt/v1`. It summarizes durable evidence including:

- incident, namespace, controller/responder/validator images;
- production job dispatch and responder-job correlation;
- repair policy and structured repair actions; either a proposal commit or, under `recorded-actions`, at least one successful action;
- outcome, reflection, and validator-evidence commits;
- responder usage (`usage`) and reflection usage summed over reflection attempts (`reflection_usage`, from the broker ledger; it includes `model_requests` when the provider exposes it), `reflection_attempts`, `reflection_fresh_retry_attempts`, and `reflection_skipped_reason` (non-null for a deterministic no-op reflection that ran no LLM turn), plus phase timings that separate operational recovery from post-recovery learning and receipt work;
- compact memory-reuse evidence: candidate count, match reasons, applied-playbook count, and warm-path status;
- driver timings for conductor readiness, inventory/lifecycle work, production runtime, and benchmark submission, plus whether lifecycle memory was reused; `incident_resolution_seconds` is strictly detection through independently verified health, while the receipt lists pre-incident lifecycle and post-recovery learning as excluded time;
- `fault_injection_deferred` and `fault_gate_timings_seconds` (`controller_baseline_wait`, `fault_injection_request`) when the conductor deferred injection until the SDO controller reported an all-clear evaluation; in that mode lifecycle and controller install happen before the fault and sit outside TTM;
- whether executable detector validation ran; unchanged diagnostics may skip the Kubernetes validator and carry `validator_skipped_reason=unchanged-diagnostics` with no fresh canaries;
- same-session reflection and independent verification; `same_session_reflection` means the first reflection attempt resumed the responder session (it stays true for a deterministic no-op reflection, which still records a reflection commit; check `reflection_skipped_reason`), while any retry after a validation rejection runs fresh (see `reflection_fresh_retry_attempts`). `reflection_session_mode` (absent in older receipts) is informational: `resume` (default) or `fresh`, the opt-in mode (`agent_config.sdo_codex.reflection_session = "fresh"`, broker flag `--reflection-session fresh`) where the first attempt starts a new session from a broker-built incident brief instead of the responder transcript; it is null when no LLM reflection turn ran. A `fresh` receipt must carry `same_session_reflection=false`, and the strict validator accepts that only for `fresh`. Compare `reflection_usage` across modes for the A/B token cost; a fresh first attempt does not count toward `reflection_fresh_retry_attempts`;
- `runtime_artifacts`: `{directory, error}` for the pod-side runtime evidence exported beside the receipt (see below); a non-null `error` means the export failed, not the run;
- final detector clearing (`detector_clear`, health detectors when any exist), the post-response `incident_detector_states`, and network-policy canaries;
- acknowledgement, cleanup, and remaining worktrees;
- accepted detector paths and any correlated controller-update rollout;
- lifecycle provenance and topology freshness.

Use `benchmarks/sregym/protocol/receipt.py` as the schema source of truth. A malformed or incomplete receipt is a failed production-evidence chain even if the benchmark process exits zero.

### Persistent-controller runs

With `persistent_controller = true` (under `agent_config.sdo_codex`) one controller pod serves every problem of an application across the pipeline. It lives in `<application-namespace>-sdo` (receipt `controller_namespace`); the application namespace only receives Roles/RoleBindings. Differences from per-problem runs:

- At the end of a problem, once the controller has verified health, the adapter writes `agent/sdo_incident_resolution.json` (`sdo.sregym-incident-resolution/v1`): `incident_id`, `incident_resolution_seconds` (detected to verified health) with `resolution_phase_timings_seconds`, `confirmed_root_causes`, `repair_actions`, `persistent_controller` (`control_namespace`, `controller_pod_uid`, `controller_pod_name`, `installed_this_stage`, `installed_by_stage`, `lifecycle_revalidation_skipped`, `maintenance_generation`, `stage_label`), `pre_injection_costs_seconds` (`previous_incident_reflection_drain`, `inventory_and_lifecycle`, `controller_install_or_reuse`, `controller_baseline_wait`), `reflection_drain_seconds`, gate timings, and `driver_phase_timings_seconds` (`reflection_drain`, `inventory_and_lifecycle`, `controller_install_or_reuse`, `fault_gate`, `injection_to_verified_recovery`, `pause_for_redeploy`, `conductor_wait`, `benchmark_submission`), `responder_session_id`, and `stage_end_runtime_artifacts` (`directory`, `error`, `scope`). Reflection is not finished yet. `incident_cost` falls back to this record (incident, responder session, resolution time) for a stage with no strict receipt, so a stopped pipeline still reports that stage's responder tokens; its reflection tokens stay unknown. After verified health and the pause, the adapter also snapshots the controller PVC's `sdo_runtime/` evidence (usage logs, agent transcripts, so the responder's tokens) into the problem's directory; a failed snapshot is recorded in `stage_end_runtime_artifacts.error` and never fails the problem. It may hold a partial reflection transcript; the drained evidence below supersedes it.
- The strict receipt of problem N is written later, by problem N+1 before its fault injection or by pipeline teardown, after the incident is acknowledged and the supervised controller relaunched (learned detectors rolled out). It merges the resolution fields and adds `reflection_drain` (`drained_by`, `waited_seconds`). `phase_timings_seconds.post_recovery_learning_and_receipt` therefore spans the inter-problem redeploy; use `reflection_usage` and the reflection turns for reflection cost, never the receipt's recording time.
- SREGym publishes a problem's staging tree when the problem ends, before the drain. Problem N+1 publishes problem N's drained receipt and evidence right after its drain, before its own fault injection, so a pipeline stopped during problem N+1 keeps problem N's strict receipt and tokens. Pipeline teardown (`python -m benchmarks.sregym.adapter.persistent teardown --state ... --publish-root <pipeline>`) copies each drained strict receipt, the drain-time controller log, and the drain-time `sdo_runtime/` evidence (usage logs, agent transcripts) into the published run directory, matched by the `incident_id` of its `sdo_incident_resolution.json`, with the opaque artifact id replaced by the problem id. A receipt that fails production validation (for example `completed=false` because the responder reported `status: failed`) is kept as `sdo_rejected_production_receipt.json` (`validation_error`, `receipt`) instead of a strict receipt, and the pipeline fails only the stage that owns it.
- The same pod UID across problems proves reuse; `installed_this_stage=false` and `lifecycle_revalidation_skipped=true` mark a reused controller.
- `lifecycle_validation` (in the resolution and in one-shot SDO receipts) tells a cached lifecycle from a freshly validated one: `{"cache": "disabled", "source": null}` unless `[runner.env] lifecycle_validation_cache = true`. With the cache enabled, `source` is `validator` (the detector validator ran and its verdict was stored), `validation-cache` (a verdict stored by an earlier run for the same validator image identity and `.sdo/diagnostics` digest was replayed; the workspace commit is `sdo: attest lifecycle memory from the shared validation cache` and the provenance attestation has `attested_by: validation-cache`), `workspace-attestation` (the workspace already carried a matching attestation), or `null` (a fresh lifecycle ran, or a reused controller skipped revalidation). Compare `pre_injection_costs_seconds.inventory_and_lifecycle` only between runs with the same `source`.
- `sdo_runtime/usage/*.jsonl` and agent transcripts come from the controller PVC and are cumulative across the pipeline's incidents; scope reflection turns to an incident by the `cwd` of its worktree (`sdo.operational_memory.incident_worktree_dirname(incident_id)`), as `benchmarks/sregym/analysis/incident_cost.py` does, and responder rollouts by `responder_session_id`.
- `controller_logs/<pod>.log` is cumulative for the single pod, so each problem's copy contains earlier problems too. Correlate by `controller_maintenance`/`maintenance_generation` markers (a resume logs `active` with the stage's generation before its full evaluation; a pause logs `paused`), `controller_closure_restart` (incident acknowledged; the Go controller exits for rollout), and `controller_supervisor: relaunch`.
- Pipeline-level `sdo_persistent_controller.json` records each application's controller, its pod UID, served stages, any incident still awaiting its drain, and `deferred_receipts` (`incident_id`, `stage_label`, `staging_dir`) used to publish drained receipts.

## SREGym result tree

Typical paths:

```text
third_party/sregym/logs/<run-or-pipeline>/
├── experiment_config.toml or pipeline_config.toml
├── run_manifest.json                       # launch provenance (runs from 2026-09-28 on)
├── pipeline_state.json                     # pipelines
├── lifecycle_seed_stage<N>/                # SDO pipeline reuse evidence
├── sdo_persistent_controller.json          # persistent-controller pipelines
└── stage_<N>_<name>/ or run root
    ├── experiment_config.toml
    ├── run_manifest.json                   # per stage, written when the stage starts
    ├── application_workspace/
    └── problem_runs/<problem-id>/
        ├── results_*.csv
        ├── agent/
        │   ├── sdo_production_receipt_strict.json
        │   ├── sdo_incident_resolution.json   # persistent-controller runs
        │   ├── sdo_rejected_production_receipt.json  # persistent runs whose drained receipt failed validation
        │   ├── sdo_turn_usage.jsonl        # host-side lifecycle turns
        │   └── sdo_runtime/                # exported from the workspace PVC
        │       ├── usage/controller-turns.jsonl   # broker + reflection turns
        │       ├── usage/responder-turns.jsonl    # responder Job turns
        │       ├── codex/sessions/YYYY/MM/DD/rollout-*-<session>.jsonl
        │       ├── claude/projects/...            # Claude provider only
        │       └── controller_logs/<pod>.log      # kubectl logs --timestamps of each sdo-controller-run pod
        └── <harness and agent logs>
```

`run_manifest.json` (`benchmarks/sregym/runner/manifest.py`, `schema_version` 1) is written by the runner at launch into the experiment directory, the pipeline directory and each stage directory. Its fields:

- `kind`: `experiment`, `pipeline` or `stage`.
- `git`: `sha`, `dirty` and `dirty_paths`, plus `submodule` with `sha`, `recorded_sha` and `dirty`.
- `images`: keyed by `<role>:<ref>`. Each entry has `id`, `repo_digests` and `id_at_preflight`; agent images also carry the probed `codex` and `agentshim` versions and `import_error`.
- `versions`: `codex_cli` (`pin`, `host`, `stock_arm`) and `agentshim` (`pin`, `host`).
- `models`: one mapping per arm or stage, from role to `provider`, `model`, `effort` and `source`. The roles are `responder`, `reflection`, `lifecycle_deployer`, `health_judge`, `baseline_agent` and `sregym_judge`.
- `judge`: `model` and `effort`.
- `kind_topology`: `clusters`, `worker_nodes` and the observed `nodes`.
- `config`: `snapshot`, `sha256`, `source` and `source_sha256`.
- `host`: `load_average`, `cpu_count` and `disk_free_bytes`.
- `codex_quota` and `lanes`.
- `preflight`: `ok`, `mode`, `waived`, `checks` and `facts`.

A resumed run keeps its first manifest and appends each resume's manifest under `resumes`. Runs from before 2026-09-28 have no manifest.

Current SREGym parallel runs instead write each run under `runs/<6-digit sequence>_<problem-id>/worker_<N>/results/`. A problem listed several times in `runner.problems` (kept in order, e.g. A B C D A B C D) and each of the `runner.repeat` independent attempts (passed as `--n-attempts`, expanded consecutively as A A B B; fresh fault injection and agent launch, not retries) gets its own sequence number, so group rows by `problem_id` across sequences rather than assuming one directory per problem.

`controller_logs/` is written by the adapter's cleanup, so it also exists after a failed controller Job; an export failure is recorded in `controller_logs/export_error.txt` instead of failing the run. Each controller evaluation line (`controller_iteration`, `findings`) carries no timestamp of its own; use the `kubectl` timestamp prefix. The broker ledger's `closure.request.detector_history` is frozen when the incident opens and compacted to the last 12 evaluations, so evaluations after dispatch appear only in these logs.

Every structured agent turn appends one JSONL record to the file named by `SDO_TURN_USAGE_LOG`: host-side lifecycle turns to `sdo_turn_usage.jsonl` in the run's agent log directory, and in-cluster turns to `/workspace/.sdo-runtime/usage/{controller,responder}-turns.jsonl` on the workspace PVC, which the adapter exports to `agent/sdo_runtime/usage/` before building the receipt. Each record has `recorded_at`, `provider`, `model`, `cwd`, `session_id`, `resumed`, `duration_seconds`, `tool_calls`, `shell_commands` (count), `shell_command_lines` (the commands in order, each capped at 2,000 chars; absent in older logs), `model_requests`, `model_requests_source`, and `usage` (`llm_calls`; agentshim's normalized token breakdown `input_tokens` (including cache reads and writes), `cache_read_input_tokens`, `cache_write_input_tokens`, `cache_write_1h_input_tokens`, `uncached_input_tokens`, `output_tokens` (including reasoning) and `reasoning_output_tokens`; the deprecated alias `cached_input_tokens` (cache reads); and `model_requests` when known). Records written before agentshim 0.7 lack `cache_read_input_tokens`, and on Claude their `cached_input_tokens` also counted cache writes. `llm_calls` is agentshim's turn count: always 1 per Codex `exec` run, but Claude's agentic turns. `model_requests` is the per-request count: for Codex, `token_count` events with usage that this turn added to its session rollout (`model_requests_source=codex-rollout-token-count`); for Claude, its reported turns (`claude-num-turns`); `null` when the rollout could not be read. Cross-check against the exported Codex rollouts, whose `event_msg`/`token_count` entries carry per-request `last_token_usage`; a resumed session's rollout also contains the earlier turns. Responder Jobs run Codex with `CODEX_HOME=/workspace/.sdo-runtime/codex`, so responder rollouts are exported under `agent/sdo_runtime/codex/sessions/` beside the reflection's (same session file when reflection resumed it).

Result CSVs may flatten stage results into fields such as `Diagnosis.success` and `Mitigation.success`. They also carry conductor wall-clock epochs `fault_injected_at`, `diagnosis_submitted_at`, and `mitigation_submitted_at` (recorded when the agent's `/submit` request arrives, before API retries or oracles) and, for agents with `defer_fault_injection`, `fault_injection_deferred_seconds`. Report timing judge-free for every arm: time to diagnosis is `diagnosis_submitted_at - fault_injected_at`, and the headline time to mitigation is `mitigation_submitted_at - fault_injected_at` minus the diagnosis-grading wait `fault_injected_at + TTL - diagnosis_submitted_at`, but never less than when the agent's last state-changing command before the mitigation POST completed (from the exported Codex rollout). The unadjusted `mitigation_submitted_at - fault_injected_at` includes judge time and is supplementary only (`raw_incl_judge_s`). The CSV's own `TTM` column additionally includes the mitigation oracle and is not a headline number. Runs with `defer_diagnosis_grading = true` carry `diagnosis_grading_deferred = true`: the diagnosis is graded by the same judge in the background while the mitigation stage is already open, so their grading wait is zero, `TTL` is measured at diagnosis submission, and the CSV's `TTM` no longer includes diagnosis judging; compare raw `TTM` only between runs with the same marker. Require explicit true values and inspect `agent_error`; missing or malformed values are not success.

For multi-stage SDO pipelines compared against stock Codex, `uv run python -m benchmarks.sregym.analysis.incident_cost <sdo-pipeline-dir> --codex <codex-dir>...` prints per stage `ttd_s` and the headline judge-free `ttm_s`; the supplementary `raw_incl_judge_s`, `no_judge_s` (raw minus the grading wait, which is zero for `diagnosis_grading_deferred = true` rows), `first_mut_s` and `last_mut_s` (first state-changing command issued, and last one completed, both from rollouts); cumulative `ttm_s`; `incident_resolution_seconds`; oracle verdicts, responder and reflection tokens (reflection kept out of resolution time), a token-breakdown table (uncached input, cache reads, cache writes and their one-hour part, output, reasoning, raw total, cost-weighted total, USD, model requests) for every SDO stage and baseline problem with the pricing table version and date it used (see "Token metrics" in `benchmarks/sregym/AGENTS.md`), receipt `memory_reuse.warm_path` beside whether the responder rollout's user prompt carried the warm-path instructions, `.sdo/` detector and playbook counts, and token break-even against the per-problem Codex mean with and without the one-time lifecycle (`sdo_turn_usage.jsonl`, or `--lifecycle-usage` for a seeded lifecycle).

## Agent trajectories

Crucible and generic CLI benchmark agents may emit JSON or JSONL trajectories under run-specific `agent/` or `trajectories/` directories. Their schema is agent-specific. Start by inspecting keys and timestamps rather than applying a historical fixed schema.

Use trajectories to explain hypotheses, commands, tool failures, and detours. Do not use them as substitutes for controller outcomes, broker commits, independent verification, or strict receipts.
