# Composite stream (10 composites, all fixes on): decisions and results

Status: 2026-10-01, in progress (sequences cstream-a and cstream-b done; cstream-c, d, e and the Codex runs on C4/C5 running). Branch `vic/exp/composite-stream` (from `vic/exp/healthy-baseline-ab`), worktree `/mnt/data/shli/sdo-worktrees/composite-stream`. Predecessors: `docs/healthy-baseline-ab-decisions.md`, `docs/cold-c2-selector-learning-decisions.md`, `docs/simultaneous-composites-decisions.md`, `docs/composite-learning-curve-decisions.md`, `docs/nfault-composites-decisions.md` (these live in sibling worktrees).

## Questions

1. With every fix on, does cost per composite (tokens, time) trend down along a 10-composite stream (per-position medians across sequences)?
2. Does memory growth (incident detectors / playbooks) stay compact?
3. Resolution rate versus Codex + verify.

## Decisions

- Stream (fixed, same for every sequence): `C1 C2 C3 C1 C4 C2 C3 C5 C1 C4` with C1 = `composite3_hotel_geo_rate_recommendation`, C2 = `composite3b_hotel_profile_mongodb_geo_recommendation`, C3 = `composite5_hotel_geo_rate_recommendation_frontend_user`, and two new hand-registered variants (one fault per Deployment, no generator): C4 = `composite3c_hotel_rate_mongodb_geo_user` (readiness rate, ConfigMap mongodb-geo, network policy user), C5 = `composite4_hotel_profile_rate_recommendation_frontend` (readiness profile, ConfigMap mongodb-rate, network policy recommendation, wrong selector frontend). ConfigMap targets are limited to mongodb-geo and mongodb-rate by the injector, so variation comes from the readiness and network-policy targets and from mixing the other families' components. Positions 4, 6, 7, 9, 10 repeat a composite (C1 x3, C2 x2, C3 x2, C4 x2), position 8 (C5) is new but reuses components.
- Arm "SDO all-on": `--inject-before-resume`, `--late-findings pull --max-follow-ups 3 --follow-up-cooldown-seconds 30`, `--reflection-guidance generalize --reflection-session fresh`, `--healthy-baseline`, images `nf5`, seed `lifecycle-stream`, Codex gpt-6-luna, fast loop, probe-graded (no LLM judge, no judge time in any timing). Script `benchmarks/sregym/experiments/composite-stream/seq_stream.sh` = `healthy-baseline-ab/seq_ab.sh` with gate on, pull, 3 follow-ups fixed and the worktree path changed. One persistent controller and `.sdo` per sequence; app redeployed (`up --redeploy`) before each composite; the live frontend pod label that an earlier responder added is removed before every composite after the first so a repeated `wrong_selector:frontend` is a real fault (selector-repeat design problem, `cold-c2-selector-learning-decisions.md`). Other persisted source fixes are not reset; inert injections are detected from `ever_red` per fault and reported.
- Baseline: Codex gpt-6-luna + verify protocol. Stored results reused for C1, C2, C3 (not rerun): C1 0/4 (verify, `nfault`), C2 0/2 (`composite-learning-curve`), C3 0/4. New Codex runs only on C4 and C5, n=2 each, on a freshly redeployed app per run (`codex_stream.sh`).
- Concurrency: at most 2 jobs at once (`run_stream_queue.sh`), waits up to 30 min while load > 20. Clusters `cl-w150`+ for first attempts, `cl-w170`+ for infra reruns; all deleted at the end. Infra failures (kind create, openebs/Calico, `ControllerInstallError`, etcd i/o timeouts before the first injection) are classified separately and rerun.
- Raw runs: `/mnt/data/shli/clc-runs/cstream-*` (SDO) and `cstream-codex*`.
- The submodule commit adding C4/C5 (`03b1df58`) lives in a private copy of the submodule git dir (`/mnt/data/shli/sdo-worktrees/.composite-stream-sregym-gitdir`, branch `vic/exp/composite-stream`); no remotes changed.

## Results, sequences cstream-a and cstream-b (n=2 so far; c, d, e to be appended)

Both sequences ran concurrently on clusters cl-w150/cl-w151, host load 6 to 17, no infra failures, no reruns. Tokens are responder plus reflection. `last-fault s` = injection end to the last fault's final green probe; `inj->mit s` = controller's injection-to-last-repair (judge time never included). "learned detectors before dispatch" lists the fault components for which a learned (non-health) incident detector activated before the first dispatch of that composite. "off-target" = a learned detector fired on a target outside the composite's fault components (the false-firing check). Memory = incident detectors / playbook directories in `.sdo` at the end of the composite. `follow-ups` = extra incidents beyond the first. Raw table: `/mnt/data/shli/clc-runs/cstream-ab-table.md`, aggregate `cstream-agg.json`, script `analyze_stream.py`.

| seq | pos | C | solved | oracle | last-fault s | inj->mit s | tokens | follow-ups | learned detectors before dispatch (fault components) | learned fired off-target | gate rejections | memory inc.det/playbooks after | inert | stop/err |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cstream-a | 1 | C1 | 3/3 | True | 57 | 93 | 0.94M | 0 | none | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-a | 2 | C2 | 3/3 | True | 68 | 81 | 0.30M | 0 | mongodb-geo, recommendation | none | 0 (+0 other) | 2/3 | - | all_faults_resolved  |
| cstream-a | 3 | C3 | 5/5 | True | 380 | 407 | 0.84M | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 4 | C1 | 3/3 | True | 68 | 80 | 0.33M | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 5 | C4 | 3/3 | True | 151 | 164 | 0.48M | 0 | mongodb-geo, user | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 6 | C2 | 3/3 | True | 94 | 108 | 0.50M | 0 | mongodb-geo, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 7 | C3 | 5/5 | True | 96 | 125 | 0.46M | 0 | frontend, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 8 | C5 | 4/4 | True | 100 | 120 | 0.47M | 0 | frontend, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 9 | C1 | 3/3 | True | 57 | 88 | 0.39M | 0 | mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-a | 10 | C4 | 3/3 | True | 73 | 95 | 1.72M | 0 | mongodb-geo, user | none | 0 (+0 other) | 4/5 | - | all_faults_resolved  |
| cstream-b | 1 | C1 | 3/3 | True | 136 | 308 | 2.51M | 0 | none | none | 0 (+0 other) | 1/2 | - | all_faults_resolved  |
| cstream-b | 2 | C2 | 3/3 | True | 171 | 209 | 1.97M | 0 | mongodb-geo | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 3 | C3 | 5/5 | True | 161 | 211 | 1.32M | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 4 | C1 | 3/3 | True | 78 | 84 | 0.52M | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 5 | C4 | 3/3 | True | 99 | 110 | 0.34M | 0 | mongodb-geo, rate, user | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 6 | C2 | 3/3 | True | 68 | 56 | 0.30M | 0 | mongodb-geo, profile, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 7 | C3 | 5/5 | True | 176 | 230 | 0.94M | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 8 | C5 | 4/4 | True | 100 | 85 | 0.52M | 0 | mongodb-rate, profile, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 9 | C1 | 3/3 | True | 78 | 94 | 0.49M | 0 | geo, mongodb-rate, recommendation | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |
| cstream-b | 10 | C4 | 3/3 | True | 73 | 130 | 1.16M | 0 | mongodb-geo, rate, user | none | 0 (+0 other) | 3/4 | - | all_faults_resolved  |

| pos | composite | n | all solved (probes) | oracle True | median tokens | median inj->mit s | median last-fault s | median follow-ups | median memory det/pb after |
|---|---|---|---|---|---|---|---|---|---|
| 1 | C1 | 2 | 2/2 | 2/2 | 1.73M | 201 | 96 | 0.0 | 1.5/2.5 |
| 2 | C2 | 2 | 2/2 | 2/2 | 1.13M | 145 | 120 | 0.0 | 2.5/3.5 |
| 3 | C3 | 2 | 2/2 | 2/2 | 1.08M | 309 | 270 | 0.0 | 3.0/4.0 |
| 4 | C1 | 2 | 2/2 | 2/2 | 0.43M | 82 | 73 | 0.0 | 3.0/4.0 |
| 5 | C4 | 2 | 2/2 | 2/2 | 0.41M | 137 | 125 | 0.0 | 3.0/4.0 |
| 6 | C2 | 2 | 2/2 | 2/2 | 0.40M | 82 | 81 | 0.0 | 3.0/4.0 |
| 7 | C3 | 2 | 2/2 | 2/2 | 0.70M | 178 | 136 | 0.0 | 3.0/4.0 |
| 8 | C5 | 2 | 2/2 | 2/2 | 0.50M | 102 | 100 | 0.0 | 3.0/4.0 |
| 9 | C1 | 2 | 2/2 | 2/2 | 0.44M | 91 | 68 | 0.0 | 3.0/4.0 |
| 10 | C4 | 2 | 2/2 | 2/2 | 1.44M | 112 | 73 | 0.0 | 3.5/4.5 |

| seq | cumulative tokens (M) after pos 1..N | cumulative inj->mit min |
|---|---|---|
| cstream-a | 0.9 1.2 2.1 2.4 2.9 3.4 3.8 4.3 4.7 6.4 | 2 3 10 11 14 16 18 20 21 23 |
| cstream-b | 2.5 4.5 5.8 6.3 6.7 7.0 7.9 8.4 8.9 10.1 | 5 9 12 14 15 16 20 22 23 25 |

| composite | first occurrence tokens (median) | repeat tokens (median) | first inj->mit | repeat inj->mit |
|---|---|---|---|---|
| C1 | 1.73M (n=2) | 0.44M (n=4) | 201 | 86 |
| C2 | 1.13M (n=2) | 0.40M (n=2) | 145 | 82 |
| C3 | 1.08M (n=2) | 0.70M (n=2) | 309 | 178 |
| C4 | 0.41M (n=2) | 1.44M (n=2) | 137 | 112 |
| C5 | 0.50M (n=2) | -M (n=0) | 102 | - |

Headline for these two sequences: 20 of 20 composites resolved by probes and by the official oracle (C1..C5, positions 1..10 in both), 0 follow-up incidents, 0 off-target (false-firing) learned detectors, 0 gate rejections, 0 inert injections (`ever_red` true for every fault).

### Cost along the stream (per-position medians, n=2)

- Tokens: 1.73M (pos 1, cold C1), 1.13M, 1.08M, then 0.43M at pos 4 and 0.40 to 0.50M at positions 5, 6, 8, 9; positions 3, 7 (C3, 5 faults) 1.08M then 0.70M; position 10 (C4 repeat) is 1.44M, driven by cstream-a's 1.72M (cstream-a created a 4th incident detector / 5th playbook at that composite, so that run paid for a reflection that produced new memory; cstream-b's pos 10 is 1.16M).
- inj->mit s: 201, 145, 309, 82, 137, 82, 178, 102, 91, 112. Pos 1 to 3 (cold or new family) median 201 s; positions 4 to 10 median about 102 s.
- Grouped by first occurrence versus repeat of each composite (median over the two sequences): C1 1.73M / 201 s first, 0.44M / 86 s repeat (n=4 repeats); C2 1.13M / 145 s versus 0.40M / 82 s; C3 1.08M / 309 s versus 0.70M / 178 s; C4 first 0.41M / 137 s versus repeat 1.44M / 112 s (the first C4 at pos 5 already benefits from memory learned on C1 to C3, and the repeat at pos 10 contains the one costly reflection); C5 (new at pos 8) 0.50M / 102 s.
- Cumulative tokens: cstream-a 0.9, 1.2, 2.1, 2.4, 2.9, 3.4, 3.8, 4.3, 4.7, 6.4M; cstream-b 2.5, 4.5, 5.8, 6.3, 6.7, 7.0, 7.9, 8.4, 8.9, 10.1M. Mean cost of the first three composites is about 1.4M each, of the last seven about 0.6M each (including the pos 10 outlier).

### Memory growth

Incident detectors / playbooks after the composite: cstream-a 2/3, 2/3, 3/4, 3/4, 3/4, 3/4, 3/4, 3/4, 3/4, 4/5; cstream-b 1/2, 3/4, 3/4 and flat at 3/4 through position 10. Memory saturates at 3 detectors (ConfigMap, network policy, and a readiness or selector one) after the first three composites and stays flat for seven more composites in b and six in a, with one late addition in a (position 10). Learned detectors for the ConfigMap and network-policy faults fire before dispatch from position 2 on in both sequences; after C3, learned selector (a) or readiness-port (b) detectors also appear in the "before dispatch" set.


## Resolution versus Codex + verify

| composite | SDO all-on (so far) | Codex + verify (stored, not rerun) | Codex + verify (new, this experiment) |
|---|---|---|---|
| C1 (3 faults) | 6/6 probes, 6/6 oracle (3 positions x 2 sequences) | 0/4 | - |
| C2 (3 faults) | 4/4, 4/4 | 0/2 | - |
| C3 (5 faults) | 4/4, 4/4 | 0/4 | - |
| C4 (3 faults, new) | 4/4, 4/4 | - | running (n=2) |
| C5 (4 faults, new) | 2/2, 2/2 | - | running (n=2) |

Stored Codex never repaired the network-policy fault (20 of 20 earlier Codex runs). Codex token cost was 0.3 to 0.9M per run versus SDO's 0.3 to 2.5M (SDO's later positions are in the same range as Codex).

## Takeaways (preliminary, after n=2 sequences)

1. All fixes on resolve a long persistent stream: 20 of 20 composites (0 follow-ups needed, 0 gate rejections, 0 false-firing learned detectors). Meaning: with pull-before-act and simultaneous injection the first responder already gets every finding, and the healthy-baseline gate did not get in the way. Confidence: medium (n=2 sequences, 20 composites, one model, fast loop with probe grading). Implication: resolution rate is not the differentiator inside SDO any more; it is the Codex gap (0 of 10 on the shared composites). Next step: n>=3 and the Codex runs on C4/C5.
2. A cost learning curve exists in tokens, not clearly in time. Meaning: the first three composites cost about 1.4M tokens each and later positions 0.4 to 0.7M (about 3x), matching the cold-versus-warm C2 result; inj->mit drops from about 200 s (pos 1 to 3) to about 100 s afterwards but is noisy (pos 7 C3 178 s, pos 10 112 s). Confidence: medium for tokens, low for time (n=2, per-position ranges overlap). Implication: report the token curve with the saturation point (about position 3 to 4), and treat speed as unproven. Next step: wait for n=3 to 5 medians.
3. Memory growth is compact. Meaning: 3 detectors / 4 playbooks by position 3 and flat through position 10 in both sequences (one late increment to 4/5 in a). Confidence: medium. Implication: no sign of detector sprawl or accumulating false-firers over ten composites. Next step: a longer stream (20+) with new families to see whether saturation breaks.

## Caveats

- n=2 sequences at this point; concurrent sequences on one shared host (load 6 to 17); no LLM judge; probe-graded with the official oracle on the end state; images nf5.
- Position medians mix different composites by design (the stream is fixed), so per-position comparisons are confounded by composite size (C3 has 5 faults, C5 4); the first-versus-repeat table controls for that but with n=2.
- Source fixes by earlier responders persist through `up --redeploy`; only the frontend label is reset live. No inert injection was recorded in a or b.
