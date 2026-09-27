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

`sdo operate REPOSITORY --namespace NAMESPACE (--goal TEXT | --goal-file PATH)` is the public production operation command. Optional flags select the application name, model, controller/responder/validator images, repository PVC, credentials Secret, deployment-attempt bound, and timeout. The lifecycle is Kubernetes-only. The health objective comes from a human; the deployer and health judge run as separate agent sessions, and the controller independently validates their artifacts. The judge edits its two owned Go files in a disposable checkout and may repeatedly invoke `sdo detector check`. A controller-authored context file lets that command report objective, topology, ownership, namespace, and oracle violations before compilation; passing drafts are then mounted read-only into the no-network validator container, which compiles only the health detector plus its registration contract. Its structured handoff contains provenance metadata rather than duplicated source text. SDO rejects edits outside the judge-owned files and independently reruns the full diagnostics validation after the session; an agent self-check is never acceptance evidence. When the deployment adapter can observe the active topology, lifecycle provenance records that selection and judge-authored coverage is restricted to matching deployed resources rather than every source variant.

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
| `benchmarks/sregym/adapter/` | Translate SREGym execution into production SDO lifecycle/controller/responder APIs; derive and persist submission relays and strict receipts |
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
| `controller/core/` | Detector execution, snapshot validation, finding emission, and playbook-path validation |
| `controller/runtime/` | Kubernetes cache, scheduler, finding state, batching, responder jobs, broker effects, leader election, and durable state |
| `controller/builder/` | Validate `.sdo/diagnostics`, compile detector tests in isolation, and generate the application-specific controller workspace |

Generated detectors are deterministic Go. They consume controller snapshots and must not call models, read hidden benchmark labels, or use external verdicts.

## Operational memory

The application repository is the shared durable memory. Five artifact classes live under `.sdo/`:

| Artifact | Owner | Contract |
|---|---|---|
| `goal.md` | Human | Application identity and exact health objective |
| `arch.md` | Deployer/upkeep | Source commit, topology fingerprint, and complete architecture summary |
| `playbooks/` | Responder | Fault-specific diagnosis, repair, and verification procedures |
| `diagnostics/` | Health judge and responder | Health and incident detector source, tests, module, and manifest |
| `outcomes.jsonl` | Controller | Append-only authoritative incident outcomes and evidence |

`schema-version` and `lifecycle-provenance.yaml` are validation metadata. They do not change the five ownership classes. Lifecycle provenance may attest an exact diagnostics-tree digest against an immutable validator image identity; only that exact pair can reuse a prior successful compilation, while any detector or validator change forces isolated validation again.

Incident responders never merge directly into the operational branch. The broker creates isolated worktrees, checks path ownership and append-only rules, runs repository and detector validation, and accepts only attributable commits. Two repair-evidence policies are supported. `commit` retains a proposal commit for every response, including an empty attribution commit. `recorded-actions` permits a live-only repair without a proposal commit when the responder returns a structured, successful action receipt; any repository changes are still validated and committed. In both modes the controller commits the authoritative outcome and reflection, and independently owned health detectors must verify recovery. The source deployer separately authors attributable deployment commits during the initial lifecycle. Health detectors remain judge-owned; responders may change responder-owned playbooks and incident detectors after closure.

## Controller/responder interaction

The controller batches persistent findings into an incident request. The request includes the health objective path, architecture path, findings, surfaced playbooks, detector history, an isolated repository worktree, and the selected repair-evidence policy. A responder returns a structured result with diagnosis, applied playbooks, repository changes, and per-action receipts recording the target, timing, success, and reversibility of live mutations. Independent health detectors must clear after the response before closure. If they do not clear within the configured verification timeout, the controller durably records a detector-review-required state and exits with an actionable error instead of leaving the incident open indefinitely.

Before dispatch, the controller deterministically selects up to three successful prior outcomes using exact finding fingerprints and, secondarily, detector/rule/resource-kind compatibility. Only compact root-cause, repair, playbook, and source-compatibility evidence enters the incident request. The responder treats that evidence as a hypothesis and must confirm, adapt, or reject it against live state; historical actions are never replayed automatically.

After independent health verification, the controller records the outcome. Selected classifications may resume the same responder session for reflection. Reflection proposals pass through the same ownership and validation boundary before controller rollout. The reflection result explicitly declares either a validated update or a justified no-change decision. Claimed updates without worktree changes and exhausted invalid proposals remain failed rather than being recorded as successful learning.

The validator image contains a Docker-layer-cached, trusted Go build-cache seed for the controller SDK and runtime dependencies. Each isolated validation copies that seed into its own ephemeral writable cache before compiling application-owned detectors. This preserves the read-only source, network isolation, and per-run scratch boundary while avoiding repeated cold dependency compilation.

## Controller-installation boundary

`sdo/controller_install/` installs the production controller in Kubernetes and exposes a narrow extension protocol for transports. The installer owns controller resources and repository synchronization. The SREGym extension adds benchmark submission resources, readiness, receipts, and cleanup without changing controller semantics. Its production-runtime adapter selects `recorded-actions` because benchmark incidents include legitimate live and node-level mitigations that need not alter application source; the strict receipt then requires either a proposal commit or a successful structured action receipt, plus the outcome/reflection commits and independent verification.

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
