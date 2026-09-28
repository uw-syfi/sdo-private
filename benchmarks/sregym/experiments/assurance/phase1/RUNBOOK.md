# Phase-1 live assurance matrix: runbook (seed precondition)

The full runbook (preconditions, launch, monitoring and analysis) is on `vic/exp/phase1-launch`. When the two
branches merge, this section becomes that runbook's precondition 0. Its text wins over any mention there of
seed `30e023d`.

## Seed (precondition 0): regenerate from a fresh lifecycle

**Do not seed phase 1 from `30e023d`.** That lifecycle ran with benchmark-tailored hints: a ConfigMap clause
in the goal, a `network-policy-total-isolation` template rule, and a judge prompt and validator that pushed a
missing-ConfigMap check. `docs/fairness-DECISIONS.md` removes them. The four SDO configs
(`sdo_codex_luna_assure_p1_{a,b,c,d}.toml`) set `[pipeline] workspace_seed = "PENDING-FRESH-LIFECYCLE-SEED"`.
The runner refuses to launch stage 0 while that placeholder is in place. The Codex arms need no seed.

Steps, run by the launch agent. This is a live LLM run of about 2M tokens and about 13 minutes.

1. **Code and images.** Check out a commit that contains `vic/fix/detailor-sdo`. Rebuild the `sdo-*:v0.1.0`
   images from that commit, as in the full runbook's image precondition. The lifecycle runs in the adapter
   on the host, but the controller, responder and validator images must match the code that produced the
   seed.
2. **Run one unseeded SDO stage** on one lane: a single-stage pipeline with the same `[defaults]` as
   `sdo_codex_luna_assure_p1_a.toml`, no `workspace_seed`, `chain_application_workspace = false`, and any
   one phase-1 problem. The adapter finds no reusable lifecycle, because the objective text and its digest
   changed, and runs the fresh deployer and the three health-judge rounds. The model is Codex gpt-6-luna at
   medium effort (the D13 rule).
3. **Take the lifecycle-only checkpoint.** The adapter writes it before the first incident, to
   `<pipeline_dir>/lifecycle_seed_stage0` (`persist_lifecycle_seed`). Copy that directory outside the run
   tree, for example `/mnt/data/shli/assure-runs/seed-<lifecycle-sha>`. Do not use the stage's final
   `application_workspace`: by then it holds incident outcomes and reflection commits.
4. **Check the seed before use.**
   - `git -C <seed> log --oneline -3`: HEAD is the lifecycle commit ("sdo: capture goal, architecture, and
     independent health judge" or the refresh commit). There are no `sdo(incident-…)` commits.
   - `.sdo/outcomes.jsonl` is empty, and the only playbook is `health-objective`.
   - `.sdo/goal.md` has no "ConfigMap" clause.
     `grep -r network-policy-total-isolation <seed>/.sdo` finds nothing.
   - The health-objective manifest watches include `apps/v1 ReplicaSet`.
   - Record the lifecycle's token cost from `sdo_turn_usage.jsonl`. It is reported separately, as the
     one-time lifecycle cost.
5. **Fill in the placeholder.** In all four SDO configs, set `workspace_seed` to the seed's absolute path.
   Record the path and lifecycle commit in the configs' header comment and in the run manifest. Commit.
   `test_each_sdo_pipeline_waits_for_a_fresh_lifecycle_seed` then fails on purpose. Update it in the same
   commit to assert the recorded seed, and keep its `30e023d` check.
6. **Optional.** Replace the no-LLM suite's `seeds/hotel_reservation_30e023d` with the new seed's `.sdo`, as
   `assurance seed` expects.

If the lifecycle fails, the adapter reports it, as the earlier 3.6M-token failed attempt shows. Fix the cause
and rerun step 2. Do not fall back to `30e023d`.
