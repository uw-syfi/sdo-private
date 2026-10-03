# Configuration boundaries

The production SDO lifecycle does not expose switches for the removed analysis, shell-script generation, bounded monitoring, or standalone fault-injection phases. The public interface is:

```text
sdo operate REPOSITORY --namespace NAMESPACE (--goal TEXT | --goal-file PATH)
  [--application NAME] [--model MODEL]
  [--controller-image IMAGE] [--responder-image IMAGE] [--validator-image IMAGE]
  [--repository-pvc PVC] [--credentials-secret SECRET]
  [--attempts N] [--timeout-seconds N]
```

Use `sdo operate --help` for descriptions and current defaults.

## Reflection guidance

`--reflection-guidance {baseline,generalize,generalize-spec}` (`ControllerInstallConfig.reflection_guidance`, broker argument of the same name; SREGym `agent_config.sdo_codex.reflection_guidance`) selects the learning guidance of outcome-driven reflection. `baseline` (default) keeps per-cause learning: a sharp playbook and incident detector for each confirmed cause. `generalize` adds a generalization protocol to the reflection request: compare the confirmed signature with every existing incident detector, widen a same-root-cause-class detector so it keys on the class-level condition and reports the affected resource through `Finding.ParameterBindings`, parameterize the existing playbook with role placeholders, and create a new detector or playbook only for a different root cause, keeping a near-miss test. It also makes the structured-handoff brief (`--reflection-session fresh`) list each existing incident detector's Spec description, whether it sets parameter bindings, and a bounded excerpt of its `Detect` predicate. `generalize-spec` is a superset of `generalize` that adds a spec-first protocol: prefer a detector predicate decidable from the current spec or status of the resources involved (visible the moment the fault exists) over one that needs later-produced evidence; events, logs, and metrics only corroborate; the matching test must include a snapshot without any event objects when the signature is spec-decidable, plus the near-miss test; a cause that is not spec-decidable is stated in the summary and uses the earliest available evidence. Its brief also states, per incident detector, whether the detector source reads Event objects. `baseline` and `generalize` prompts are unchanged by it. The flag changes only reflection prompt wording and brief content, so arms can share images. Prompt and static brief wording is kept problem-agnostic; a unit test scans it for problem-specific terms.

## Late findings

`--late-findings {off,pull}` (`ControllerInstallConfig.late_findings`, broker argument of the same name; SREGym `agent_config.sdo_codex.late_findings`; fastloop `--late-findings`) lets a responder pull findings that activated after it was dispatched. `off` (default) changes nothing: the responder prompt, controller arguments and closures are byte-identical to before. `pull` adds a short, problem-agnostic paragraph to the responder instructions (environment `SDO_LATE_FINDINGS=pull` on the responder Job) telling it to run `python3 -m sdo.operational_memory.late_findings --incident-id ID` (also `sdo incident late-findings --incident-id ID`) once at the start and again before its first change to the cluster or repository, and to treat returned playbooks as hypotheses to confirm against live state. The command reads the controller's durable firing stream on the repository volume (`/workspace/.sdo-runtime/telemetry/detector-firings.jsonl`), keeps only `activated` records of its own incident whose `dispatch_relation` is `after_dispatch`, keeps only playbook paths that exist in the worktree, and prints compact JSON. It needs no dispatch delay, no controller push and no extra transport; it is read-only apart from a one-line pull receipt beside the stream. The broker folds the receipts into the closure as `late_findings_pull`, and the SREGym receipt reports it with `late_finding_consumed`. The stream must be enabled (default in job mode); without it the command reports `telemetry_available: false` and an empty list.

## Follow-up responders

`--max-follow-ups N` (controller flag; `ControllerInstallConfig.max_follow_ups`; SREGym `agent_config.sdo_codex.max_follow_ups`; fastloop `--max-follow-ups`) and `--follow-up-cooldown` (`follow_up_cooldown_seconds`, default 30) let the controller keep working when health findings stay active after a responder completes. `0` (default) changes nothing: the controller records `detector_review_required` after the verification timeout and exits, as before. With `N > 0`, once a responder has completed and `follow-up-cooldown` (capped by the verification timeout) has elapsed, any still-active finding of a health detector produces a follow-up responder request instead: a new incident id in the same open incident, carrying only the residual findings and a `follow_up` context (`original_incident_id`, `parent_incident_id`, `attempt`, `max_follow_ups`, and a bounded `prior_responder_summary` of the earlier responders' status, root causes, and repairs). The responder prompt scopes the follow-up to those findings. At most `N` follow-ups run per original incident; the attempt number is part of the persisted request, so a Job restart resumes with the same budget. When the budget is spent the controller falls back to `detector_review_required`. One closure is cut for the last request in the chain, when health detectors finally clear; its request carries the `follow_up` chain. Health detectors stay judge-owned and the controller dispatches without calling an LLM. The cooldown should exceed the health detectors' clear window (clear threshold times interval), or a finding that is about to clear can still trigger a follow-up.

## Healthy-baseline detector gate

`--healthy-baseline DIR` (`sdo-detector-check test|draft-test`; broker `--healthy-baseline-dir DIR`; `healthy_baseline=` on `ContainerSandboxRunner`, `KubernetesJobSandboxRunner` and `LocalSandboxRunner`) makes detector validation replay every incident-class detector on recorded snapshots of the healthy application and reject any detector that reports an active finding on them. `DIR` is relative to the validated tree (the worktree) and must stay inside it; it holds one `*.json` file per snapshot, in the `sdktest.Snapshot` shape (`namespace`, `configMaps`, `services`, `pods`, `deployments`, `replicaSets`, `endpoints`, `endpointSlices`, `networkPolicies`, `events`, each a list of ordinary Kubernetes objects; unknown fields are an error). Unset (default) changes nothing: no extra test is generated, container and Job arguments are unchanged, and the validation identity is unchanged. Health detectors are exempt (the health judge owns them). The check is deterministic Go on the validator's existing no-network path; it reads no benchmark verdict. Note that `lifecycle_validation_cache` keys on the validator identity (which now differs when the gate is on) and the `.sdo/diagnostics` digest, not on the baseline files.

`--healthy-baseline` (fastloop `run`; SREGym `agent_config.sdo_codex.healthy_baseline`; `ControllerInstallConfig.healthy_baseline`, default off) supplies the snapshots in live runs. The harness records three snapshots of the application namespace immediately before it injects a fault (`benchmarks/sregym/adapter/healthy_baseline.py`), writes them to `/workspace/.sdo-baseline/healthy` on the controller's repository volume, and the controller passes the broker `--healthy-baseline-source /workspace/.sdo-baseline/healthy --healthy-baseline-dir .sdo-baseline/healthy`; the broker stages the files into the validated worktree only while the detector gate runs and removes them afterwards, so they are never memory artifacts. Needs rebuilt controller and validator images. A failed capture aborts before injection, and a missing source fails validation. Default off changes nothing (controller arguments are byte-identical). See `docs/healthy-baseline-live-decisions.md`.

## Cause admissibility

`SDO_CAUSE_ADMISSIBILITY` (`on` | `off`, default `on`) on the responder Job selects the responder-side cause-admissibility gate (`sdo/agent_runtime/responder/cause_admissibility.py`). The gate runs strictly upstream of the controller's deterministic `verify_diagnosis`: it can only *remove* a confirmed root cause the responder proposed, never relax, duplicate, or substitute for the fenced verifier, and never cause a confirmation the verifier would not otherwise make. A cause is withheld only when its entire evidentiary basis is unverifiable narrative — it cites only `live-observation` evidence and explains no detector that fired for this incident; a cause that cites a corroborating-kind item (`detector-finding`, `synthetic-traffic`, or `state-change`) or explains a detector that fired is admitted, so every component of a genuine composite survives. Admissibility reads only the responder's `IncidentResult` and the controller's `IncidentRequest`; it reads no benchmark verdict and encodes no fault-specific knowledge. `on` also adds a problem-agnostic admissibility bar to the responder prompt (route a benign configuration drift to `static_context`, do not assert a purely narrative cause); a unit test scans it for problem-specific terms. `off` restores byte-identical pre-gate behavior (no prompt paragraph, no filtering) for A/B comparison. The gate is applied once at the `execute_incident` choke point, so every caller sees the gated result. See `docs/cause-admissibility-decisions.md`.

## SREGym experiment configuration

SREGym configuration remains benchmark-specific. Parsing and resolved defaults live under `benchmarks/sregym/runner/`, with complete examples under `benchmarks/sregym/experiments/`.

`preserve_infrastructure = true` retains shared storage and observability services across attempts after verifying an in-cluster readiness marker. It changes only benchmark setup and cleanup; application resources and injected faults are still reset. Persistent application workspaces remain separately controlled by `application_workspace = "persistent"`.

`defer_diagnosis_grading = true` (conductor env `SREGYM_DEFER_DIAGNOSIS_GRADING=1`) opens the mitigation stage as soon as the diagnosis is submitted and grades the diagnosis with the same judge in the background. Teardown waits for the verdict, so results keep both verdicts and add `diagnosis_grading_deferred = true`; `TTM` then excludes judge latency.

`fast_namespace_teardown = true` (`SREGYM_FAST_NAMESPACE_TEARDOWN=1`) deletes a terminating application namespace's pods with a zero grace period during conductor cleanup instead of waiting out each pod's termination grace. Fault recovery, PV cleanup, and baseline reconciliation are unchanged.

`source_build_cache = true` (`SREGYM_SOURCE_BUILD_CACHE=1`) labels source-built application images with a digest of their build context (excluding `.git`, `.sdo`, and `.sdo-runtime` at its root) and reuses the local image when the digest is unchanged, still loading it into the kind cluster.

`lifecycle_validation_cache = true` sets `SDO_LIFECYCLE_VALIDATION_CACHE_DIR` to `.sdo-runtime/lifecycle-validation-cache` in the checkout. Lifecycle reuse then shares passing detector-validator verdicts across pipelines under the attestation's own key (validator image identity and `.sdo/diagnostics` digest), so a stage 0 that starts from an unchanged seed skips revalidation. Receipts record the outcome in `lifecycle_validation`.

Common sections are:

| Section | Purpose |
|---|---|
| `[runner]` | Agent, model, parallelism, problem selection, summaries, and application-workspace behavior |
| `[runner.variants]` | Variant generation, ordering, limits, and seeds |
| `[runner.env]` | Benchmark worker and judge environment |
| `[agent.crucible]` | Legacy Crucible agent diagnosis, mitigation, private memory, reflection, and iteration settings |
| `[agent.sdo_codex]` | SDO benchmark-adapter model and timeout settings |
| `[pipeline]`, `[defaults]`, `[[stages]]` | Multi-stage experiments and per-stage overrides |

Do not move benchmark options into production configuration. In particular, benchmark results, fault identities, judge models, submission limits, task lists, and variant controls must remain behind the SREGym boundary.

Crucible flags configure only `benchmarks/sregym/agents/crucible/`; they are not supported production SDO flags. Production agent behavior is configured through `sdo operate` and subsystem-owned SDO contracts.

Configuration snapshots emitted by benchmark runs are preferable to the original input file when analyzing results because they record resolved defaults and stage overrides.
