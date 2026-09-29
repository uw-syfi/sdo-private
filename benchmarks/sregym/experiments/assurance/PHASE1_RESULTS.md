# Phase-1 assurance results (exec-parity run, 2026-09-28/29)

Branch `vic/exp/phase1-run`. Launched 23:22 UTC by `.launch/launch_exec.py`; every lane finished by 01:00 UTC.
Everything here is judge-free for time (TTD is the diagnosis POST minus the injection; TTM is `Verdict.ttm_seconds`, the
judge-excluded time floored at the last mutation). Agents are Codex `gpt-6-luna` only, the judge is `codex-gpt-6-luna`
at `xhigh`. Weighted tokens use PLAN.md's weights (cache read 0.1, output 8).

**Setup being measured.** Both arms have identical namespace permissions, including `pods/exec`, `attach` and
`port-forward` (D18). Neither gets Secrets or RBAC. The Codex arm is concise-verify (`verify_protocol = "concise"`,
`allow_exec = true`), 25 attempts on `assure-w4..w7`. SDO is 4 pipelines (rotations A-D) of 10 stages on `assure-w0..w3`,
seeded from `/mnt/data/shli/assure-runs/seed-dd6bc81`, with `detection_timeout_sec = 900`. SDO stage n = 40 (20 first
encounters, 20 repeats). Codex is memoryless, so its 25 attempts are independent.

**Run directories.**
- SDO pipelines: `third_party/sregym/logs/20260928_232253_pipeline_assure-p1-sdo-a`, `..._232455_..-b`, `..._232654_..-c`, `..._232856_..-d`.
- Codex: `third_party/sregym/logs/20260928_232348_codex`, `..._232548_codex`, `..._232749_codex`, `..._232958_codex`.

**Reproduce.**
```
uv run python -m benchmarks.sregym.assurance.phase1_analyze --sdo <4 pipelines> --codex <4 codex dirs>
uv run python -m benchmarks.sregym.assurance.phase1_tables   --sdo <4 pipelines> --codex <4 codex dirs>
uv run python -m benchmarks.sregym.assurance.phase1_timeline --sdo <4 pipelines> --codex <4 codex dirs>
```

## 0. Read this first

- **Two problem classes behave differently and are reported separately.** S1, S2 and K2 are symptomatic faults: an
  incident is visible and SDO's controller detects it in under a second. S3 (`network_policy_block`) and K1's
  NetworkPolicy component are latent (masked) faults, per `NP_MASKING.md` (branch `vic/exp/np-masking-verify`, commit
  `87314ee`): the established gRPC connection keeps working, so no user request fails.
- **The two arms are not told the same thing.** Codex is told an incident exists. SDO must detect it itself.
- **n is small.** 4 pipelines, 5 Codex attempts per problem. Every CI below is wide. Nothing here is a final claim.
- **The rotation makes some "first encounters" partly primed.** In the headline set, S1 is primed by K1's rate
  ConfigMap in 3 of 4 pipelines, S2 by K2's selector in 2 of 4, K2 by S2 in 2 of 4. Only S1 in A, S2 in A and B, and K2
  in C and D are truly cold. The "first encounter" rows are best read as "first time this exact problem was seen in the
  stream".
- **SDO product bug that cost data.** Five strict receipts were rejected (three `same_session_reflection=true` failures,
  two `completed=true` failures). One of them killed the next stage before injection (pipeline C, stage 9, S2 round 2),
  and pipelines B and C ended with a failed persistent-controller teardown. See section 7.

## 1. Headline: S1, S2 and K2 (symptomatic faults)

Time cells use end-to-end passes only (a failed run has no time to mitigation). Token cells are medians over all runs
that recorded tokens. SDO tokens are the responder's; the last column adds the reflection the incident triggered.
"uncached in" excludes cache reads.

| Problem | Arm | n | Diagnosis | Mitigation | End-to-end (Wilson 95%) | TTD s | TTM s | last-mutation s | uncached in | cache read | output | reasoning | weighted | weighted + reflection |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | SDO first encounter | 4 | 4/4 | 4/4 | 4/4 (0.51-1.00) | 31.1 | 58.8 | 58.8 | 21,680 | 275,840 | 3,128 | 516 | 76,382 | 263,094 |
| S1 | SDO repeat | 4 | 4/4 | 4/4 | 4/4 (0.51-1.00) | 17.1 | 27.2 | 26.7 | 26,417 | 157,696 | 2,201 | 243 | 56,563 | 56,563 |
| S1 | Codex | 5 | 5/5 | 5/5 | 5/5 (0.57-1.00) | 50.9 | 121.1 | 106.0 | 47,809 | 694,016 | 3,484 | 1,241 | 138,424 | 138,424 |
| S2 | SDO first encounter | 4 | 4/4 | 4/4 | 4/4 (0.51-1.00) | 14.4 | 36.3 | 34.7 | 25,928 | 181,760 | 2,234 | 344 | 61,998 | 81,592 |
| S2 | SDO repeat | 4 | 3/4 | 3/4 | 3/4 (0.30-0.95) | 8.5 | 14.7 | 14.7 | 26,677 | 158,720 | 1,843 | 347 | 59,833 | 59,833 |
| S2 | Codex | 5 | 3/5 | 3/5 | 3/5 (0.23-0.88) | 37.0 | 42.2 | 41.2 | 40,014 | 460,544 | 2,045 | 654 | 99,196 | 99,196 |
| K2 | SDO first encounter | 4 | 3/4 | 4/4 | 3/4 (0.30-0.95) | 19.9 | 47.8 | 47.7 | 35,411 | 286,208 | 3,522 | 894 | 101,630 | 259,246 |
| K2 | SDO repeat | 4 | 4/4 | 4/4 | 4/4 (0.51-1.00) | 16.7 | 131.3 | 131.3 | 25,242 | 284,032 | 3,722 | 1,161 | 87,175 | 87,175 |
| K2 | Codex | 5 | 4/5 | 4/5 | 3/5 (0.23-0.88) | 37.8 | 114.6 | 114.6 | 35,110 | 484,864 | 2,951 | 946 | 110,405 | 110,405 |

The S2 SDO repeat has n = 4 stages but one was lost to the receipt bug before injection (pipeline C stage 9) and counts
as a failure. The K2 first-encounter stage that failed diagnosis (pipeline B stage 3) still mitigated.

Pooled over S1, S2 and K2 (stratified bootstrap, 10,000 replicates, seed 20261003; ratio of medians):

| Ratio | Pooled | 95% CI | Per problem |
|---|---|---|---|
| TTM, Codex / SDO repeat | 3.98 | 2.21-5.90 | S1 4.45, S2 2.86, K2 0.87 |
| TTM, Codex / SDO first encounter | 2.22 | 1.66-2.66 | S1 2.06, S2 1.16, K2 2.40 |
| TTM, SDO repeat / SDO first (C5) | 0.56 | 0.35-0.97 | S1 0.46, S2 0.41, K2 2.75 |
| last-mutation, Codex / SDO repeat | 3.69 | 3.06-4.73 | S1 3.97, S2 2.79, K2 0.87 |
| last-mutation, SDO repeat / SDO first | 0.55 | 0.40-0.64 | S1 0.45, S2 0.43, K2 2.75 |
| weighted tokens, SDO repeat / Codex | 0.50 | 0.37-0.62 | S1 0.38, S2 0.50, K2 0.79 |
| weighted tokens, SDO repeat incl. reflection / Codex | 0.50 | 0.37-0.69 | same |
| weighted tokens, SDO first encounter (responder) / Codex | 0.66 | 0.55-0.78 | S1 0.55, S2 0.63, K2 0.92 |
| weighted tokens, SDO first encounter incl. reflection / Codex | 1.88 | 1.16-2.48 | S1 1.90, S2 0.82, K2 2.35 |
| weighted tokens, SDO repeat / SDO first (responder) | 0.76 | 0.57-0.93 | S1 0.69, S2 0.80, K2 0.86 |

End-to-end differences (Newcombe 95%): SDO minus Codex on the headline set is +0.18 (22/24 vs 11/15; CI -0.05 to
+0.44). Round 1 alone and round 2 alone are each +0.18 (11/12 vs 11/15; CI -0.13 to +0.45).

**Takeaways.**
- **What it shows.** On symptomatic faults SDO solves 22 of 24 stages and Codex 11 of 15. SDO repeats mitigate about 4x
  faster than Codex by median TTM on S1 and about 3x on S2, and use about half the weighted tokens. SDO's first
  encounters are already about 2.2x faster than Codex, and their responder tokens are 0.66x Codex's.
  K2 is the exception: the K2 repeat (131 s) is no faster than Codex (115 s), and slower than SDO's own first
  encounter (48 s).
- **Why K2 repeats are slow (verified in the rollouts).** In pipelines B, C and D the retrieved playbook was the
  selector playbook. Its `repair.sh` fixed the selector within a few seconds, and the agent then spent 100-330 s
  finding the second cause (the readiness probe), as it would cold. In pipeline A the learned playbook repaired both
  causes in one step, and that stage took 21 s. So a K2 playbook that encodes only one component gives a fast partial fix
  and no speedup on the other. The stage-level result is a composite-playbook quality problem, not a detection problem.
- **Confidence.** Moderate for S1 and S2 direction (both arms' ranges do not overlap on TTM), low for magnitudes (CIs of
  a factor of 2-3 at n = 4 and 5) and low for K2. Accuracy differences are not significant (every Newcombe CI includes 0).
- **Implication for SDO's claims.** The speed and token results support C1-C3 in direction on the symptomatic set. The
  solve-rate claim (C4) is not established here: SDO 22/24 is above Codex's 11/15 on the point estimate, but its Wilson
  lower bound (0.74) is just under 0.75 and n is small.
- **Next step.** Fix composite playbook synthesis so a reflection on a multi-cause incident records every repaired
  component, then rerun K2. Add more repeats for S2 (n = 3 after the lost stage).

### 1.1 Why S1 repeats look no faster on the raw clock, and why S2 was only about 0.7x mid-run

The coordinator's mid-run reading (S1 round 2 near 75 s against 73-76 s in round 1) is real for one pipeline and for the
raw, judge-inclusive clock. Final numbers are different.

Median timeline in seconds after the harness's `fault_injected_at` anchor. The harness stamps the anchor when the
injection request returns, which is 6.1-6.5 s after the injection starts (12.4 s for K2); values before 0 happened
during the request. Detection lands 0.3 s into the injection request.

| Problem, round | Detector fires | Incident open | Responder session start | First agent action | Diagnosis POST | Last mutation | Mitigation POST (incl. judge wait) | Gate clear |
|---|---|---|---|---|---|---|---|---|
| S1 round 1 | -6.2 | -5.7 | -3.4 | +2.5 | 31.1 | 58.8 | 73.5 | 118.8 |
| S1 round 2 | -6.2 | -5.7 | -3.6 | +1.9 | 17.1 | 27.2 | 48.0 | 87.2 |
| S2 round 1 | -6.0 | -5.0 | -3.0 | +3.2 | 14.4 | 34.7 | 50.8 | 86.5 |
| S2 round 2 | -6.0 | -5.5 | -3.6 | +1.3 | 8.5 | 14.7 | 34.2 | 68.5 |
| K1 round 1 | -6.1 | -5.6 | -2.9 | +2.5 | 23.6 | 59.0 | 72.9 | 111.9 |
| K1 round 2 | -6.2 | -5.7 | -3.0 | +3.1 | 17.4 | 27.5 | 52.2 | 107.6 |
| K2 round 1 | -12.2 | -10.8 | -8.1 | -3.3 | 15.2 | 85.0 | 105.2 | 169.3 |
| K2 round 2 | -12.3 | -11.8 | -8.9 | -2.8 | 16.7 | 131.3 | 145.5 | 212.2 |

**Takeaways.**
- **What it shows.** Fixed overhead is not the bottleneck. The detector fires 0.3 s into the injection, the incident opens
  about 0.5 s later, the responder Job's session starts about 3 s after that, and the first agent action is at most about
  9 s after injection start. Detection and Job startup together are about 3 s of a 27-60 s TTM. Agent work dominates
  round 1 (first action to last mutation is 56 s for S1); in round 2 it drops to 25 s for S1 and 13 s for S2.
- **Why the raw clock hides the repeat speedup.** The mitigation POST (raw column) comes after the judge grades the
  diagnosis and after the agent verifies the rollout. For S1 the POST moves only from 73.5 s to 48.0 s (and in pipeline A
  from 72.5 s to 74.3 s), because the agent's diagnosis grading wait and the `mongodb-geo` rollout and
  `sdo incident status` verification burst set a floor of roughly 20-45 s after the last mutation. The judge-free TTM
  (58.8 to 27.2 s) and last-mutation times show the real halving. The per-pipeline S1 medians are: round 1 47/60/59/59 s,
  round 2 46/28/30/27 s (A/B/C/D). Pipeline A's S1 repeat is the outlier.
- **Was the playbook retrieved and followed in round 2?** Yes for most. Of the 17 round-2 responder sessions, 12 ran the
  playbook's `repair.sh` after `verify.sh`, 3 read the playbook and repaired by hand, and 2
  found no playbook (the S3 flicker stages, `applied_playbook_count` 0). Pipeline A's S1 repeat is the case where the
  playbook matched but the warm prompt did not fire (`warm_prompt = no`), so the agent behaved like a first encounter
  (10 commands, first mutation at 26 s, POST at 74 s). In round 1, 12 of 16 responder sessions already had a matching
  playbook (family or component priming) but only 3 ran a repair script.
- **Update to the S2 reading.** The mid-run 0.7x was one pair. With all data, S2 repeat / first is 0.41 (14.7 s vs
  36.3 s, n = 3 vs 4), and pipeline D's S2 first encounter (7.8 s) was already primed by K2's selector playbook.
- **Confidence.** Moderate for the overhead claim (n = 33 responder sessions, consistent across problems). Low for
  pipeline-level explanations (one stage each).
- **Implication.** The remaining fixed cost after memory is the closure gate (roughly 55-90 s from last mutation to gate
  clear) and rollout time, not detection or Job startup. Speedups beyond about 2x on TTM will not come from faster
  detection.
- **Next step.** Report last-mutation time next to the judge-free TTM whenever the judge grades during the incident, and
  investigate why A's S1 repeat did not get the warm prompt.

## 2. Latent (masked) faults: S3 and K1

**Framing.** Per `NP_MASKING.md`, `deny-all-recommendation` blocks new connections but not the frontend's already
open gRPC connection, so users see 100% success. Detection by symptom is therefore undefined or unbounded, censored
here at the 15-minute detection timeout. **Codex is told an incident exists; SDO must detect it.** These rows are not
a like-for-like measure of diagnosis skill. No TTD or TTM is defined for any of them, because no run mitigated.

| Problem | Arm | n | Diagnosis | Mitigation | End-to-end | Incident opened within 15 min | Weighted tokens (median, responder) |
|---|---|---|---|---|---|---|---|
| S3 | SDO first encounter | 4 | 0/4 | 0/4 | 0/4 (0.00-0.49) | 0/4 (all censored at 900 s) | none spent |
| S3 | SDO repeat | 4 | 1/4 | 0/4 | 0/4 (0.00-0.49) | 2/4 (both transient flickers, see below; 2 censored) | 33,263 |
| S3 | Codex | 5 | 0/5 | 0/5 | 0/5 (0.00-0.43) | not applicable (told) | 112,106 |
| K1 | SDO first encounter | 4 | 4/4 | 0/4 | 0/4 (0.00-0.49) | 4/4 | 76,551 |
| K1 | SDO repeat | 4 | 4/4 | 0/4 | 0/4 (0.00-0.49) | 4/4 | 50,225 |
| K1 | Codex | 5 | 1/5 | 0/5 | 0/5 (0.00-0.43) | not applicable (told) | 158,310 |

S3 detection detail across all 8 SDO S3 stages: 6 had no incident within 900 s (censored detection misses), and 2 (pipeline
C stage 5, pipeline D stage 9) opened an incident from a transient `traffic-health` finding (`scenario-slo.hotel-search`
and `hotel-user-login`, 5-10 s after injection). In both the finding had cleared by the time the responder ran, `sdo
incident status` read HEALTHY, the responder changed nothing, and the NetworkPolicy stayed. In C stage 5 the judge
still accepted the diagnosis because the text named `deny-all-recommendation` as a plausible change.

K1 detail: in all 8 SDO K1 stages the controller detected the rate ConfigMap component within a second and repaired it
(last mutation at 59 s in round 1 and 27.5 s in round 2), and every receipt was `completed` with independent verification
passing HEALTHY, while the mitigation oracle failed because the NetworkPolicy component was still there. That is a
false closure in 8 of 8 K1 stages (section 5). Diagnoses on K1 were correct for the visible component (4/4 and 4/4).
Codex diagnosed K1 correctly once (1/5) and never mitigated it.

**Takeaways.**
- **What it shows.** Neither arm fixes the NetworkPolicy fault in any of 26 attempts. SDO does not detect S3 by symptom
  (6/8 censored), and closes K1 incidents as healthy with the NetworkPolicy in place. Codex, though told about the
  incident, reaches the wrong cause on S3 (0/5), mostly by following the `failure-admin` decoy (section 5).
- **Confidence.** High that the fault is latent and that SDO cannot see it by symptom (deterministic mechanism, 8/8
  consistent stages, mechanism verified independently in `NP_MASKING.md`). Low on any cross-arm comparison: the arms have
  different information, and n is 4-5.
- **Implication for SDO's claims.** This is not evidence that SDO is worse at diagnosis. It is evidence that SDO's health
  gate and traffic detectors share the blind spot the fault creates, which turns it into a false closure. The
  composite-mitigation claim (C8) and the zero-false-closure claim (C4) fail on these two problems for that reason.
  Presenting these two as latent faults (as `NP_MASKING.md` recommends) and adding a per-edge fresh-dial probe is the
  right framing, not dropping them.
- **Next step.** Add the per-edge fresh-dial probe and a NetworkPolicy state-change detector to the SDK, then rerun S3 and
  K1 with and without a frontend restart.

## 3. Claims against PLAN.md's pre-registered thresholds

Verdict mapping: PASS, FAIL or INCONCLUSIVE (the analyzer's "directional", where the point estimate meets the threshold
but the CI bound or another clause does not). The verdicts come from `phase1_analyze` on all five problems, as
pre-registered. Times in those pooled ratios include runs that did not mitigate (K1 for both arms), so section 1's
headline-only numbers are the cleaner view.

| Claim | Verdict | Evidence against the pre-registered criterion |
|---|---|---|
| C1 recurring faults faster (pooled >= 2.0, lower bound >= 1.5, per problem >= 1.5 on >= 4 of 5) | INCONCLUSIVE (directional) | Pooled 3.75, CI 2.21-4.42: both pooled clauses met. Per-problem ratio >= 1.5 on 3 of 5 (S1 4.45, S2 3.89, K1 4.19; K2 0.61; S3 undefined): the per-problem clause fails. Headline-only: 3.98 (2.21-5.90), 2 of 3. K1's ratio is over runs that failed mitigation, so it is not meaningful |
| C2 recurring faults fewer tokens (ratio <= 0.8, upper < 1.0; magnitude <= 0.4) | PASS (direction); magnitude not met | Pooled weighted ratio 0.42 (0.31-0.55) incl. reflection. Headline-only 0.50 (0.37-0.69). The paper's 0.4 magnitude is not met, as expected |
| C3 novel faults not degraded (time <= 1.25, accuracy diff >= -0.10 with Newcombe lower > -0.30, responder tokens <= 1.25) | PASS | Time ratio SDO cold / Codex 0.57 (0.46-0.82). e2e difference +0.11 (Newcombe -0.17 to +0.37). Responder-token ratio 0.63. Caveat: cold stages are partly primed (section 0). Total cold tokens including reflection are 1.88x Codex (1.16-2.48), reported not gated |
| C4 SDO solve rate (e2e >= 0.90 with Wilson lower >= 0.75; SDO >= Codex - 0.05; zero false closures) | FAIL | SDO e2e 22/40 = 0.55 (Wilson 0.40-0.69). SDO minus Codex +0.11 (meets that clause). False closures: 10 (section 5), so the hard clause fails. On the symptomatic set alone SDO is 22/24 = 0.92 (0.74-0.98), just under the 0.75 bound |
| C5 learning curve (round-2/round-1 <= 0.5, upper <= 0.7; memory growth; Codex flat) | INCONCLUSIVE (directional) | Pooled 0.47 (0.39-0.78): point meets, upper bound 0.78 does not. Memory-growth check passes on all 4 lanes. The Codex first-half/second-half clause was not computed. Headline-only ratio is 0.56 (0.35-0.97) |
| C6 detector retrieval | not in this run | Fastloop, no-LLM replay |
| C7 parameter-binding transfer | not in this run | Needs phase-2 V1 |
| C8 composites (full mitigation >= 0.75 and >= Codex; zero false closures; gate 3/3) | FAIL | SDO full mitigation 8/16 = 0.50 by mitigation oracle (7/16 by e2e); K2 8/8, K1 0/8. Codex 4/10 = 0.40 (3/10 by e2e). Point estimate below 0.75; SDO false closures on K1 are 8 of 8. The no-LLM gate qualification (K1 and K2 3/3) is not contradicted for K2 but does not hold for K1 live, where the NetworkPolicy is masked |
| C9 idle-cost | not in this run | Fastloop soak |
| C10 lifecycle from source | not in this run | Phase 2 |
| C11 memory safety (SDO decoy-driven failures = 0; C1 ratio >= 2.0) | PASS | 0 of 40 SDO diagnoses cite the failure-admin decoy or the log-level drift; Codex cites the decoy in 11 of 25 attempts and 9 of its diagnosis failures are decoy-driven. C1's pooled ratio is 3.75 with lower bound 2.21. K3 (the benign-drift composite) is phase 2. Caveat: the decoy check is a text scan of the submitted diagnosis, not a trace review |

**Takeaways.**
- **What it shows.** SDO beats the concise-verify baseline on speed and tokens where the fault is symptomatic (C2, C3
  pass; C1 and C5 directional), and it is not misled by decoys (C11). It fails the two hard reliability criteria (C4
  zero false closures, C8 composite mitigation), and both failures trace to the latent NetworkPolicy fault.
- **Confidence.** C2, C3 and C11 are the most solid. C1 and C5 are directional only, mainly because K2 repeats are slow
  and n is 4. C4 and C8 failures are firm in direction (0/16 on the latent problems, deterministic mechanism) but the
  "0.55" figure should not be quoted without its split: 22/24 on symptomatic faults and 0/16 on latent ones.
- **Implication.** Do not claim "SDO solves at least 90%" from phase 1. The defensible statement is: on symptomatic faults
  SDO matches or beats a verify-only baseline at about half the tokens and 2-4x faster, and it silently closes latent
  faults its detectors cannot see.
- **Next step.** The latent-fault fix (fresh-dial probe) and composite-playbook synthesis (K2), then a phase-1 rerun.
  Phase 2 should not launch until the false-closure path is closed.

## 4. Comparison with the earlier, discarded Codex attempts (different setup)

These runs are **not comparable** to section 1. They ran without `pods/exec` (and in the first set, behind the
confounded stream proxy), so they had a strictly weaker tool set than the exec-parity arm. They are kept for
provenance only.

| Set | Attempts | Setup | S1 e2e | S2 e2e | K2 e2e | S3 e2e | K1 e2e | Median weighted tokens (S1) |
|---|---|---|---|---|---|---|---|---|
| Discarded 1, `20260928_203955 .. 210650` | 26 | concise verify, no exec | 5/6 | 0/5 | 3/5 | 0/5 | 0/5 | 155,944 |
| Discarded 2, `20260928_214557 .. 215139` | 25 | concise verify plus `exec_disclosure = true`, no exec allowed | 4/5 | 2/5 | 2/5 | 0/5 | 0/5 | 153,946 |
| **This run** | 25 | concise verify, exec allowed | 5/5 | 3/5 | 3/5 | 0/5 | 0/5 | 138,424 |

TTM medians over passing runs, S1: 198.9 s (set 1), 142.5 s (set 2), 121.1 s (this run). K2: 140.8, 135.1, 114.6 s.

**Takeaways.**
- **What it shows.** Giving Codex exec did not change its solve rate materially (S1 5/5 vs 5/6 and 4/5, S2 3/5 vs 0/5
  and 2/5, K2 3/5 vs 3/5 and 2/5) and made its passing runs somewhat faster (S1 121 s vs 143-199 s).
- **Confidence.** Low: n = 5 per cell, and the earlier sets differ in proxy and prompt. The S2 difference (0/5 to 3/5) is
  larger than the noise you would expect but is not established.
- **Implication.** The exec-parity Codex baseline is, if anything, slightly stronger than the earlier no-exec one, so
  the SDO speed advantage in section 1 is measured against the strongest baseline we have run.
- **Next step.** None needed; do not mix these sets into any pooled statistic.

## 5. Reliability

Run validity (`run_validity`, after the fix below): 65 problem runs, 31 valid, 34 `agent_failure`, **0 `invalid_infra`**.
"agent_failure" here means the harness graded the run as failed (oracle did not pass, or SDO produced no strict
receipt), not that the agent crashed. Breakdown of the 34: 11 diagnosis and mitigation both failed, 10 mitigation failed,
8 never submitted, 2 diagnosis failed, 1 no mitigation verdict (some runs carry two reasons: 7 had no strict receipt, 3
receipts were rejected for `same_session_reflection`, 2 for `completed=true`).

A first pass of `run_validity` excluded two SDO stages (pipeline C stage 4 and 5) as "agent output shows nodes of other
lanes {'ure-w2'}". That was a false positive: the agent's tool output was truncated mid-node-name
("...tokens truncated...ure-w2-worker"). I fixed `_NODE` to ignore a name cut by the truncation marker, test first
(`test_a_node_name_cut_by_tool_output_truncation_is_not_another_lane`). All 65 runs are now in.

| Measure | SDO (40 stages) | Codex (25 attempts) |
|---|---|---|
| End-to-end success | 22 (0.55) | 11 (0.44) |
| Stages with no verdict | 8 (6 S3 detection misses, 1 S3 flicker stage, 1 lost to the receipt bug) | 0 |
| False closures (controller closed an incident while the mitigation oracle failed or was never posted) | 10: K1 8/8 (receipt `completed`, verification HEALTHY, NetworkPolicy still present) and S3 2/2 flicker stages (gate cleared, no repair) | not applicable (no closure gate) |
| Decoy cited in the diagnosis | 0 | 11 (S1 2, S2 2, S3 4, K1 3); 9 decoy-driven diagnosis failures (S2 2, S3 4, K1 3) |
| Leftover helpers flagged by `run_validity` | 0 | 0 |
| Censored at TTM > 900 s | 0 | 0 |

- **SDO receipts.** 5 of 40 strict receipts were rejected: 3 with `same_session_reflection=true` required for
  `reflection_session_mode=resume` (B2, B9, C8; each has `recovery_attribution = external`, `reflection_attempts = 0`) and
  2 with `completed=true` (C5, D9, the S3 flicker stages). The C8 rejection made the next stage's controller reuse fail with
  `ControllerInstallError`, so pipeline C stage 9 (S2 round 2) never injected. Pipelines B and C ended with a failed
  persistent-controller teardown (exit 1). The harness data for the affected stages is intact.
- **Codex helpers.** The `run_validity` helper check covers SDO only; Codex helper cleanup relies on the concise-verify
  prompt and was not checked independently.

**Takeaways.**
- **What it shows.** No run was lost to infrastructure. SDO's 18 failures are: 8 K1 mitigations that never repaired the
  masked NetworkPolicy, 8 S3 stages with no verdict, 1 K2 diagnosis miss, and 1 stage lost to the receipt bug.
  The controller closed 10 incidents as healthy while the fault remained. The Codex arm's failures are dominated by the
  decoy (9 of 14 failures).
- **Confidence.** High on the counts (read from receipts and CSVs). The false-closure definition is mine: "closed"
  means the controller reported an independently verified resolution. It does not check that the oracle's fault
  is the same fault the gate checks.
- **Implication.** SDO's reliability problem is concentrated in one blind spot and one receipt bug, both fixable.
  The receipt bug is a product defect (`sdo` production receipt validation), not a benchmark artifact.
- **Next step.** Fix the `external` recovery attribution and reflection-mode mismatch in the production receipt, and add
  a regression test; make a stage whose gate clears without repair count as an unresolved incident, not a closure.

## 6. Exec usage

| | Attempts or sessions | Used exec | Used port-forward | Used attach | Tool calls |
|---|---|---|---|---|---|
| SDO responders | 33 sessions | 0 | 0 | 0 | 444 |
| Codex | 25 attempts | 19 (65 calls) | 13 (19 calls) | 0 | 392 |

What Codex used exec for (by attempt, not exclusive): 15 attempts probed the app from inside a pod (`wget` against the
frontend or a service), 8 inspected MongoDB users, and **11 mutated MongoDB roles through exec**
(`db.grantRolesToUser('admin', ...)`, following the failure-admin decoy's `revoke-mitigate` script). Of those 11 attempts
only the 2 S1 attempts passed end to end; the other 9 failed. `port-forward` was used by 13 attempts to curl the frontend
from the host (8 of 13 passed).

**Takeaways.**
- **What it shows.** SDO never needed exec: its playbooks and `kubectl` reads were enough, and it had no decoy exposure
  through exec. Codex used exec or port-forward in every attempt, and its most consequential use was acting on the
  decoy, not diagnosing the fault.
- **Did exec help?** For diagnosis probing (frontend `wget`), plausibly, but it is confounded with problem: passes were
  5 of 15 attempts that probed, and 2 of 11 that ran DB grants. For SDO it had no role. Against the discarded no-exec
  runs (section 4), Codex's pass rate did not change materially.
- **Confidence.** High on the counts; low on any statement about whether exec changed outcomes (confounded, n = 25).
- **Implication.** D18's stated risk (exec can read mounted secrets) has a second, larger one: exec gives an agent a
  write path into a data store that the SDO controller neither sees nor checks. Exec parity makes the baseline stronger
  on speed but also gives it a way to be misled by decoys.
- **Next step.** Decide whether the fairness statement should record the DB-mutation exec path, and whether the analysis
  should treat exec-based mutations as state changes (`_is_state_change` currently ignores `kubectl exec`).

## 7. Infrastructure notes

- Disk stayed above 949 GB free; the quota watchdog read 90% throughout and no lane hit its 1.5x cap (the reading did not
  move during the run, so it may be coarse). No lane needed relaunching.
- The controller log and receipts are the source for the timelines in section 1.1 (`phase1_timeline.py`); the
  injection anchor is the harness's `fault_injected_at`.
- The `assure-w0..w7` clusters were left in place (reuse mode).

## 8. Overall takeaways

- **Meaning.** On symptomatic faults SDO does what the paper claims against a verify-only baseline: about 2-4x faster to
  mitigate, about half the weighted tokens on repeats, and never misled by decoys. On latent faults it fails
  silently, because its detectors and health gate share the blind spot the fault creates, and Codex fails too for a
  different reason (the decoy).
- **Confidence.** Direction: moderate. Magnitudes: low (n = 4 pipelines, 5 attempts). The latent-fault result: high.
- **Implication.** SDO's claims C2, C3 and C11 hold at small scale. C1 and C5 are directional. C4 and C8 fail on
  false closures that the latent NetworkPolicy fault produces. Do not report a headline solve rate without splitting the
  latent problems out and stating that the arms had different information.
- **Next step.** (1) Fresh-dial probe and NetworkPolicy state detector, then rerun S3 and K1. (2) Composite playbook
  synthesis, then rerun K2. (3) Fix the production-receipt rejection. (4) Only then run phase 2.
