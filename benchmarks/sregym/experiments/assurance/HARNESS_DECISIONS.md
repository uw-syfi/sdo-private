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

