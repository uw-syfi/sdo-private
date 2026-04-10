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
