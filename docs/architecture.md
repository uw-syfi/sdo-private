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

`sdo operate REPOSITORY --namespace NAMESPACE (--goal TEXT | --goal-file PATH)` is the public production command. Optional flags select the application name, model, controller/responder/validator images, repository PVC, credentials Secret, deployment-attempt bound, and timeout. The lifecycle is Kubernetes-only. The health objective comes from a human; the deployer and health judge run as separate agent sessions, and the controller independently validates their artifacts.

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
| `benchmarks/sregym/` | First-party benchmark boundary: adapter, protocol, runner, experiments, analysis, and legacy participants |
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
| `benchmarks/sregym/participants/` | Benchmark competitors that are not production SDO components |

`benchmarks/sregym/participants/crucible/` is the legacy Crucible competitor. Its judge loop, benchmark-oracle recovery, and private knowledge-base formats are useful only for historical benchmark comparisons; they do not define the SDO responder or `.sdo/` operational memory. The external harness itself remains pinned separately under `third_party/sregym/`.

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

`schema-version` and `lifecycle-provenance.yaml` are validation metadata. They do not change the five ownership classes.

Incident responders never merge directly into the operational branch. The broker creates isolated worktrees, checks path ownership and append-only rules, runs repository and detector validation, and accepts only attributable commits. The source deployer separately authors attributable deployment commits during the initial lifecycle. Health detectors remain judge-owned; responders may change responder-owned playbooks and incident detectors after closure.

## Controller/responder interaction

The controller batches persistent findings into an incident request. The request includes the health objective path, architecture path, findings, surfaced playbooks, detector history, and an isolated repository worktree. A responder returns a structured result with diagnosis, applied playbooks, repair evidence, and commit information.

After independent health verification, the controller records the outcome. Selected classifications may resume the same responder session for reflection. Reflection proposals pass through the same ownership and validation boundary before controller rollout.

## Controller-installation boundary

`sdo/controller_install/` installs the production controller in Kubernetes and exposes a narrow extension protocol for transports. The installer owns controller resources and repository synchronization. The SREGym extension adds benchmark submission resources, readiness, receipts, and cleanup without changing controller semantics.

The installation code provides manifests and orchestration logic; the always-running runtime is `controller/runtime/` in Go. Unit and Go integration tests validate contracts; successful operation on a particular cluster still depends on cluster access, images, credentials, storage, and the target application's deployment artifacts.

## Shared libraries

- `libs/agent_cli/` provides the production coding-agent adapter; SDO's deployer, health judge, responder, and reflection backends use its structured Codex execution while retaining subsystem-owned protocols and Pydantic validation.
- `libs/model_config/`, `libs/agent_mw/`, and `libs/pydantic_agent/` support legacy benchmark participants where configured.
- `libs/sdo_core/` contains neutral command, filesystem, and tool helpers.
- SREGym-specific protocols and runner utilities live under `benchmarks/sregym/`, not `libs/`.

## Supported and excluded scope

Supported production scope is the lifecycle, memory, controller, responder, and Kubernetes runtime described above. SREGym code is supported as benchmark infrastructure.

Excluded from production scope are bounded shell-monitor loops, generated shell health checks, a second deployment lifecycle, standalone Compose fault injection, historical trajectory recorders, and benchmark verdict logic inside production packages.

See [sdo-paper-parity-implementation-plan.md](sdo-paper-parity-implementation-plan.md) for the code-to-paper matrix and [testing-guide.md](testing-guide.md) for validation commands.
