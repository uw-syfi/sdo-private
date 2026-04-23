# Feature Flags

Feature flags are split across two `sds.toml` sections:

- **`[operator.phase]`** — phase toggles that skip entire operator pipeline stages
- **`[features]`** — cross-cutting capability flags that apply across runtimes

```toml
[operator.phase]
code_analysis = true              # default: true
fix_summary_consolidation = true  # default: true
health_monitoring = true          # default: true

[features]
git_integration = false           # default: false
```

---

## Phase Toggles (`[operator.phase]`)

### `code_analysis`

**Default:** `true`

Runs `CodeAnalyzerAgent` before the deployment phase. The agent reads the target codebase and writes `.sds/code_analysis.md`, which is then injected into the deployer prompt to provide app-specific context.

Disable to skip the analysis step and reduce token usage:

```toml
[operator.phase]
code_analysis = false
```

---

### `fix_summary_consolidation`

**Default:** `true`

Enables the hypothesis-driven deployment progress document (`.sds/deployment_progress.md`). When enabled, the Error Fixer writes a hypothesis (root cause, fix plan, success criteria) before making any edits, and the Health Judge reads and validates those criteria after each attempt, recording confirmed/refuted outcomes. The accumulated history prevents re-trying approaches that have already been disproved.

Disable to skip the progress document entirely:

```toml
[operator.phase]
fix_summary_consolidation = false
```

---

### `health_monitoring`

**Default:** `true`

Runs periodic health checks after successful deployment. Disable to skip all post-deployment monitoring:

```toml
[operator.phase]
health_monitoring = false
```

---

## Capability Flags (`[features]`)

### `git_integration`

**Default:** `false`

Exposes the `make_change_on_remote_copy` tool to the agent. When enabled, the agent can push branches and open GitLab MRs from within the operator loop.

Requires GitLab credentials to be configured. **Opt-in only** — do not enable in local dev environments where pushing to a remote repository is undesirable.

```toml
[features]
git_integration = true
```

---

## SREGym Runner Settings (`[runner]`)

Runner settings live in experiment TOML files (e.g. `sregym_agents/experiments/default.toml`) under `[runner]` and are passed as CLI arguments to `bench/sregym/main.py` by `sregym_agents/run_sregym.py`.

### `agent`

**Default:** `"crucible"`

Name of the registered agent to run (from `sregym_agents/agents.yaml`).

---

### `model`

**Default:** `"google-vertex:gemini-2.5-flash"`

Model ID for the agent. Can be overridden at launch time with the `MODEL` environment variable.

---

### `parallel`

**Default:** `4`

Number of problems to run concurrently. Can be overridden with the `PARALLEL` environment variable.

---

### `app_filter`

**Default:** `""` (disabled)

Restrict benchmark problems to a single application. This narrows whatever problem source is otherwise active: the default tasklist, a custom `tasklist`, inline `problems`, `spec_names`, or variant/sequence sampling.

Accepted values use the same app aliases as `bench/sregym/main.py`, for example:

- `hotel_reservation`
- `social_network`
- `astronomy_shop`
- `fleet_cast`

```toml
[runner]
app_filter = "hotel_reservation"
```

---

### `enable_summary`

**Default:** `true`

Enables knowledge base (KB) mode. Passes `--enable-summary` to `main.py`, which activates the KB worker and per-run summarization pipeline. Set to `false` to run without any KB.

---

### `no_inject_summary`

**Default:** `true`

Update the KB after each run but do not inject it into the agent's context before the run. Useful when bootstrapping a fresh KB. Set to `false` to inject existing KB content at the start of each run.

---

### `repeat`

**Default:** `1`

Run each problem N times. Useful for measuring variance across repeated runs.

---

### `sequence_len`

**Default:** `0` (disabled)

Draw a random sequence of N problems from the full problem set and run them in order. Set to `0` to disable. Cannot be used with variants mode. `sequence_seed` controls the RNG.

---

### `sequence_seed`

**Default:** `42`

RNG seed for `sequence_len` sampling.

---

### Problem selection (mutually exclusive)

Three mutually exclusive ways to specify which problems to run (all are also mutually exclusive with `[runner.variants]`):

- **`tasklist`** — Named pre-built set (maps to `bench/sregym/sregym/conductor/tasklist.<name>.yml`) or path to a custom YAML file.
- **`problems`** — Inline list of specific problem IDs (all get diagnosis + mitigation stages).
- **`spec_names`** — List of spec/category prefixes; runs all problems whose ID equals or starts with `<spec>_`.

```toml
[runner]
# Option 1: named tasklist
tasklist = "count_train"

# Option 2: specific problems
problems = ["faulty_image_correlated", "incorrect_port_assignment"]

# Option 3: by spec prefix
spec_names = ["service_dns_resolution_failure"]
```

---

## SREGym Variant Mode (`[runner.variants]`)

Variant mode draws problems by sampling across fault classes rather than using a fixed tasklist. Mutually exclusive with `tasklist`, `problems`, and `spec_names`.

### `enabled`

**Default:** `false`

Enable variant sampling mode.

---

### `count`

**Default:** `0`

Total number of variants to run. Set to `0` for no global cap (adaptive mode only; otherwise required).

---

### `offset`

**Default:** `0`

Starting offset into the variant list — skip the first N variants. Useful for resuming mid-sequence without repetition.

---

### `seed`

**Default:** `42`

RNG seed for variant sampling.

---

### `order`

**Default:** `"round_robin"`

How to schedule variants across fault classes. One of:

- `flat` — draw variants in a flat random order ignoring class boundaries.
- `round_robin` — cycle through classes one problem at a time.
- `grouped` — exhaust each class up to `max_per_class` before moving to the next.
- `adaptive` — keep feeding a class until the agent reliably solves it (`consec_solves_to_stop` consecutive full solves), then advance. Requires `max_per_class` and `consec_solves_to_stop`.

---

### `max_per_class`

**Default:** none (required for `grouped` and `adaptive`)

Hard cap on problems per fault class. For `adaptive`, this is the absolute ceiling before a class is dropped regardless of solve rate.

---

### `consec_solves_to_stop`

**Default:** none (required for `adaptive`)

Stop feeding a fault class after N consecutive full solves (diagnosis + mitigation both succeed). Used only with `order = "adaptive"`.

---

### `spec_names` (variants)

**Default:** `[]` (all classes)

Restrict variant sampling to specific fault class specs. Requires `enabled = true`.

```toml
[runner.variants]
enabled = true
order = "adaptive"
count = 50
seed = 42
max_per_class = 20
consec_solves_to_stop = 3
spec_names = ["wrong_dns_policy"]
```

---

## SREGym Environment (`[runner.env]`)

These values are injected as environment variables into the `main.py` worker process.

### `judge_model_id`

**Default:** `""` (inherits `MODEL`)

Model ID for the judge agent (`JUDGE_MODEL_ID` env var). Allows using a different, often more capable model for judgment than for the SRE agent.

```toml
[runner.env]
judge_model_id = "vertex-ai-gemini-2.5-pro"
```

---

### `crucible_seed_kb_dir`

**Default:** `""` (no seed)

Path to a pre-built KB directory to seed into a fresh experiment. The launcher copies this directory into the new experiment's KB dir before any worker starts, letting the agent begin with accumulated knowledge.

```toml
[runner.env]
crucible_seed_kb_dir = "/path/to/bench/sregym/logs/20260331_012038_crucible/kb"
```

---

### `worker_cpu_limit`

**Default:** `""` (no limit)

CPU limit string passed to each worker container (`SREGYM_WORKER_CPU_LIMIT` env var). Useful for constraining resource usage in multi-tenant environments.

```toml
[runner.env]
worker_cpu_limit = "16"
```

---

### `submit_done_returns_feedback`

**Default:** `false`

Controls whether autonomous-mode `submit_done()` returns rich grading
feedback (`SREGYM_SUBMIT_DONE_RETURNS_FEEDBACK` env var). When `false`,
the agent only gets a neutral completion payload with timing fields and the
submission count. When `true`, `submit_done()` also includes diagnosis and
mitigation verdict details, matched-candidate reasoning, ground truth, and
the submitted diagnoses.

```toml
[runner.env]
submit_done_returns_feedback = true
```

---

## SREGym Pipeline Config (`[[stages]]`)

A pipeline TOML uses `[[stages]]` instead of `[runner]` to chain multiple experiments automatically, passing each stage's KB as the seed for the next. Detected by the presence of a `stages` key; handled transparently by `run_sregym.py`.

```toml
[pipeline]
name = "my-pipeline"

[defaults]
# shared [runner] settings for all stages
agent = "crucible"
model = "google-vertex:gemini-2.5-flash"
parallel = 4

[[stages]]
name = "bootstrap"
chain_kb = false   # no prior KB to chain from; default is true

[[stages]]
name = "refine"
chain_kb = true    # seeds from bootstrap stage's KB output
```

Each stage entry supports:
- **`name`** — human-readable label for logs and directory naming.
- **`chain_kb`** (**default:** `true`) — automatically set `crucible_seed_kb_dir` to the previous stage's KB output directory. Set to `false` for independent stages that should not inherit a KB.
- **`[stages.runner]`** — per-stage overrides (deep-merged over `[defaults]`).

---

## Crucible Agent Flags (`[agent.crucible]`)

Crucible flags live in experiment TOML files (e.g. `sregym_agents/experiments/default.toml`) under the `[agent.crucible]` section and map to `CrucibleConfig` fields.

### `enable_judge`

**Default:** `true`

Runs the judge agent after each SRE agent submission. The judge independently investigates the cluster, reviews the agent's hypothesis, and either approves it (triggering a benchmark submission) or rejects it (sending the SRE agent back for another iteration).

Disable to submit the SRE agent's answer directly to the benchmark without judge review — useful for ablation experiments or faster local iteration.

```toml
[agent.crucible]
enable_judge = false
```

---

### `enable_ltm_retrieval`

**Default:** `false`

When enabled, the SRE agent retrieves relevant KB content at runtime via the `search_prior_incidents` / `search_prior_mitigations` tools rather than receiving the full KB summary injected up-front in the prompt. The judge always receives the full summary regardless of this flag.

Also gates whether `incidents_dir`, `playbooks_dir`, and `mitigation_playbooks_dir` are wired into the SRE agent's deps. Required by `enable_ltm_verified_direct_submit` and `enable_playbook_shortcut`.

```toml
[agent.crucible]
enable_ltm_retrieval = true
```

---

### `enable_ltm_verified_direct_submit`

**Default:** `false`

Enables the LTM verification short-circuit for diagnosis: if the SRE agent's internal verification subagents confirm a KB hypothesis, the orchestrator raises `LTMShortCircuit`, skipping the rest of the SRE agent loop and the judge, and submits the confirmed candidates directly to the benchmark.

Requires `enable_ltm_retrieval = true`.

```toml
[agent.crucible]
enable_ltm_verified_direct_submit = true
```

---

### `include_benchmark_results`

**Default:** `false`

Enables recovery agents after a failed stage. On a failed diagnosis, a recovery diagnosis agent receives the benchmark's ground-truth reasoning and investigates the cluster to build a validated causal chain — its output is stored in the KB instead of the original wrong answer. On a failed mitigation, a recovery mitigation agent reflects on why the fix failed. Also passes benchmark result data into the KB summarization prompts.

```toml
[agent.crucible]
include_benchmark_results = true
```

---

### `enable_reflection`

**Default:** `true`

After each KB update, runs the reflector to distill learned rules ("priors") from stage outputs into the knowledge base (e.g., triage priors, arbitration priors). Has a legacy alias `enable_heuristic_refinement` that is still accepted.

Disable to skip reflection and reduce post-run LLM calls.

```toml
[agent.crucible]
enable_reflection = false
```

---

### `enable_playbooks`

**Default:** `false`

Enables the playbook lifecycle in the KB: after each session the KB synthesizes, refines, and stores structured diagnosis and mitigation playbooks. Also gates whether the `playbooks/` and `mitigation_playbooks/` directories are injected into the experiment environment and made available to the SRE agent via `search_prior_mitigations`.

Requires KB mode (`--kb-dir`). Required by `enable_playbook_shortcut`.

```toml
[agent.crucible]
enable_playbooks = true
```

---

### `enable_playbook_shortcut`

**Default:** `false`

Before running the full SRE mitigation agent, tries to execute a matching mitigation playbook directly. The slug is resolved from the diagnosis stage via `matched_candidate_index` in the benchmark oracle, cross-referenced with slugs threaded from the `LTMShortCircuit` signal. If the playbook applies successfully, the result is submitted directly to the benchmark — bypassing the SRE agent entirely. Falls back to the normal mitigation loop if no playbook matches or execution fails.

Requires `enable_ltm_retrieval = true` and `enable_playbooks = true`.

```toml
[agent.crucible]
enable_playbook_shortcut = true
```

---

### `recovery_phase2_enabled`

**Default:** `false`

Runs a second recovery phase after the recovery diagnosis agent completes. Reads both the primary and recovery agent message histories and distills a structured `RecoveryReflection` (what the agent missed, what evidence it ignored, what the correct reasoning path was). This reflection is passed to the KB update for richer prior distillation.

Requires `include_benchmark_results = true`.

```toml
[agent.crucible]
recovery_phase2_enabled = true
```

---

### `include_incident_files`

**Default:** `true`

Controls whether historical incident files are copied from the KB into the experiment environment before the run. When `per_app = true` (default), the most recent incident files for the current app are injected; when `per_app = false`, files from all apps are included. Also controls whether the current session's incident file is written back to the KB after the run.

Disable to reduce context size when incident history is not useful.

```toml
[agent.crucible]
include_incident_files = false
```

---

### `per_app`

**Default:** `true`

When `true`, the KB uses per-application subdirectories for the summary and incident files (e.g. `kb/hotelReservation/summary.md`, `kb/hotelReservation/incidents/`). When `false`, a flat shared summary and a cross-app incidents directory are used instead, letting the agent draw on experience from other applications.

```toml
[agent.crucible]
per_app = false
```

---

## Crucible Tuning Parameters (`[agent.crucible]`)

These are numeric settings in `CrucibleConfig`, not boolean feature flags.

### `prompt_version`

**Required** (no default)

Selects the prompt template directory under `sregym_agents/crucible/configs/prompts/` (e.g. `"v2"`, `"v3"`). Must be set in the experiment TOML or passed via `--prompt-version` on the command line.

```toml
[agent.crucible]
prompt_version = "v3"
```

---

### `max_diagnosis_iterations`

**Default:** `5`

Maximum number of SRE-agent → judge loop iterations for the diagnosis stage before giving up.

---

### `max_mitigation_iterations`

**Default:** `5`

Maximum number of SRE-agent → judge loop iterations for the mitigation stage before giving up.

---

### `wait_stage_timeout`

**Default:** `300` (seconds)

How long to wait for the benchmark conductor to transition from diagnosis to mitigation stage before proceeding anyway.

---

### `stage_timeout`

**Default:** `900` (seconds, 15 minutes)

Wall-clock time limit for a single diagnosis or mitigation stage. If elapsed time exceeds this before an iteration starts, the stage is aborted and marked as failed.
