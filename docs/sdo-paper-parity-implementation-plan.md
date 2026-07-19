# SDO Paper-Parity Implementation Plan

## Purpose

This document sketches how to evolve the current SDS repository into the complete Self-Defining Operator (SDO) described in `sdo_paper/`.

The current branch already contains useful pieces:

- source deployment and independent health judging in `app_operator/`;
- an autonomous incident responder in `sregym_agents/cli_agent/`;
- a typed Go detector SDK and one-shot runtime in `observer/`;
- durable playbooks and helper scripts under `.sds/playbooks/`;
- persistent application workspaces and SREGym experiment orchestration.

The goal is to connect those pieces into one continuously running operator and implement the paper's missing control-plane guarantees: detector scheduling, persistent-finding dispatch, five-part operational memory, validated commits, outcome-driven refinement, and architecture upkeep.

## Definition of paper parity

The implementation is paper-faithful when all of the following are true:

1. A human supplies a natural-language health objective and an application source repository.
2. A deployer and an independent health judge iteratively bring the application to a verified healthy Kubernetes deployment.
3. The judge compiles the health objective into deterministic health detectors.
4. A long-running Kubernetes controller evaluates health and fault-specific detectors without an LLM in the steady-state path.
5. Only findings that satisfy the configured persistence policy dispatch a responder.
6. One responder session can diagnose and mitigate multiple concurrent faults, verify recovery, and reflect while its incident context is still available.
7. Reflection can propose playbooks, detector code, scripts, architecture updates, and detector refinements.
8. A trusted commit broker validates every proposed memory change before it reaches the application repository.
9. The controller records detector-to-playbook outcomes in an append-only log and exposes them to later reflection.
10. Architecture memory is tied to a source commit and refreshed when topology-relevant changes occur.
11. Every learned behavior is readable, editable, versioned, and attributable to an incident session.
12. LLM usage in steady state is zero; model calls occur only during deployment, incident response, reflection, or topology-relevant memory upkeep.

## Recommended compatibility decision

The paper uses `.sdo/`, while the current implementation uses `.sds/`. Make `.sdo/` the canonical application-local operational-memory root and support `.sds/` as a read-only compatibility source during migration.

Recommended migration behavior:

- New deployments write only `.sdo/`.
- Existing `.sds/` applications continue to run through a compatibility reader.
- `operator migrate-memory <app>` copies recognized artifacts into `.sdo/`, validates them, and creates a migration commit.
- If both roots exist, `.sdo/` wins and the operator emits a warning rather than merging implicitly.
- Remove `.sds/` compatibility only after all experiment fixtures and application workspaces have migrated.

If retaining `.sds/` is preferred for product naming, update the paper and appendix instead. Do not maintain two writable roots.

## Target architecture

```text
Human objective + application repository
                  |
                  v
        Python agent runtime
        - deployer
        - independent health judge
        - incident responder/reflection
        - commit broker client
                  |
          validated Git commits
                  |
                  v
        Application repository
        - source/deployment artifacts
        - .sdo operational memory
                  |
          build and redeploy
                  |
                  v
        Go SDO controller
        - Kubernetes informer cache
        - typed snapshot builder
        - detector scheduler
        - persistence/debounce state
        - incident batcher/deduplicator
        - responder dispatcher
        - outcome recorder
                  |
                  v
           Live Kubernetes cluster
```

Keep the split between the Python agent runtime and Go controller explicit. They should communicate through a small, versioned protocol rather than importing each other's implementation details.

Build one generated detector registration binary or image per application, using the current temporary-workspace generator as the starting point. An accepted detector change should cause the commit broker to build the new artifact and roll out the controller revision. Avoid Go plugins: a normal compiled binary keeps builds reproducible and matches the paper's redeploy-to-register model.

### Controller-to-agent protocol

Define a versioned `IncidentRequest` payload containing:

- application and namespace identity;
- incident/session ID;
- triggering detector findings and their evaluation history;
- surfaced playbook paths and proposed parameter bindings;
- current source commit and deployed commit;
- architecture-summary path and health-objective path;
- repository checkout/worktree location;
- response deadline and cancellation token.

Define a corresponding `IncidentResult` containing:

- confirmed root causes;
- applied playbooks and scripts;
- repair/source changes;
- final detector states;
- proposed memory changes;
- verification evidence;
- completion, failure, or cancellation status;
- usage and timing metrics.

Use Pydantic v2 models on the Python side and generated or hand-maintained Go structs with golden JSON contract tests. The controller may initially invoke the agent runtime as a subprocess or Kubernetes Job; the wire contract should allow a later HTTP/gRPC transport without changing semantics.

## Canonical operational-memory layout

```text
.sdo/
  goal.md                         # Human-authored health objective
  arch.md                         # Agent-authored architecture summary
  outcomes.jsonl                  # Controller-authored append-only outcomes
  schema-version                  # On-disk schema version

  diagnostics/
    manifest.yaml                 # Detector registration and ownership
    go.mod
    detectors/
      health/                     # Health-judge-owned detectors
        <detector>/detector.go
      incidents/                  # Responder-authored fault detectors
        <detector>/detector.go

  playbooks/
    README.md                     # Concise routing index
    <fault-class>/
      README.md                   # Decide, diagnose, mitigate, verify
      scripts/
        diagnose.sh
        mitigate.sh
        verify.sh
```

### Artifact ownership

Enforce ownership in the commit broker:

- Humans own `goal.md`.
- The deployer owns deployment artifacts and may propose `arch.md` updates.
- The health judge owns health detectors.
- Responders own incident playbooks, incident detectors, and playbook-local scripts.
- The controller alone appends `outcomes.jsonl`.
- The upkeep workflow owns artifact review-status fields and commit-tag refreshes in `arch.md`.

A responder must not weaken `goal.md` or health-detector success criteria to declare an incident resolved.
The controller authors outcome records, but the commit broker still performs the corresponding Git write so there is only one validated repository mutation path.

## Core data models

Introduce structured models before implementing orchestration. Avoid passing reusable shapes as raw dictionaries.

### Architecture summary metadata

Store Markdown with validated front matter:

```yaml
schema_version: 1
generated_at_commit: <git-sha>
generated_at: <timestamp>
application: <stable-id>
topology_fingerprint: <hash>
```

The body should describe components, build graph, deployment units, public entry points, dependencies, configuration contracts, and health flows.

### Outcome record

Each JSONL record should include:

- schema version and incident ID;
- source and deployed commit;
- detector evaluation history;
- findings surfaced at dispatch;
- playbooks surfaced, inspected, confirmed, rejected, and applied;
- confirmed root causes;
- final health-detector state;
- outcome classification: success, partial, false positive, false negative, failed, or cancelled;
- repair and memory commit SHAs;
- responder backend/model and usage;
- timestamps for detection, dispatch, mitigation, verification, and completion.

The controller should derive FP/FN labels from structured facts where possible. The responder may provide explanations but must not be the sole authority for the classification.

### Detector manifest

Extend the existing manifest with:

- stable detector ID and package;
- detector class: `health` or `incident`;
- resource watches;
- evaluation interval;
- required consecutive firing and clearing counts;
- severity and batching policy;
- declared possible playbooks;
- owner and originating incident/commit;
- optional telemetry dependencies.

Validate that manifest declarations match `Detector.Spec()` at build time.

## Phased implementation

Every phase follows red/green TDD: add contract or reproducing tests first, implement the smallest behavior that passes, then run `bash scripts/format_code.sh`, `bash scripts/check_errors.sh`, and the relevant test suites.

### Phase 0: Freeze contracts and make the repository reproducible

Goals:

- Record architecture decisions for the memory root, controller-agent transport, and detector deployment model.
- Restore a complete `agentshim` dependency checkout or switch to a resolvable package source.
- Establish the cross-language protocol and on-disk schema versions.
- Add a paper-parity feature matrix to CI-visible documentation.

Tests first:

- A clean checkout can run Python lint and unit tests.
- Golden JSON fixtures round-trip through Python and Go protocol models.
- `.sdo` and legacy `.sds` resolution follows the compatibility rules.

Exit criteria:

- `uv sync`, lint, Python tests, and Go tests work from a clean checkout.
- Architecture decisions are documented and accepted.
- No later phase needs to invent a conflicting artifact path or protocol shape.

### Phase 1: Complete the detector SDK and snapshot contract

Goals:

- Add the Kubernetes resources required by the paper and current playbooks: ConfigMaps, Secrets metadata, StatefulSets, DaemonSets, Jobs, PVCs, resource quotas, ingresses, and network policies.
- Keep secret values inaccessible by default; detectors should receive metadata and explicitly approved fields only.
- Make snapshots immutable from the detector API's perspective.
- Add deterministic ordering so identical cluster state produces byte-stable findings.
- Validate findings, severities, object references, fingerprints, metadata serialization, and declared playbook membership.

Tests first:

- A missing-ConfigMap detector matching the paper's example can be expressed and tested with `sdktest`.
- Snapshot helper tests cover selector matching, ready endpoints, events, owners, volumes, probes, and ConfigMap references.
- The same logical snapshot in different Kubernetes list orders yields identical findings.
- Invalid or undeclared playbook references fail validation.
- A detector cannot mutate cached Kubernetes objects through returned slices/maps.

Exit criteria:

- The paper's detector example compiles against the public SDK.
- Existing benchmark fault classes can be represented without application-specific additions to the SDK.
- SDK and core modules have focused unit tests for every accessor and validation rule.

### Phase 2: Turn the observer into a long-running controller

Goals:

- Replace repeated full-list `RunOnce` polling with informer-backed caches and typed snapshots.
- Consume each detector's declared resource watches and interval.
- Evaluate only detectors affected by a resource change or timer.
- Track finding fingerprints across evaluations.
- Require configurable consecutive firing samples before dispatch and consecutive clear samples before resolution.
- Continue operating if one detector errors; surface detector health separately.
- Support graceful shutdown, leader election, and restart recovery.

Tests first:

- Unit tests for scheduling, watch-to-detector routing, persistence thresholds, clearing thresholds, fingerprint deduplication, and error isolation.
- `envtest` integration tests proving that Kubernetes changes trigger only the expected detectors.
- Restart tests proving active findings and incident locks recover from durable state.
- Load tests for hundreds of detectors and realistic namespace object counts with zero LLM calls.

Exit criteria:

- A controller Deployment can run indefinitely against a namespace.
- Transient findings never dispatch under the default policy.
- Detector intervals and watches have observable, tested effects.
- Steady-state telemetry reports zero model usage.

### Phase 3: Add incident batching and responder dispatch

Goals:

- Batch simultaneous findings into one incident session.
- Prevent duplicate responders for the same active finding set.
- Allow new findings to join an active session through a controlled update channel.
- Dispatch the existing CLI-agent responder through the versioned protocol.
- Supply detector history, playbooks, architecture, and goal as initial context.
- Make responder completion depend on health detectors clearing, not on self-declaration alone.
- Support explicit human dispatch and record it as a potential false negative.

Tests first:

- Two detectors for independent faults produce one incident with two findings.
- Repeated evaluations do not launch duplicate responders.
- A manually reported incident with no detector becomes an FN candidate.
- A responder claiming success while health detectors still fire remains active.
- Cancellation and timeout release locks without losing the outcome record.

Exit criteria:

- A fault injected into a live test cluster automatically dispatches exactly one responder.
- Compound faults are handled in one session.
- The incident closes only after independent health verification succeeds.

### Phase 4: Implement the transactional memory commit broker

Goals:

- Stop allowing agents to commit operational memory directly.
- Give each agent session an isolated Git worktree.
- Accept a proposed patch plus structured provenance through an MCP tool or local API.
- Validate the complete candidate tree before committing.
- Commit accepted source repairs and memory changes with incident/session metadata.
- Reject invalid changes without modifying the application branch.

Validation pipeline:

1. Validate on-disk schema versions and Markdown front matter.
2. Validate artifact ownership and forbidden edits.
3. Validate playbook index links and script paths.
4. Validate detector manifest/package correspondence.
5. Run Go formatting, tests, compilation, static analysis, and registration checks.
6. Run detector unit tests against captured incident fixtures.
7. Validate every possible and emitted playbook reference.
8. Check health detectors and `goal.md` were not weakened by a responder.
9. Validate shell scripts for syntax and repository path containment.
10. Commit atomically only after every gate passes.

Tests first:

- Invalid Go, missing playbooks, dead index links, path traversal, symlinks, forbidden goal edits, and failing detector tests all leave the target branch unchanged.
- Concurrent incident proposals serialize or rebase without losing accepted changes.
- A successful proposal creates one attributable commit and returns its SHA.
- A failed post-validation deployment can roll back the deployed commit without deleting the audit record.

Exit criteria:

- It is impossible for an agent session to persist invalid operational memory through the supported API.
- Every memory commit names its originating incident and validation result.

### Phase 5: Unify deployment, health objectives, and health detectors

Goals:

- Extend the operator CLI to accept a health-objective file or text and save it as `.sdo/goal.md`.
- Keep deployer and health-judge contexts independent across handoffs.
- Replace deployment-only `health_check.sh` as the canonical success signal with judge-authored health detectors; keep scripts as helpers where useful.
- Persist `.sdo/arch.md` early enough to carry deployment continuity across fresh sessions.
- Have the deployer commit each attempt through the commit broker.
- Leave successful applications running and hand control to the controller instead of unconditionally stopping after a fixed monitoring count.

Tests first:

- The deployer cannot edit the health objective.
- The judge catches a partial deployment that the deployer declares successful.
- Fresh deployer sessions resume from `arch.md` rather than prior conversation state.
- Deployment completion requires all health detectors to pass consecutively.
- The CLI transitions from deployment to continuous controller ownership without stopping the application.

Exit criteria:

- One top-level command performs objective capture, deployment, judge validation, controller rollout, and ongoing operation.
- Both Pydantic AI and coding-agent backends implement the same role contracts.

### Phase 6: Complete reflection and outcome-driven memory refinement

Goals:

- Preserve the responder session through post-fix reflection.
- Append the controller-derived outcome before reflection begins.
- Give reflection the current incident record plus relevant historical outcomes.
- Require playbooks to use role placeholders and architecture binding rather than incident-specific resource names.
- Allow one detector to surface a small set of candidate playbooks and multiple detectors to coexist for compound faults.
- Implement refinement operations: tighten, widen, split, merge, remap, add, and mark-for-review.
- Make reflection optional on failed/unverified incidents; never learn a repair as successful without evidence.

Tests first:

- Helpful, rejected, and missed playbooks produce correct FP/FN outcome signals.
- Repeated false positives trigger a refinement proposal without deleting the historical record.
- A confirmed cause reached from scratch creates a playbook and detector proposal.
- A detector that repeatedly maps to divergent repairs is proposed for splitting.
- Playbook lint rejects hard-coded incident IDs, namespaces, and resource names unless explicitly justified as intrinsic to the fault class.

Exit criteria:

- A recurring fault is detected, localized, and routed to its learned playbook.
- A novel fault falls back to open-ended investigation and adds validated memory after recovery.
- FP/FN behavior improves across a deterministic incident sequence.

### Phase 7: Implement architecture upkeep and telemetry extensions

Goals:

- Compare `arch.md`'s source commit with the current application commit.
- Classify diffs as topology-relevant or topology-neutral.
- Regenerate only affected architecture sections.
- Detect renamed or removed entities referenced by memory and mark the affected playbook or detector for review.
- Add fixed, read-only telemetry adapters for Prometheus/OpenTelemetry-backed signals.
- Let agents author declarative telemetry queries or adapters only through a validated interface.

Tests first:

- Adding/removing a service, changing a dependency edge, or renaming a configuration key updates the expected architecture section.
- A documentation-only change does not trigger architecture regeneration.
- References to removed entities mark the affected artifacts for review.
- Telemetry detector fixtures are deterministic and cannot mutate external systems.

Exit criteria:

- Memory remains aligned after representative application evolution commits.
- The operator can detect at least one application-level latency/error-rate objective without an LLM in steady state.

### Phase 8: Migrate artifacts and harden evaluation/release workflows

Goals:

- Migrate current playbooks to `.sdo/` and repair all dead index links.
- Generalize application-specific playbooks using role placeholders and architecture bindings.
- Add detector packages and captured snapshot fixtures for the committed playbook corpus.
- Publish exact experiment configurations for Memoryless, History-Embed, History-LLM, and SDO.
- Pin model, prompt, application, SREGym, and fault-stream versions.
- Record controller, agent, memory, and deployment commit SHAs in every result.

Tests first:

- Every index entry resolves to a playbook.
- Every detector-declared playbook exists.
- Every committed detector has unit fixtures and compiles in a clean checkout.
- Experiment smoke tests produce complete provenance manifests.
- Frozen-memory overlap/no-overlap tests enforce read-only memory.

Exit criteria:

- A clean checkout can reproduce representative deployment, recurrence, compound-fault, no-overlap, and retrieval experiments.
- The implementation-to-paper feature matrix contains no unimplemented claims.

## Proposed module boundaries

The exact names may change, but responsibilities should be separated as follows:

```text
observer/
  sdk/                         # Public detector API only
  core/                        # Snapshot, validation, runner primitives
  controller/                  # Informers, scheduling, persistence, dispatch

app_operator/
  lifecycle/                   # Deploy -> handoff -> continuous operation
  roles/                       # Deployer, judge, responder interfaces
  protocol/                    # IncidentRequest/Result models
  memory/
    models.py                  # Goal/arch/outcome/manifest models
    repository.py              # Read-only artifact access
    commit_broker.py           # Transactional validation and Git commits
    upkeep.py                  # Outcome and architecture refinement
  responder/                   # Backend-neutral incident workflow

sregym_agents/
  cli_agent/                   # Coding-agent adapter, not lifecycle owner
  baselines/                   # Lesson/history/RAG/LLM experimental systems
```

Move SDO lifecycle ownership out of the SREGym-specific driver. SREGym should invoke the same production interfaces through an adapter rather than define production semantics in benchmark prompts.

## End-to-end test matrix

Before declaring parity, automate at least these scenarios:

| Scenario | Required behavior |
|---|---|
| Healthy steady state | Detectors run continuously; no responder and no LLM calls |
| Transient fault | Finding clears before threshold; no dispatch |
| First occurrence | Health detector dispatches responder; scratch investigation repairs and writes validated memory |
| Exact recurrence | Fault detector surfaces the learned playbook and reduces investigation work |
| Parameter-shifted recurrence | Detector localizes different resources and binds the same generalized playbook |
| Novel fault with similar symptoms | Responder rejects the misleading playbook and falls back safely |
| Compound incident | Findings are batched; all causes are repaired before health clears |
| Detector false positive | Outcome is logged and later reflection tightens/splits the detector |
| Detector false negative | Human dispatch is logged; reflection adds or widens coverage |
| Application topology change | `arch.md` refreshes and stale memory enters review |
| Invalid reflection output | Commit broker rejects it; active memory remains valid |
| Controller restart | Active finding/session state recovers without duplicate dispatch |
| Concurrent incidents | Worktrees and commits do not overwrite each other |

## Operational and security requirements

- Run detector code with read-only Kubernetes RBAC and resource limits.
- Run responder mutations through a separate, auditable credential boundary.
- Never expose Kubernetes Secret values to generic detector snapshots.
- Sandbox detector compilation and playbook scripts.
- Apply command/path validation to all agent-authored shell execution.
- Sign or otherwise identify broker-created commits.
- Redact credentials and secret-bearing command output from trajectories and outcomes.
- Bound detector execution time, memory, output size, and finding count.
- Expose controller, detector, dispatcher, broker, and agent metrics independently.
- Preserve enough structured provenance to reconstruct why each responder was dispatched and why each memory edit was accepted.

## Recommended delivery order

The critical dependency chain is:

```text
Contracts/reproducibility
  -> complete detector SDK
  -> continuous controller
  -> responder dispatch
  -> transactional commit broker
  -> unified deployment handoff
  -> outcomes/reflection
  -> architecture upkeep/telemetry
  -> artifact migration and evaluation hardening
```

Do not begin by adding more playbooks or prompt instructions. Until the controller, outcome recorder, and commit broker exist, more agent-authored artifacts increase corpus size without establishing the paper's autonomy and safety guarantees.

## Suggested first implementation slice

The first vertically complete slice should be one known fault, such as a missing ConfigMap object:

1. Add ConfigMaps and reference helpers to the detector snapshot API.
2. Write a generic missing-ConfigMap detector and fixture first.
3. Run it continuously in the controller with a two-sample persistence threshold.
4. Dispatch one responder with the finding and one generalized playbook.
5. Require health-detector clearance before completion.
6. Append an outcome record.
7. Submit any reflection edit through the commit broker.
8. Reinject the fault on a differently named Deployment and ConfigMap and verify the learned path still applies.

This slice exercises the full control loop without waiting for the entire detector corpus or deployment system to be migrated.

## Current implementation handoff (2026-07-10)

The branch now contains the vertically complete production path described by the earlier phases. The remaining work
is evidence collection across the exact four-stage SREGym pipeline, not a deterministic substitute for that pipeline.

Completed and locally verified:

- [x] The canonical `.sdo/` goal, architecture, playbook, diagnostics, outcome, and lifecycle-provenance artifacts are
  created through the shared lifecycle implementation in [`app_operator/lifecycle/`](../app_operator/lifecycle/).
- [x] The production adapter uses fresh model-backed deployer sessions and a bounded three-round, independently
  validated health-judge loop; the deterministic bootstrap remains an explicit smoke-test double only.
- [x] The informer-backed Go controller persists state and broker effects, renews its Lease independently of long
  validation work, restores after termination, and suppresses duplicate responder dispatch.
- [x] Reflection stays in the responder's original model session, and proposal, outcome, and reflection commits are
  accepted only through deterministic schema, ownership, provenance, and sandbox validation.
- [x] Validator Jobs run without a service-account token, credentials, or a writable repository; live allow/deny
  canaries prove that the default-deny egress policy is enforced before untrusted detector code runs.
- [x] [`scripts/smoke_sdo_runtime_kind.sh`](../scripts/smoke_sdo_runtime_kind.sh) passes with two injected controller
  pod terminations, exactly one responder Job, detector clearance, independent verification, acknowledgement,
  cleanup, durable Job completion, and a successful post-reflection controller rollout.
- [x] Focused Python tests pass (85 passed, one opt-in live-Codex test deselected); `ruff`, `tach`, and Go race tests for
  `observer/sdk`, `observer/core`, and `observer/controller` pass.
- [x] The exact current controller, responder, and validator images are built and loaded on every `sregym-w0` node.

Live four-stage acceptance run:

- Pipeline: `bench/sregym/logs/20260710_104648_pipeline_sdo-codex-four-e2e/`
- Configuration: [`sregym_agents/experiments/sdo_codex_four_e2e.toml`](../sregym_agents/experiments/sdo_codex_four_e2e.toml)
- [x] Stage 0 real deployer handoff and all three fresh health-judge rounds completed; one round-one compile rejection
  was corrected in a new session, and `.sdo/lifecycle-provenance.yaml` records the unique session IDs.
- [ ] Stage 0 readiness-probe benchmark and strict post-cutover production receipt are in progress.
- [ ] Stage 1 missing-ConfigMap benchmark and strict production receipt.
- [ ] Stage 2 wrong-Service-selector benchmark and strict production receipt.
- [ ] Stage 3 NetworkPolicy-block benchmark and strict production receipt.

Every stage counts only if the benchmark completes and its strict receipt proves real lifecycle provenance,
Kubernetes Job dispatch, exactly one responder, empty clear fingerprints, proposal/outcome/same-session-reflection
commits, passing independent verification, acknowledgement, cleanup, no remaining worktrees, and a successful
controller rollout when diagnostics changed. Earlier failed or interrupted stage attempts are excluded.

## Completion checklist

- [ ] Clean-checkout build, lint, and tests pass.
- [ ] Canonical memory root and schema are versioned.
- [ ] Detector SDK covers the paper's structured-state examples.
- [ ] Go controller runs continuously with informer-backed scheduling.
- [ ] Persistent findings automatically dispatch one responder.
- [ ] Compound findings batch into one incident session.
- [ ] Health objectives and health detectors are independently owned.
- [ ] Responder success requires external health clearance.
- [ ] Operational memory contains all five paper artifacts.
- [ ] Outcome records drive tested detector refinement.
- [ ] Architecture summaries are commit-tagged and refreshed from diffs.
- [ ] Commit broker prevents invalid or unauthorized memory changes.
- [ ] Accepted memory changes are auditable Git commits.
- [ ] Steady-state LLM usage is zero.
- [ ] Current playbooks are generalized and all index links resolve.
- [ ] Production and SREGym paths use the same lifecycle interfaces.
- [ ] Paper experiments have pinned, reproducible configurations and provenance.
