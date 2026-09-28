# Harness assurance: decisions log

This log covers the SREGym harness assurance work: the launch preflight, the per-run manifest, the automatic run-validity checker, and the CI image smoke job. None of it needs an LLM or Codex quota, and no live run was launched while building it (the weekly Codex quota was at 90% on 2026-09-28; live runs wait for the 2026-10-03 reset).

Each incident listed in the brief maps to a check:

| Past incident | Caught by |
| --- | --- |
| npm `@openai/codex@latest` 0.158.0 returned 404 | preflight `codex-cli-pins` (a concrete pin that resolves on npm, for the CLI and its platform package); validity `agent-install` (`install.rc != 0` is `invalid_infra`) |
| controller image shipped an old agentshim, imports broke at run time | preflight `sdo-images` (runs `codex --version`, reads agentshim's version and smoke-imports the entry points inside each image); CI `controller image` job |
| `/mnt/data` reached 100% and crashed MongoDB pods | preflight `disk` (at least 100 GB free on the logs and Docker data disks) and the manifest's disk and load at start |
| parallel lanes shared one kubeconfig | preflight `lane-isolation`; validity `isolation-guard` and `foreign-nodes` |
| runs renamed `invalid_` or `sdobug_` by hand | `python -m benchmarks.sregym.analysis.run_validity` classifies every run; `incident_cost` excludes `invalid_infra` runs and prints why |

## Preflight

Code: `benchmarks/sregym/runner/preflight.py`. The runner calls it before `run_single_experiment` creates its directory and before `run_pipeline` creates its pipeline directory, over every stage. Standalone for the arms of a comparison: `uv run python -m benchmarks.sregym.runner.preflight <arm.toml> ...`.

- **A failed check raises `PreflightError` before anything exists on disk.** The message names each failing check, what was found and how to fix it.
  - Alternative: record the failure and let the run proceed. Rejected: the incidents above each cost a run (or a whole parallel window) that had to be discarded afterwards.
  - `SDO_PREFLIGHT=warn` is the only way past a failure. The manifest then records `preflight.waived = true`, and the validity checker classifies the run `invalid_infra`. A waived run can be useful for debugging, but it can never produce a reported number.
- **`unknown` is not a failure.** A check that cannot be evaluated offline (npm unreachable, no quota snapshot, `kind` missing) is recorded as `unknown` in the report and the manifest, and the launch proceeds. Refusing to launch on missing information would block legitimate offline development.
- **Disk: 100 GB free (10^9 bytes) on both the logs disk (`third_party/sregym/logs`) and Docker's data root.** Both were on `/mnt/data` when it filled up. The nearest existing parent of the logs path is measured, so a first run still gets checked.
- **One Codex CLI version everywhere.** `controller/Dockerfile.runtime` `ARG CODEX_VERSION` is the single pin. The preflight fails when:
  - the stock Codex arm's `third_party/sregym/agents.yaml` `agent_version` is null, `latest` or a different version;
  - the host CLI differs (the SREGym judge runs through the host's `codex exec`, so it is part of every arm's measurement);
  - SDO's agent images contain a different CLI.

  The luna decisions log already required "Codex CLI 0.157.1 in both arms"; this makes it mechanical.
- **The pin must resolve on npm, including the platform package.** SREGym's `install-codex.sh` installs `@openai/codex@V` and then `@openai/codex@V-linux-x64` under an alias. The 0.158.0 failure was a 404 at install time. `npm view` on both specs catches it before launch. It is the one networked probe. When npm is unreachable the result is `unknown`, not a failure.
- **Submodule bump to SREGym `cbb9715f` ("pin the Codex CLI to 0.157.1").** That commit was already pushed on SREGym `vic/fix/pin-codex-cli`, one commit on top of the `2933dbe4` that SDO main pins. Without it the new check (correctly) refuses every stock Codex arm, because `agents.yaml` still says `agent_version: null`.
- **Images are probed by running them, not by labels.** `docker run --rm --entrypoint codex <image> --version`, plus a `python3 -c` that reads agentshim's version and imports each image's entry points: controller `controller.builder.check_cli` and `sdo.agent_runtime.responder.broker_cli`; responder `sdo.agent_runtime.responder.job` and `libs.agent_cli`; validator `controller.builder.check_cli`. This is what caught nothing in time when an old agentshim pin broke `libs/agent_cli` imports at run time.
  - Alternative: OCI labels written at build time. Rejected: a label states what the Dockerfile intended, not what `pip` installed.
  - The probe never touches a cluster. Each container runs for about a second.
  - `sregym-agent-base:latest` is only recorded (digest): SREGym rebuilds it from the checkout when it is missing, and the Codex CLI is installed into it at container start from the pinned `agents.yaml` version.
- **Lane isolation** covers the lane's clusters, `<SREGYM_KIND_CLUSTER_PREFIX><SREGYM_WORKER_ID_OFFSET + worker>` for each of `parallel` workers:
  - **The cluster lock.** A lane whose SREGym `cluster_lock` is held by another process fails. The holder is found through `/proc/locks` by inode, so the preflight never takes the lock itself. Taking and releasing it could make a concurrently starting experiment fail its own lock.
  - **The stable kubeconfig.** With `reuse_cluster`, the kubeconfig SREGym copies into the run (`~/.cache/sregym/kubeconfigs/<cluster>.kubeconfig`) must name only `kind-<cluster>`, and its API port must match the running control plane's published port. A stale or foreign file is exactly how one lane could drive another's cluster.
  - **An explicit `KUBECONFIG`.** When it selects a kind context, the context must be one of the lane's clusters.

  The in-run crossover guard (`verify_agent_kubeconfig` in SREGym) stays the authority during the run; the preflight catches misconfiguration before any cluster work starts.
- **Model policy (user rule, 2026-09-28).**
  - Every arm is `sdo_codex` or `codex`.
  - `runner.model` is `gpt-6-luna` and `runner.reasoning_effort` is `medium`, the effort SDO pins in code. A test ties `ModelPolicy.agent_effort` to `INCIDENT_REASONING_EFFORT` and to the lifecycle backend's default.
  - Every SDO role (responder, reflection, lifecycle deployer, health judge) resolves to `codex:gpt-6-luna`. The resolution follows the driver: `[agent.sdo_codex].model`, else `MODEL_ID`, else the driver default `gpt-5.4`. A config that omits the model therefore fails loudly instead of silently running gpt-5.4.
  - The judge is `codex-gpt-6-luna`, and `JUDGE_REASONING_EFFORT` is unset or `xhigh`.
  - `SDO_RESPONDER_MODEL`, `SDO_LIFECYCLE_MODEL` and `SDO_DEPLOYMENT_MODEL` must be unset or luna. Today the driver always passes a model, so they are inert, but a later code path that honours them must not change a role's model unnoticed.
  - The policy is one frozen dataclass (`ModelPolicy`), so a later rule change is one edit.
  - Consequence: the Claude arms (`sdo_claude_haiku_*`) and legacy Crucible configs no longer launch. That is the rule.
- **Arm parity.** Across all configs given (the stages of a pipeline, or the arms passed to the CLI), these must be identical: model, reasoning effort, judge, `app_filter`, `deploy_from_source`, `worker_cpu_limit`, `kind_worker_nodes` and `agent_timeout`. Every checked-in luna comparison passes (tested).
- **Codex quota, offline.** Every Codex response writes a `token_count` event whose `rate_limits` field carries the account's window usage (`used_percent`, `window_minutes`, `resets_at`). The preflight reads the newest snapshot from the local session rollouts (`$CODEX_HOME/sessions`, default `~/.codex/sessions`). No API call and no quota are spent.
  - It fails at 85% or more used of any unexpired window. Override with `SDO_PREFLIGHT_MAX_QUOTA_USED_PERCENT`. 85 leaves room for about one comparison; the exact cost of a comparison is not known in advance, so the threshold is a judgement call.
  - A snapshot older than 6 hours or past its reset is `unknown`.
  - On 2026-09-28 the real host passes every check except this one: `primary window 90% used (resets 2026-10-03 18:19 UTC)`. That is the correct answer: live runs wait for the reset.
- **Tests never touch the host.** All probes go through a `HostProbe` protocol. The tests use a fake host that is healthy by default and break one thing at a time. The runner tests get a warn-mode fake through an autouse fixture, so orchestration tests with stub Crucible configs keep running.

## Run manifest

Code: `benchmarks/sregym/runner/manifest.py`. The runner writes `run_manifest.json` right after the config snapshot: into a single experiment's directory, into a pipeline's directory, and into each stage directory when that stage starts. The field list is in `.agents/skills/analyze-experiment/references/trajectory-schema.md`.

- **One manifest per stage, not only per pipeline.** A pipeline runs for hours. Host load, free disk and even image IDs can change between stages, and the validity checker judges each stage's problem runs on their own. The pipeline-level manifest records every stage's resolved roles at launch.
- **Image IDs are re-read when each manifest is written.** The versions and import results come from the preflight probe, and `id_at_preflight` keeps the ID at probe time. An ID that changed between the two readings means someone rebuilt an image mid-pipeline.
- **The config hash is of the snapshot written into the run** (`experiment_config.toml` or `pipeline_config.toml`), which is what a resume reads. The source TOML's path and hash are recorded too when the run was launched from a file.
- **Git state: the SDO commit, `dirty` for tracked changes only, and the dirty paths.** Untracked files are ignored, because scratch files would flag every run as dirty. The submodule entry records both its checked-out commit and the commit SDO records for it. A mismatch means the harness ran with a submodule other than the one the SDO commit pins.
- **Resolved models per role.** The resolution is the one the preflight enforces (see Model policy above), so the manifest shows the model each role actually got, not only what the TOML said.
- **Resumes append; they never overwrite.** A resumed run keeps its first manifest as the top-level document and adds each later launch under `resumes`. A resume from a different commit or image is therefore visible.
- **Writes are atomic** (write a temporary file, then rename). A killed launcher never leaves a truncated manifest for the checker to trip on.

## Run validity checker

Code: `benchmarks/sregym/analysis/run_validity.py`. Run it with `uv run python -m benchmarks.sregym.analysis.run_validity <dir>... | --logs-root <root> [--legacy] [--json]`. It exits 1 when any run is `invalid_infra`. `incident_cost` runs the same classification by default.

- **The unit is one problem run** (`runs/<seq>_<problem>/worker_<n>/results`), the unit `incident_cost` reports. A directory's verdict is not all-or-nothing. In the superseded Codex x5, only attempt 4 lost its mitigation to the harness; the other four attempts are sound.
- **Three classes, and who is to blame decides between the last two.**
  - `invalid_infra`: the measurement says nothing about the agent, so the run is excluded and the reason printed.
  - `agent_failure`: the measurement is sound and the agent did not succeed. It stays in every report as a failure. Excluding it would flatter the agent.
  - `valid`: everything passed.
- **Missing SDO evidence counts against SDO.** No valid strict receipt, a rejected receipt, or leftover `sdo.dev/responder-helper=true` objects are `agent_failure`, not infrastructure. The one exception is a persistent-controller stage whose pipeline was stopped before the next stage's drain or teardown could publish its deferred receipt. Its receipt was never due, so it is `invalid_infra` with that reason.
  - Consequence: `sdobug_20260927_200838` stage 0 is `invalid_infra` (the pipeline was stopped), as the human decision had it. `20260927_163003` (teardown failed, so no receipt was published) is `agent_failure`, which is stricter than excluding it.
- **Timing uses `incident_cost`'s own `Verdict`,** so the checker validates the TTD and TTM that get reported, never a reimplementation.
  - A passed stage without judge-free TTD (needs `fault_injected_at` and `diagnosis_submitted_at`) or TTM (also `mitigation_submitted_at` and `TTL`) is `invalid_infra`.
  - So is a TTM earlier than the TTD. It means the grading window subtracted for judge time overlaps the agent's own work, so judge time was not removed correctly.
- **Token reconciliation is exact** on input, cache-read and output tokens:
  - Codex: the per-request rollout sum against the rollout's final `total_token_usage` and the driver's `usage_metrics`.
  - SDO: the responder's receipt `usage` against its rollout's first turn and its `responder-turns.jsonl` record.

  Reasoning tokens are left out, because receipts written before agentshim 0.7 record zero. On every existing run the sources agree exactly, so any difference means an accounting bug. A run with no token evidence at all is `invalid_infra`: its cost cannot be reported.
  - Not reconciled: reflection tokens. A resumed reflection's receipt usage is session-cumulative by design, and `incident_cost` already rebuilds it from the rollout.
- **Isolation, three signals.**
  1. The in-run guard's `verified: port P reaches only <cluster>` lines in `worker.log`. A mismatch line, or a verified cluster other than the lane's, is `invalid_infra`. No verification at all is `invalid_infra` in strict mode.
  2. Kind node names of *other* lanes in what the agent's commands printed: rollout tool outputs, or Codex `--json` command output when there are no rollouts. This is the direct symptom of the shared-kubeconfig incident (`luna-w1-worker3` inside a `luna-w0` run). Only tool outputs are scanned, not prompts, because an SDO prompt may quote operational memory from another lane. JSON escapes are undone first; a raw `\nluna-w3` once parsed as the fake cluster `nluna-w3`.
  3. Legacy runs only: with no guard evidence, a run that overlapped another run's active window under the same logs root is `invalid_infra`. The contamination needed two concurrent runs. The window is the stage's config snapshot mtime to the last `worker.log` or result CSV mtime.
- **A submission the harness acknowledged but never graded is `invalid_infra`.** A missing `Diagnosis.success` or `Mitigation.success` column, when the agent's own `POST /submit` commands got `Submission received` at least as many times as needed, is the conductor bug of `dec0e283`. Without those acknowledgements, the missing verdict means the agent never submitted: `agent_failure`.
- **Codex quota exhaustion is matched only in Codex's own `--json` `error` and `turn.failed` events.** Grepping logs for "429" or "rate limit" matched image-pull timings and MongoDB log lines.
- **Helper leftovers are checked where the evidence records them.** The runtime on `vic/feat/robust-feedback-loop` (`c4a548c`) deletes labelled helpers and records `cleaned_helpers` in the closure. A failed cleanup is reported as `clean up responder helpers: ...` in the controller log. The checker fails on that line, or on a receipt `leftover_helpers` / `remaining_helpers` list. Runs from before that change record nothing, which is `not_recorded`, not a pass.
- **`--legacy` exists because every run before 2026-09-28 lacks a manifest.** In strict mode (the default) all 81 existing problem runs are `invalid_infra` for exactly that reason. `incident_cost --legacy-runs` applies the legacy policy. A waived preflight is `invalid_infra` even under `--legacy`.
- **A person's prefix (`invalid_`, `sdobug_`, ...) is shown as `manual:` and never used.** The checker must reach the same answer from evidence alone.
- **Also fixed:** `incident_cost.pipeline_stage_dirs` returned the pipeline directory itself for a stage that never started (empty `experiment_dir`). It now skips such stages.

### What it found on the existing runs (2026-09-28, `--legacy`, `third_party/sregym/logs`)

81 problem runs in 40 directories: 41 `valid`, 12 `agent_failure`, 28 `invalid_infra`.

- **Every run a person had renamed was classified `invalid_infra` from evidence alone:** `invalid_` 11/11, `sdobug_` 2/2, `stopped_` 1/1, `stopped_userdirected_` 3/3, `aborted_` 1/1.
  - For the five `invalid_` directories, the reason is the one the humans found: they ran without the isolation guard, concurrently with each other.
  - In `invalid_20260927_190901_codex`, attempts 1 and 4 also show nodes of `luna-w1` and `luna-w2` in a `luna-w0` run.
  - Attempt 4 of that run also lost its mitigation to the grading-time submission bug.
- **`superseded_prefix_20260927_195049_codex`:** attempt 4 is `invalid_infra` (the harness dropped its acknowledged mitigation). The other four attempts are `valid`. Humans superseded the whole directory because it predates the fix; the checker shows which attempt was actually affected.
- **Unlabelled but `invalid_infra` (9):**
  - three SDO attempts that never produced a result row (`20260927_090342`, `093811` and `102206`, each excluded in the luna decisions log);
  - the July and September development runs without result rows;
  - `20260913_182622_pipeline_sdo-claude-haiku-reuse`, whose CSV predates the submission timestamps, so its judge-free TTD/TTM cannot be computed.
- **`agent_failure` (12):**
  - 8 stock Codex attempts with a failed oracle;
  - SDO `20260927_163003` (both stages: teardown failed, so no strict receipt was published);
  - SDO `20260927_165936` stage 1 (receipt rejected, `completed=false`);
  - one development run from 2026-09-14.
- **`reuse1` and `fresh1`** (`20260927_182519` and `20260927_184719`, pre-guard) are `valid`: they ran alone. The luna log reached the same judgement by hand.
- **Token accounting reconciled exactly on every run that has token evidence.** No run was excluded for a token mismatch.


## Image CI and version pins

- **CI builds the images.** A new `images` job in `.github/workflows/ci.yml` runs `scripts/build_sdo_images.sh`, which builds the controller, responder, sregym-responder and validator images and smoke-runs the benchmark submission client. It then runs `python -m benchmarks.sregym.runner.preflight --image ROLE=REF ...`. That image-only preflight (`check_images`) imports each image's entry points (SDO, `libs.agent_cli`, agentshim, `controller.builder.check_cli`) and checks the Codex CLI and agentshim against the pins. It is the same probe a launch runs, so CI and the launch cannot disagree.
- **Pip installs every Python package at its `uv.lock` version, with `--no-deps`.** Before this, pip resolved the transitive packages itself at build time: pydantic-core, typing-extensions, typing-inspection, annotated-types, and httpx and requests' dependencies. `tests/unit/controller/test_image_python_pins.py` computes the lock-graph closure for Linux x86_64 on each image's interpreter (3.11 for runtime, 3.12 for validator) and fails when any package is unpinned or any `pip install` lacks `--no-deps`. Verified by a local build with unique `assure-h-*` tags: `pip check` was clean in both images and the image-only preflight passed. The tags were removed afterwards, and no caches were pruned.
- **CI pins every action to a commit SHA** (resolved from the tags with `gh api`), pins uv to 0.9.24 (the local version), and runs `uv sync --locked`. A test enforces all three.
- **Already pinned and now tested:**
  - base images by digest;
  - npm `@openai/codex` and `@anthropic-ai/claude-code` to exact versions;
  - the stock Codex arm's `agent_version` (submodule `cbb9715f`).

### Still floating (not fixed here)

- **Debian packages:** the apt packages (`git`, `python3`, `python3-pip`, `ca-certificates`) come from whatever bookworm serves on the build day, although the digest-pinned base limits the drift. Pinning them needs a snapshot.debian.org mirror.
- **npm transitive dependencies of the Codex and Claude Code CLIs:** there is no lockfile. Codex ships a self-contained platform binary, so the exposure is small.
- **SREGym agents that none of our arms use:** `claudecode`, `gemini`, `opencode` and `copilot` have `agent_version: null`, so they install the latest release. Pin one before using it in a comparison.
- **The local `sregym-agent-base:latest` tag:** SREGym builds it from the checkout and refers to it by tag. The manifest records its image ID, so a rebuild between runs is still visible.

## Phase-1 quota budget correction (2026-10)

**Decision:** `PLAN.md` (d)'s per-unit quota costs (SDO stage 0.13%, Codex
attempt 0.07%, "good to about x2") are wrong by several times over.
`benchmarks/sregym/assurance/phase1_budget.py` now budgets the phase-1
matrix in tokens, calibrated from a measured live run, instead.

**Evidence:** a live `network_policy_block` eval (lifecycle bootstrap + 3
SDO warm attempts + 3 Codex-stock attempts + 3 Codex+verify attempts, run
2026-09-28 05:04-08:30 UTC, artifacts under the run's own scratch
`.../scratchpad/np/{queue.events,ic_*.json,*.meta}`) consumed on the order
of 8-13M raw tokens total:

- `lifecycle_tokens=2,086,905` for the one cold lifecycle bootstrap
  (`ic_20260928_074550.json`);
- `sdo_tokens_with_learning` of 521,453 / 789,458 / 705,392 across three
  warm single-stage repeats (`sdo1`/`sdo2`/`sdo3`), mean 666,758;
- a Codex-stock mean of 529,706.67 tokens/attempt x3 runs = 1,589,120
  (`codex_x3b`).

Across every `QUOTA-READ` in `queue.events` over that same window, the
primary window's `used_percent` read exactly `90.0` at every checkpoint —
unmoved from the run before it to the run after it. `(delta used_percent) /
(delta tokens)` is therefore unmeasurable as a positive rate: every observed
delta is exactly zero even at multi-million-token scale.

**Resolution:** since the true rate cannot be measured directly, use a
conservative fallback instead of inventing precision the data does not
support: `POINTS_PER_TOKEN = 1e-7` (1 point per 10,000,000 tokens). That
rate is higher (assumes more quota cost per token, i.e. more cautious) than
either observed upper bound (under 1 point per ~12.9M tokens per the
correction as reported; under 1 point per ~8.4M tokens summed across every
run in the cited session scratchpad), so it will not under-budget relative
to what was actually observed. PLAN.md (d)'s relative multipliers (composite
x1.5, verify x1.3, worst case x2) are kept unchanged; only the absolute
weekly-percent conversion was wrong.

**Consequence for the launcher:** the start gate is budget-aware
(`current used_percent + planned worst-case percent <= stop_percent`, as
first landed)
instead of a fixed "`used_percent <= 50%`" rule, the launcher defaults to
the full phase-1 matrix (the 2026-10 reduced preset is an explicit
`--matrix reduced` option, not the default), and it auto-shrinks the
matrix's pipeline/attempt counts — most expensive component first, down to
a floor — if the full plan does not fit the current quota headroom. See
`benchmarks/sregym/experiments/assurance/phase1/RUNBOOK.md`'s "Quota budget
correction (2026-10)" section for the operational detail. **This worst-case
gate was replaced the same day** — see "Phase-1 gate: expected cost, not
the 2x worst case" below.

## Phase-1 gate: expected cost, not the 2x worst case (2026-10, same day)

**Decision (user):** the start gate now checks the plan's EXPECTED
(nominal) token cost against the stop line, not its 2x worst case. The
worst case is still computed and printed (`TokenBudgetEstimate.render`,
the launcher's own console output and `gate_decision.json`), but no longer
gates the start decision (`TokenBudgetEstimate.fits`,
`QuotaGate.can_start_matrix`).

**Why.** The worst-case gate, at the (already conservative) fallback rate
of 1 point per 10M tokens, auto-shrank the full phase-1 matrix down to a
single SDO pipeline under realistic quota headroom (see the correction
above: the full matrix's 2x worst case does not clear a 96% stop line even
at 90% used, because the 2x multiplier alone doubles an already-cautious
per-token rate). That is too timid given the same evidence this module
already cites: the measured `network_policy_block` eval moved 12.9M tokens
through the account without moving `used_percent` at all, so a 2x safety
factor on top of a rate already chosen to be conservative buys no real
margin, only a much smaller matrix.

**Where the actual safety comes from instead:**
- the live global hard stop, `QuotaGate.must_stop_matrix` (`--stop-percent`,
  default 96%, still one point under PLAN.md (d)'s own 97%): the matrix
  terminates every running lane the moment a real quota read crosses it,
  mid-run, regardless of what was planned;
- the per-lane 1.5x budget abort, `QuotaGate.lane_over_budget`: a lane whose
  own attributed spend runs away (a retry storm, a stuck loop) is stopped
  on its own without touching the rest of the matrix.

Both of these react to the account's actual, currently-measured usage, not
a pre-launch estimate; doubling the pre-launch estimate does not make either
of them more effective, it only makes the launcher refuse to start a matrix
it could safely have run.

**Also fixed the same day: phase 1 has no stock (no-verify) Codex arm**
(separate user decision, logged in `PLAN.md`'s own decisions log). Its sole
Codex arm is now the default, concise-verify baseline.
`phase1_budget.py`'s `FULL_MATRIX` and `REDUCED_MATRIX` presets therefore
carry one Codex component (`codex_attempts`), not two
(`codex_stock_attempts` / `codex_verify_attempts`); the estimate per
concise-verify attempt still starts from the measured stock constant times
the 1.3x verify multiplier, since no concise-specific token measurement
exists yet.

**Also fixed: lane start order is interleaved across arms, not grouped by
arm.** Lane names sort alphabetically by arm (SDO `assure-w0`-`w3`, Codex
`assure-w4`-`w7`); staggering lanes in that plain sorted order would launch
every SDO lane before any Codex lane even started, so a matrix stopped
partway through (the hard stop above, or a person killing the launcher)
would leave lopsided partial data — one arm well progressed, the other
barely begun. `interleaved_launch_order` (`phase1_launch.py`) round-robins
the stagger schedule across arms instead (`w0, w4, w1, w5, w2, w6, w3, w7`),
so a stop at any point leaves roughly balanced partial data across both
arms.
