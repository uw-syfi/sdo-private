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
