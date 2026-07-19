# SDO architecture

This document describes the production boundaries implemented in this repository and the separate SRE Gym benchmark boundary. It follows the architecture in `sdo_paper/`; it does not treat historical deployment experiments as production features.

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

`app_operator/` is retained as the implementation package name, while its public product and command name are SDO.

| Package | Responsibility |
|---|---|
| `app_operator/lifecycle/` | Source deployment, source-backed topology inventory, independent health-judge rounds, and initial memory creation or reuse |
| `app_operator/memory/` | Typed memory contracts, isolated worktrees, validation, transactional commit brokering, and authoritative outcomes |
| `app_operator/protocol/` | Typed controller-to-responder and closure contracts |
| `app_operator/responder/` | Incident response sessions, broker integration, credentials, and outcome-driven reflection |
| `app_operator/runtime/` | Kubernetes resources, repository synchronization, controller installation, and optional transport extensions |
| `app_operator/sdo_sregym/` | Benchmark-only adapter, submission transport, and evaluation receipts |

Production packages may not import `app_operator.sdo_sregym`, `sregym_agents`, `libs.sregym_lib`, or benchmark code. The dependency direction is from benchmark adapters to production APIs.

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

## Runtime boundary

`app_operator/runtime/` installs the production controller in Kubernetes and exposes a narrow extension protocol for transports. The base runtime owns controller resources and repository synchronization. The SRE Gym extension adds benchmark submission resources, readiness, receipts, and cleanup without changing controller semantics.

The runtime code provides manifests and orchestration logic. Unit and Go integration tests validate contracts; successful operation on a particular cluster still depends on cluster access, images, credentials, storage, and the target application's deployment artifacts.

## Shared libraries

- `libs/agent_cli/`, `libs/model_config/`, `libs/agent_mw/`, and `libs/pydantic_agent/` support SRE Gym agents where configured. Production lifecycle and responder code currently use the Codex concrete backends behind their own protocols.
- `libs/sdo_core/` contains neutral command, filesystem, and tool helpers.
- `libs/sregym_lib/` is benchmark-only.

## Supported and excluded scope

Supported production scope is the lifecycle, memory, controller, responder, and Kubernetes runtime described above. SRE Gym code is supported as benchmark infrastructure.

Excluded from production scope are bounded shell-monitor loops, generated shell health checks, a second deployment lifecycle, standalone Compose fault injection, historical trajectory recorders, and benchmark verdict logic inside production packages.

See [sdo-paper-parity-implementation-plan.md](sdo-paper-parity-implementation-plan.md) for the code-to-paper matrix and [testing-guide.md](testing-guide.md) for validation commands.
