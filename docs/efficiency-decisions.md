# Experiment efficiency: where wall-clock went, and what is fixed

Branch `vic/feat/experiment-efficiency` (from `vic/exp/mixed-integration` at `ce7663db`), 2026-10-02. Unit-tested only: no cluster experiment and no Codex call was run for this work, so every saving below is an estimate from earlier runs, labelled as such.

## Where the time went (measured)

Cold C1 through the conductor (Tier 0, `docs/mixed-stream-decisions.md`), from the strict receipt's `driver_phase_timings_seconds` and the run log:

| phase | seconds | share of the driver's 960 s before submission | note |
| --- | --- | --- | --- |
| kind cluster up | 179 | before the stage | 04:46:38 to 04:49:37 |
| app deploy from source (build and rollout) | about 345 | before the stage | 04:49:37 to 04:55:22, from log timestamps |
| `inventory_and_lifecycle` (cold lifecycle) | 678 | 70.6 % | |
| `controller_install_or_reuse` | 12 | 1.3 % | |
| fault gate (baseline wait 10.7, injection request 21.1) | 32 | 3.3 % | healthy-baseline capture 5.2 s inside it |
| injection to verified recovery | 237 | 24.7 % | responder 238 s, verification 13 s |
| post-recovery learning and receipt (reflection) | 420 | after submission | on the critical path of the next incident; excluded from TTD and TTM |

The conductor reported 1340.8 s for the stage in total.

The cold lifecycle is 70 % of the driver's time before the fault is even injected. It was paid again for every launch and retry on the same source. Other measured losses from the same day:

| loss | measured | cause |
| --- | --- | --- |
| mini attempt 1, both sequences | 648 s and 750 s, then failed | stale validator image (fixed on `vic/fix/validator-image-plumbing`) |
| mini attempt 2 | 708 s then failed; the other sequence still in the lifecycle after 12 min | lifecycle guard false positive (fixed on `vic/fix/lifecycle-guard-false-positive`) |
| mini attempt 3, incident 1 | 8.5 and 11 min waiting for a fault that no detector could see, 2 runs | no per-incident detection timeout in the fastloop; then killed, which leaked the NetworkPolicy into incident 2's baseline |
| Phase A step 2 | about 18, 20 and 45 min across three relaunches | fastloop missed the validator-image call site, the launcher lacked `--closeout-state-gate`, the deferred fault gate raced the link prober |
| host load | 35 to 65 for stretches (other users' CI) | health timeouts and pods not Ready on earlier runs |

## What is implemented

| # | change | commit | estimated saving |
| --- | --- | --- | --- |
| 1 | `LifecycleSeedCache`: keep the `.sdo` a cold lifecycle produced, keyed by source commit, health objective, active topology, validator image identity, provider, model and a digest of the lifecycle authoring code; restore it into a clean workspace as one commit. A restored lifecycle still goes through `reuse_initial_lifecycle_if_valid` (provenance, deployer, objective, topology, validator verdict) and is reverted and authored cold if that fails. Default on in the fastloop, `--cold-lifecycle` to measure a cold one; opt-in elsewhere through `SDO_LIFECYCLE_SEED_CACHE_DIR`, so conductor runs stay cold | `ff44c5e5`, `2b54771c` | about 9 to 10 min per repeated launch (678 s to the restore, one validator attestation and the controller install). At least seven cold lifecycles were authored on 2026-10-02 (Tier 0, mini attempts 1 and 2 on two sequences each, the 07:04 confirming launches). Unmeasured on a cluster |
| 2 | Shared detector-validation cache. The fastloop made one cache per run dir, so it never outlived a run (`mini3-a`: the seed was attested by the stale `v0.1.0` validator `aba7...`, the run used `mini1` `aedb...`, so the check legitimately re-ran the validator and its verdict was then thrown away with the run dir). It now defaults to `~/.cache/sdo/lifecycle-validation` (or `SDO_LIFECYCLE_VALIDATION_CACHE_DIR`). The self-check re-run was correct behaviour, not a bug: the identity guard is what stops a lifecycle validated under a stale SDK from being trusted under a new one | `2b54771c` | about 1 min per run whose seed was attested by a different validator image |
| 3 | `--detection-timeout N` in the fastloop (the conductor already had `detection_timeout_sec`). An incident nothing opens ends as `undetected` (`IncidentRecord.undetected`, `error` starts with `undetected:`), the loop still grades it, recovers the fault (so nothing leaks into the next baseline) and goes on. Opt-in, default off; `seq_mixed.sh` uses 240 s | `2b54771c` | 8.5 to 11 min minus N per undetected incident, so about 5 to 7 min at 240 s. The diagnosis was submitted 22 s after injection in Tier 0 |
| 4 | Load governor: `hostguard wait` holds a cluster-heavy step until the 1-minute load stays at or under 20 for 120 s (max wait 20 min); `fastloop up` runs it by default (`--no-load-governor`, `--max-load`, `--calm-seconds`, `--max-load-wait`) and writes `host-load.json`, and a host that never calms is flagged as load-contaminated, not silently measured | `f4517f9c` | prevents load-induced retries and contaminated numbers; unmeasured |
| 5 | Stall watchdog: `hostguard watch --path <run dir>` classifies "no file written for N s" as infra (load at or over 25), a detection gap (controller installed, nothing opened; points at `--detection-timeout`) or an unclassified setup stall, and exits 0/1/2/3 so a script can act on it | `f4517f9c` | replaces manual polling |
| 6 | Contract test: the installer's controller arguments, for each optional feature and all together, are parsed by the launcher's own parser. Red against the pre-fix launcher in under a second; the missing `--closeout-state-gate` cost about 20 min after cluster up, image load and a cold lifecycle | `2dd1004f` | about 20 min of the Phase A loss, per recurrence |
| 7 | `--inject-before-resume` now warns that it blinds the link probe | `80b105db` | prevents a misleading "undetected" result |
| 8 | `seq_mixed.sh`: the warm-iteration sequence runner with all of the above, no hard-coded worktree path, no `--inject-before-resume` for single faults, `SDO_VALIDATOR_IMAGE` unset | this commit | |

Usage:

```bash
SEED_REPO=/path/to/cold-source-repo benchmarks/sregym/experiments/mixed-stream/seq_mixed.sh <worker-id> <name> <image-tag>
COLD=1 ...        # measure a cold lifecycle (stage 0 of a stream whose cold cost matters)
python -m benchmarks.sregym.fastloop.hostguard watch --path /mnt/data/shli/clc-runs/<name> --interval 120
python -m benchmarks.sregym.fastloop.hostguard wait --max-load 20
```

## Decisions

- **The seed cache stores only a cold lifecycle's `.sdo` and restores only into a workspace with no `.sdo`.** Alternative: also cache the validator verdict. Rejected: the in-workspace attestation and the shared validation cache already carry it, and a second trust path would be harder to reason about.
- **The cache key includes a digest of `sdo/agent_runtime/lifecycle/*.py` and the sandbox, not an image tag.** A tag can be rebuilt with the same name; the source digest changes whenever the lifecycle prompts or validation change, so a stale handoff is never restored. Cost: any edit to the lifecycle package invalidates the cache, which is the safe direction.
- **Fastloop defaults the seed cache on; the conductor keeps it opt-in.** Final numbers in this repo come from the conductor and must measure a cold lifecycle where that matters; the fastloop is the dev loop.
- **The detection timeout is opt-in.** A default would change what existing fastloop runs mean. It ends the incident as a failed one (`undetected`), recorded and recovered, not as a stall.
- **The undetected signal lives in `loop.py` as `UndetectedIncidentError`**, translated from the adapter's `DetectionMissError` by `sdo_agent.py`, so the loop stays transport-neutral.
- **Not a bug: the "self-check re-ran on every run".** See change 2. Tier 1's three-step todo item is closed with that diagnosis.

## Proposed, not implemented

| proposal | why not now | estimated saving |
| --- | --- | --- |
| Skip kind image load for images a node already holds. `ensure_kind_platform_images` and `ensure_kind_images` in `third_party/sregym/sregym/worker_infra.py` `docker save` and import every image into every node on each `up`; the three images saved on each `up` (controller, SREGym responder, validator) are 7.5 GB | it lives in the SREGym submodule, which needs a commit on the fork's `sdo` branch; compare the node's `crictl inspecti` digest with the local one and skip on a match | about 1 to 3 min per `up` on a reused cluster (the `docker save` was seen at 43 % CPU; unmeasured) |
| A no-LLM preflight on the validator image: validate a one-line `links.yaml` fixture in the image before the lifecycle starts | needs a fixture `.sdo` workspace; `StaleValidatorImageError` already fails loudly, but only after the deployer and round 1 | about 10 min per stale-image launch, found in 15 s |
| Reflection on the critical path: 420 s of post-recovery learning gate the next incident in a stream. The composite-stream takeaway proposes skipping it when learned detectors already fired and nothing new was found | it changes SDO behaviour, so it needs its own A/B before it is used for numbers | up to 7 min per incident where it applies |
| Fastloop composites without `--inject-before-resume`: let the default controller-resume, wait for the link baseline, inject sequence apply, so composites with a NetworkPolicy fault are detectable | needs a cluster run to check that the near-simultaneous injection keeps the composite semantics | removes the blind spot; correctness, not time |

## How to merge

The branch is based on `vic/exp/mixed-integration` and touches `sdo/agent_runtime/lifecycle` (new `seed_cache.py`, a public `validator_identity`), `benchmarks/sregym/adapter/driver.py` (`run_or_reuse_lifecycle(seed_cache=...)`), `benchmarks/sregym/fastloop` (`cli.py`, `loop.py`, `records.py`, `sdo_agent.py`, new `hostguard.py`) and the adapter facade (`DetectionMissError`). Merge it after `vic/exp/mixed-integration` settles; conflicts are most likely in `fastloop/cli.py` and `adapter/driver.py`. `IncidentRecord.undetected` defaults to `False`, so earlier `incidents.jsonl` files still load.
