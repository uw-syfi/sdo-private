# SDO architecture

This document describes the production boundaries implemented in this repository and the separate SREGym benchmark boundary. It follows the architecture in `sdo_paper/`; it does not treat historical deployment experiments as production features.

## End-to-end lifecycle

```text
human health objective
        |
        v
source deployer -- attributable Git commit --> deployed Kubernetes app
        |
        v
fresh health-judge sessions -- deterministic validation --> .sdo memory
        |
        v
controller builder --> application-specific Go controller
        |
        v
snapshot scheduler --> detectors --> persistent findings --> incident batch
        |
        v
isolated responder worktree --> commit broker --> validated repair/memory commits
        |
        v
controller outcome --> same-session reflection --> refined playbooks/detectors
```

`sdo operate REPOSITORY --namespace NAMESPACE (--goal TEXT | --goal-file PATH)` is the public production operation command. Optional flags select the application name, model, controller/responder/validator images, repository PVC, credentials Secret, deployment-attempt bound, and timeout. The lifecycle is Kubernetes-only. The health objective comes from a human; the deployer and health judge run as separate agent sessions, and the controller independently validates their artifacts. Every lifecycle agent session runs in a neutral clone at `<tmp>/application`, never in the hosting workspace path, which a benchmark may name after the injected fault. The judge chooses its checks from the human objective and the application's topology: the controller-owned registration watches every kind the SDK snapshot exposes except Events, the bootstrap detector checks only the objective's own clauses (available Deployments, Services with ready endpoints), and validation never requires a check for a particular fault class (`docs/fairness-DECISIONS.md`). The judge edits its two owned Go files in a disposable checkout and may repeatedly invoke `sdo detector check`. A controller-authored context file lets that command report objective, topology, ownership, namespace, and oracle violations before compilation; passing drafts are then mounted read-only into the no-network validator container, which compiles only the health detector plus its registration contract and the ExternalName invariant: every health detector runs against the application's source Service names recast as ExternalName aliases, and a finding that appears only when such a Service lacks endpoints or pods fails validation. Its structured handoff contains provenance metadata rather than duplicated source text. SDO rejects edits outside the judge-owned files and independently reruns the full diagnostics validation after the session; an agent self-check is never acceptance evidence. When the deployment adapter can observe the active topology, lifecycle provenance records that selection and judge-authored coverage is restricted to matching deployed resources rather than every source variant.

## Python orchestration

`sdo/` is the canonical Python package and public product namespace. It implements the paper's agent runtime,
operational-memory boundary, message contracts, and installation of the separate Go controller.

| Package | Responsibility |
|---|---|
| `sdo/agent_runtime/lifecycle/` | Source deployment, source-backed topology inventory, independent health-judge rounds, and initial memory creation or reuse |
| `sdo/operational_memory/` | Typed memory contracts, isolated worktrees, validation, transactional commit brokering, and authoritative outcomes |
| `sdo/contracts/` | Typed findings, detector evaluations, and controller-to-responder incident contracts |
| `sdo/agent_runtime/responder/` | Incident response sessions, broker integration, credentials, and outcome-driven reflection |
| `sdo/controller_install/` | Kubernetes resources, repository synchronization, controller installation, and optional transport extensions |
| `benchmarks/sregym/` | First-party benchmark boundary: adapter, protocol, runner, experiments, analysis, and legacy agents |
| `third_party/sregym/` | External SREGym harness, retained as a Git submodule outside first-party package namespaces |

Production packages may not import `benchmarks.sregym` or code from the external harness. The dependency direction is from benchmark adapters to production APIs. Reusable lifecycle, incident-response, contracts, and operational-memory behavior belongs under `sdo/`, not under the benchmark namespace.

The first-party SREGym tree is divided by responsibility:

| Package | Responsibility |
|---|---|
| `benchmarks/sregym/adapter/` | Translate SREGym execution into production SDO lifecycle/controller/responder APIs; derive and persist submission relays (refusing a mitigation submission with exit 4 while `sdo incident status` is unhealthy) and strict receipts (including `diagnosis_verification`) |
| `benchmarks/sregym/protocol/` | Benchmark-only conductor, HTTP, MCP submission, and strict-receipt evidence contracts |
| `benchmarks/sregym/runner/` | Experiment and pipeline configuration, lifecycle chaining, and harness process orchestration |
| `benchmarks/sregym/experiments/` | Checked-in benchmark and end-to-end experiment definitions |
| `benchmarks/sregym/analysis/` | Benchmark-result summarization utilities |
| `benchmarks/sregym/agents/` | Benchmark competitors that are not production SDO components |

`benchmarks/sregym/agents/crucible/` is the legacy Crucible competitor. Its judge loop, benchmark-oracle recovery, and private knowledge-base formats are useful only for historical benchmark comparisons; they do not define the SDO responder or `.sdo/` operational memory. The external harness itself remains pinned separately under `third_party/sregym/`.

## Controller

The controller is split by stability and privilege:

| Package | Responsibility |
|---|---|
| `controller/sdk/` | Public Go interfaces for detector specifications, snapshots, findings, persistence, batching, and test snapshots |
| `controller/sdk/servicehealth/` | Zero-knowledge ready-endpoints detector that names the selector labels a Service's backing Deployment lacks |
| `controller/sdk/traffic/` | Synthetic-traffic scenarios, parameter generators, workload profiles, the deterministic engine, sliding-window SLO evaluation, and the scenario SLO detector |
| `controller/core/` | Detector execution, snapshot validation, finding emission, and playbook-path validation |
| `controller/runtime/` | Kubernetes cache, scheduler, finding state, batching, responder jobs, broker effects, leader election, durable state, and the prober pod and its observer |
| `controller/runtime/prober/` | The isolated synthetic-traffic prober process (no Kubernetes dependency): continuous health probes, verify bursts, HTTP API |
| `controller/builder/` | Validate `.sdo/diagnostics`, compile detector and traffic-generator tests in isolation, and generate the application-specific controller and prober workspaces |

Generated detectors are deterministic Go. They consume controller snapshots and must not call models, read hidden benchmark labels, or use external verdicts.

### Synthetic traffic

The health judge writes Go generators (`.sdo/diagnostics/traffic/generators/`, a package exporting `Scenarios() traffic.Catalog`) and workload profiles (`traffic/workloads/<name>.yaml`). A scenario is a user journey: steps whose endpoints build requests from a seeded rng and per-iteration state and check responses, the Services on its request path (from `.sdo/arch.md`, for localization), a side-effect class (read, idempotent write, or write with cleanup steps), a synthetic-data marker for writes, and the fault classes it must detect. A workload is data: purpose (`health-probe` runs continuously and feeds a traffic health detector; `verify-burst` and `journey` run on demand for a bounded time), scenarios and weights, arrival pattern and rate, timeouts, and per-scenario SLOs.

The `traffic.Engine` owns scheduling, arrival, rate, in-flight and time caps, latency measurement, and seeding: iteration *n* of a workload always builds the same requests, so any failing probe replays exactly. It refuses unsafe requests (writes from read scenarios, writes without the marker) without sending them. The builder compiles generators only into a separate prober binary (`cmd/prober`, with workloads embedded), never into the controller; generated tests prove that every scenario fails against simulated unreachable and 5xx targets plus its declared classes, that workloads name provided scenarios, and that traffic detectors consume health-probe workloads, and a static guard rejects generator imports that reach the network, filesystem, or cluster and package-level clock or rng calls.

The controller runs the prober as a pod in the control namespace: no service-account token, a read-only mount of only its content-addressed binary on the repository volume, CPU and memory limits, restart on crash, and a NetworkPolicy allowing egress only to the application namespace and cluster DNS. An unchanged binary keeps its pod across controller relaunches. The controller polls the prober's windows every 500 ms and wakes the traffic detectors only while a failure is inside a scenario's window; a scenario judges health only after it has succeeded once, and an unreachable prober surfaces as a detector error that blocks closure but never opens an incident. The always-on `service-endpoints` detector needs no authoring.

## Operational memory

The application repository is the shared durable memory. Five artifact classes live under `.sdo/`:

| Artifact | Owner | Contract |
|---|---|---|
| `goal.md` | Human | Application identity and exact health objective |
| `arch.md` | Deployer/upkeep | Source commit, topology fingerprint, and complete architecture summary |
| `playbooks/` | Responder | Fault-specific diagnosis, repair, and verification procedures |
| `diagnostics/` | Health judge and responder | Health and incident detector source, tests, module, and manifest; synthetic-traffic generators and workloads (judge-owned, except responder-owned `traffic/generators/incident/` and `traffic/workloads/incident-*.yaml`) |
| `outcomes.jsonl` | Controller | Append-only authoritative incident outcomes and evidence |

`schema-version` and `lifecycle-provenance.yaml` are validation metadata. They do not change the five ownership classes. Lifecycle provenance may attest an exact diagnostics-tree digest against an immutable validator image identity; only that exact pair can reuse a prior successful compilation, while any detector or validator change forces isolated validation again.

Incident responders never merge directly into the operational branch. The broker creates isolated worktrees, checks path ownership and append-only rules, runs repository and detector validation, and accepts only attributable commits. Two repair-evidence policies are supported. `commit` retains a proposal commit for every response, including an empty attribution commit. `recorded-actions` permits a live-only repair without a proposal commit when the responder returns a structured, successful action receipt; any repository changes are still validated and committed. In both modes the controller commits the authoritative outcome and reflection, and independently owned health detectors must verify recovery. The source deployer separately authors attributable deployment commits during the initial lifecycle. Health detectors remain judge-owned; responders may change responder-owned playbooks and incident detectors after closure.

## Controller/responder interaction

The controller batches persistent findings into an incident request. The request includes the health objective path, architecture path, findings, surfaced playbooks, detector history, an isolated repository worktree, and the selected repair-evidence policy. A responder returns a structured result with diagnosis, applied playbooks, repository changes, and per-action receipts recording the target, timing, success, and reversibility of live mutations. Independent health detectors must clear after the response before closure. If they do not clear within the configured verification timeout, the controller durably records a detector-review-required state and exits with an actionable error instead of leaving the incident open indefinitely.

The controller keeps a baseline of the application namespace's configuration, taken from namespace-scoped informers over Services, workloads, NetworkPolicies, ConfigMaps, Secrets, Roles and RoleBindings. The first healthy evaluation sets it. A later healthy snapshot replaces it only after a two-minute settle period with no unhealthy evaluation, and a maintenance pause resets it. When an incident opens, the request carries `state_changes`: the objects added, removed or modified since that baseline, with field-level before and after values. ConfigMap and Secret data appear only as digests. Kinds the controller cannot list are reported as unobserved. Objects that existed unchanged while the application was healthy never appear. The diff is computed in memory from the informer cache, so it adds no API calls on the dispatch path.

Responder Jobs receive the prober's Service address (`http://sdo-prober.<control-namespace>.svc:8080`) as `SDO_PROBER_URL` when they are dispatched, so a prober pod replaced mid-incident stays reachable. `python3 -m sdo incident status` runs one verify burst of the incident's scenarios through the prober. It exits 0 when healthy, 1 when unhealthy and 3 when the prober is unavailable, and only scenarios the steady probe has seen succeed can make it unhealthy. The controller also publishes an `incident_view` in its `sdo-controller-state` ConfigMap while an incident is open (the health-detector findings that still block closure and the current state diff) and passes its location as `SDO_CONTROLLER_STATE`; the status is then also unhealthy while a non-traffic health detector fires, and it lists state changes made after the request was taken. The gate has its own clear hysteresis, separate from closure's: while a blocking finding is clearing, the controller re-evaluates its detector every second for the gate only, and moves it to the view's `clearing_findings` after 3 consecutive clear evaluations spanning at least 2 s. Those re-evaluations never reach the finding tracker or detector history, so closure still waits for the health judge's own clear persistence. Responders must run it before declaring a repair. Each confirmed root cause cites structured live `evidence` (a detector finding, synthetic traffic, a state change or a live observation) and the `explained_detectors` it accounts for. Scripts and manifests go in `static_context`, which is never evidence. After closure, `verify_diagnosis` checks the citations against the request and the post-response detector states. It records a verdict per cause (`confirmed`, `contradicted`, `unverified`, `unattributed` or `no-evidence`) in the outcome record. A cause is `confirmed` only when the responder's own repair backs it: each repair action receipt names the objects it mutated (`resources`), and a successful action that started before the closure's `health_cleared_at` (when the gate detectors began their final clear streak) must have touched the cause's resources. Touching an object in the dispatch-time state diff is enough; otherwise the cause is also refused when a dispatch-diff change was reverted by something other than the responder's repairs. The closure also lists `observed_state_changes`, every object the controller saw differ from the baseline while the incident was open with its first observation, so a `state-change` citation outside the dispatch diff counts only if the controller saw it before the responder's own repair of that object started: a wrong fix cannot cite its own edit (a restart's `restartedAt` stays in the closing view), and a late fault the responder reverted exactly still verifies. When health clears in time but no cause is backed, the cause is `unattributed` and the outcome is `external_recovery`: someone else restored health. The verdict does not gate closure. Reflection learns playbooks and incident detectors only from confirmed causes, the broker never reflects on a `partial` or `external_recovery` outcome, and the controller does not surface contradicted or unattributed causes as prior-outcome evidence. Responders label any helper pods or Jobs they create `sdo.dev/responder-helper=true`. When the responder completes, the controller deletes them before evaluating the closure gate and records them in the closure as `cleaned_helpers`. If health clears only after the verification window, the closure carries `detector_review_required_at`; the broker does not credit that recovery to the responder, so the outcome is `partial` and reflection does not learn from it. A one-shot or bounded run exits when detector review is required; a persistent controller logs the review once and keeps observing, because its Job would otherwise restart it into a crash loop that ends detection. A split-namespace install grants this through a narrow pods/jobs delete Role.

Before dispatch, the controller deterministically selects up to three successful prior outcomes using exact finding fingerprints and, secondarily, detector/rule/resource-kind compatibility. Only compact root-cause, repair, playbook, and source-compatibility evidence enters the incident request. The responder treats that evidence as a hypothesis and must confirm, adapt, or reject it against live state; historical actions are never replayed automatically.

After independent health verification, the controller records the outcome. Selected classifications may resume the same responder session for reflection. An opt-in broker setting (`--reflection-session fresh`, `ControllerInstallConfig.reflection_session`) instead starts the first attempt in a fresh session given a bounded broker-built incident brief (closure findings with verbatim evidence, the responder's structured result, its recorded shell commands, and excerpts of the relevant memory); the ledger and receipt record `reflection_session_mode`, and `same_session_reflection` is then false. Before returning, reflection runs `python3 -m sdo.operational_memory.memory_check`, which applies the validator's memory rules (without detector tests) to its uncommitted `.sdo/` edits against the outcome commit. The broker supplies its own topology review (arch.md versus source fingerprints and the stale flag) and scopes the agent's self-check to the incident detector it changed. If validation rejects a proposal, the broker rolls it back and retries in a short fresh session given only the rejected diff, the validator error, and the original reflection request, so retries never re-send the responder transcript; the ledger counts these as `reflection_fresh_retry_attempts`. Reflection proposals pass through the same ownership and validation boundary before controller rollout. The reflection result explicitly declares either a validated update or a justified no-change decision. Claimed updates without worktree changes and exhausted invalid proposals remain failed rather than being recorded as successful learning. The responder and broker share one warm rule: a prior verified outcome matched by exact fingerprint and surfaced a playbook owned by (listed in `possiblePlaybooks` of) a registered responder-owned incident detector, through that detector's active finding, the request's surfaced playbooks, the prior outcome's applied playbooks, or the detector having been learned from that prior incident. The detector need not have fired; the warm responder prompt then replaces its missing evidence with one combined precondition check. A repeated exact-match success skips the LLM turn: when the warm rule held, the responder applied that playbook, every repair action and verification passed, the detector was not firing in its latest post-response evaluation or was not evaluated after the response (the controller's closure carries post-response `incident_detector_states` as evidence, not as a closure gate), and health was verified, the broker records a deterministic no-op reflection commit with `reflection_skipped_reason`. Reflection never rewrites an existing incident detector's `originatingIncident` or `originatingCommit`; the memory validator rejects such proposals. A new or changed incident detector registration must use `persistence.firing: 1` so the learned signature fires alongside the health detectors; untouched legacy registrations are not re-checked.

The validator image contains a Docker-layer-cached, trusted Go build-cache seed for the controller SDK and runtime dependencies. Each isolated validation copies that seed into its own ephemeral writable cache before compiling application-owned detectors. This preserves the read-only source, network isolation, and per-run scratch boundary while avoiding repeated cold dependency compilation.

## Controller-installation boundary

`sdo/controller_install/` installs the production controller in Kubernetes and exposes a narrow extension protocol for transports. The installer owns controller resources and repository synchronization. The SREGym extension adds benchmark submission resources, readiness, receipts, and cleanup without changing controller semantics. Its production-runtime adapter selects `recorded-actions` because benchmark incidents include legitimate live and node-level mitigations that need not alter application source; the strict receipt then requires either a proposal commit or a successful structured action receipt, plus the outcome/reflection commits and independent verification.

By default the controller shares the application namespace. `ControllerInstallConfig.controller_namespace` (and `sdo operate --controller-namespace`) instead places the controller Job, repository PVC, credentials, state ConfigMap, Lease, maintenance ConfigMap, and responder and validator Jobs in a separate per-application namespace labelled `sdo.dev/controller-namespace=true`. The application namespace then receives only two namespace-scoped grants bound to that namespace's ServiceAccounts: a read-only observer Role for the controller and the responder's repair Role; no ClusterRole is created. The repair Role is generic namespace-scoped edit on application resources (the shape of Kubernetes' `edit` role, without Secrets or RBAC objects, and with pods/exec, attach and port-forward for parity with the Codex baseline; see `docs/fairness-DECISIONS.md`, "Exec parity"), not a list of particular repairs. Responder pods point kubectl at the incident's application namespace. SDO never edits application charts or manifests. A long-running install runs `check_cli controller --supervise`: the Go controller exits after each acknowledged closure (`--restart-after-closure`), the supervisor compiles and records the rollout of any learned detector, and relaunches it in the same pod. The operator-owned `sdo-controller-maintenance` ConfigMap (`state: paused|active`, `generation`) pauses detector evaluation and application observation for planned redeploys while in-flight closure, reflection, and acknowledgement continue; resuming re-lists the application namespace with a fresh informer generation, logs `controller_maintenance` with the generation, and evaluates every detector once. With `reuse_existing`, re-running the installer keeps a running controller whose install fingerprint matches and only re-applies the namespace-scoped grants.

SREGym deletes and recreates the application namespace for every problem (and reconciles away unexpected namespaces and PersistentVolumes). The benchmark's opt-in persistent mode (`persistent_controller = true` under `agent_config.sdo_codex`) therefore installs one controller per application in `<application-namespace>-sdo`, asks the harness through `SREGYM_PRESERVE_NAMESPACE_LABEL` to keep that namespace and its volume, pauses the controller after each incident is verified, and on the next problem re-grants access, resumes with a new generation, and gates fault injection on the first all-clear evaluation after that resume. Per-problem resolution ends at controller-verified health; reflection continues asynchronously. Before the next injection the adapter waits for the previous incident's acknowledgement and supervised relaunch (`reflection_drain`), syncs the controller's repository into the new workspace, and writes the previous problem's strict receipt; pipeline teardown drains the last incident and deletes the controller namespace.

Persistent benchmark experiments may additionally preserve shared cluster infrastructure behind an explicit SREGym-only flag. A durable in-cluster marker gates reuse, and the warm storage/observability installation becomes the reconciliation baseline; application and injected-fault state are still cleaned between attempts.

The installation code provides manifests and orchestration logic; the always-running runtime is `controller/runtime/` in Go. Unit and Go integration tests validate contracts; successful operation on a particular cluster still depends on cluster access, images, credentials, storage, and the target application's deployment artifacts.

## Shared libraries

- `libs/agent_cli/structured.py` runs every production agent turn through [agentshim](https://github.com/vic-lsh/agentshim). SDO's deployer, health judge, responder, and reflection backends name a provider (Codex or Claude Code) and an access level, and keep their subsystem-owned prompts and Pydantic validation. The access levels map onto each provider's own confinement: `read-only` is Codex's read-only sandbox, or Claude's settings sandbox denying workspace writes plus the Edit/Write tools; `workspace-write` is Codex's workspace-write sandbox (network off) with `sdo detector check` exempted by an exec-policy `allow` rule, or Claude's sandbox with `sdo detector check` excluded and a `PreToolUse` hook refusing direct `go`. Codex reads that rule only from `$CODEX_HOME/rules`, so each Codex workspace-write turn runs with a private, non-resumable `CODEX_HOME` under `$SDO_CODEX_HOME_ROOT` (default `~/.cache/sdo/codex-homes`, outside the workspace and `/tmp`) holding the rule and a 0600 copy of the user's `auth.json` (or none when `CODEX_API_KEY`/`OPENAI_API_KEY` is set), deleted when the turn ends; `danger-full-access` removes confinement for sessions that operate the cluster. Each turn also returns the shell commands the agent started, which the lifecycle audits for reads outside the application checkout; Codex omits commands its sandbox denied from that stream, which the audit tolerates because denied commands never ran. Responder and broker share only the selected provider's session directory so verified reflection can resume the exact incident session.
- The rest of `libs/agent_cli/` (the `CLICodingAgent` family) serves only the legacy Crucible benchmark agent.
- `libs/model_config/`, `libs/agent_mw/`, and `libs/pydantic_agent/` support legacy benchmark agents where configured.
- `libs/sdo_core/` contains neutral command, filesystem, and tool helpers.
- SREGym-specific protocols and runner utilities live under `benchmarks/sregym/`, not `libs/`.

## Supported and excluded scope

Supported production scope is the lifecycle, memory, controller, responder, and Kubernetes runtime described above. SREGym code is supported as benchmark infrastructure.

Excluded from production scope are bounded shell-monitor loops, generated shell health checks, a second deployment lifecycle, standalone Compose fault injection, historical trajectory recorders, and benchmark verdict logic inside production packages.

See [sdo-paper-parity-implementation-plan.md](sdo-paper-parity-implementation-plan.md) for the code-to-paper matrix and [testing-guide.md](testing-guide.md) for validation commands.
