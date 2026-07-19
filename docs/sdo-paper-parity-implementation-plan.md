# SDO code-to-paper scope matrix

This matrix replaces the historical implementation plan. It records which repository modules correspond to the paper design and distinguishes implementation evidence from claims that require a live cluster or experimental result.

Status meanings:

- **Implemented** — code and focused automated tests exist.
- **Integrated** — component boundaries are wired in repository code; environment-specific execution still requires external infrastructure.
- **Benchmark-only** — retained for SREGym evaluation and excluded from production imports.

## Feature matrix

| Paper design element | Repository implementation | Status | Evidence and limit |
|---|---|---|---|
| Single SDO lifecycle | `sdo operate`; `sdo/operation.py`; `sdo/agent_runtime/lifecycle/`; `sdo/controller_install/` | Implemented | CLI and orchestration have focused tests; successful deployment still depends on a Git worktree, Kubernetes, images, credentials, and application source |
| Source-to-running deployment | `sdo/agent_runtime/lifecycle/deployment.py` | Implemented | Bounded agent attempts, attributable commits, and an independent verifier have unit coverage; no universal application-success claim |
| Backend-neutral agent boundary | Lifecycle, deployment, and responder `Protocol` interfaces; `libs/agent_cli` structured Codex adapter | Integrated | Production currently ships the agent-cli Codex backend; the Pydantic AI implementation is retained only for SREGym, so the paper's second production backend is not integrated |
| Standardized MCP agent outcomes | Structured Pydantic request/result schemas and guarded CLI checks | Integrated | Commit and controller boundaries are structured, but deployment/lifecycle sessions currently use Codex output schemas rather than the paper-described MCP outcome servers |
| Fresh deployer and health judge | `sdo/agent_runtime/lifecycle/agents.py`, `operational_memory.py` | Implemented | Structured handoffs, distinct session identifiers, bounded corrections, and deterministic validation are tested |
| Human health objective | `.sdo/goal.md`; lifecycle command input | Implemented | Objective is persisted with human ownership and digested by the judge; benchmark verdicts are forbidden inputs |
| Five-artifact operational memory | `sdo/operational_memory/`; `.sdo/{goal.md,arch.md,playbooks,diagnostics,outcomes.jsonl}` | Implemented | Typed models, repository loading, ownership validation, append-only outcomes, and fixtures have tests |
| Source-grounded architecture | `sdo/agent_runtime/lifecycle/operational_memory.py` | Implemented | Source commit, topology fingerprint, resource inventory, and coverage validation are deterministic; prose quality remains model-dependent |
| Go detector SDK | `controller/sdk/` | Implemented | Detector, snapshot, finding, persistence, batching, and test-snapshot contracts have Go tests |
| Generated detector validation | `controller/builder/`, `sdo/operational_memory/sandbox.py` | Implemented | Manifest/path validation, isolated compile/test workflows, and generated workspace behavior have focused tests |
| Long-running controller | `controller/core/`, `controller/runtime/` | Integrated | Scheduling, cache snapshots, finding state, batching, dispatch, state, and leader-election behavior have Go tests; live durability depends on cluster resources |
| Isolated responder | `sdo/contracts/`, `sdo/agent_runtime/responder/`, controller responder jobs | Implemented | Typed request/result contracts, worktree isolation, credential handling, and session backends have tests |
| Transactional commit broker | `sdo/operational_memory/commit_broker.py`, `broker_service.py`, `validation.py`, `worktrees.py` | Implemented | Role/path ownership, proposal validation, merge sequencing, outcomes, and idempotency are covered by focused tests |
| Outcome-driven reflection | `sdo/agent_runtime/responder/reflection.py`, `sdo/operational_memory/broker_service.py` | Implemented | Selected outcome classifications resume the responder session and validate memory proposals; learning effectiveness is an experimental question |
| Controller refresh after detector change | `controller/builder/check_cli.py`, runtime rollout records | Integrated | Fingerprint and correlated rollout contracts have tests; production rollout requires a cluster and images |
| SREGym evaluation boundary | `benchmarks/sregym/`, `third_party/sregym/` | Benchmark-only | First-party adapter, protocol, runner, experiment, analysis, and agent code is separated from the external harness and intentionally outside production modules |

## Artifact ownership matrix

| Artifact | May author | May update | Validation rule |
|---|---|---|---|
| `.sdo/goal.md` | Human | Human | Agents cannot change it |
| `.sdo/arch.md` | Deployer | Deployer/upkeep | Must match tracked source commit and topology fingerprint |
| `.sdo/playbooks/` | Bootstrap index, then responder | Responder | Paths, front matter, incident provenance, and history are validated |
| `.sdo/diagnostics/detectors/health/` | Health judge | Health judge lifecycle | Must preserve objective digest, ownership, compile, and tests |
| `.sdo/diagnostics/detectors/incidents/` | Responder | Responder | Requires incident ownership/provenance, compile, and matching plus near-miss tests |
| `.sdo/outcomes.jsonl` | Controller | Controller append only | Existing records are immutable |

## Repository scope matrix

| Path | Scope decision |
|---|---|
| `sdo/agent_runtime`, `sdo/operational_memory`, `sdo/contracts`, `sdo/controller_install` | Production SDO Python runtime |
| `controller/sdk`, `core`, `runtime`, `builder` | Production SDO |
| `libs/sdo_core` | Retained production-neutral support |
| `libs/agent_cli` | Production coding-agent CLI adapter, also reused by SREGym |
| `libs/model_config`, `libs/agent_mw`, `libs/pydantic_agent` | Retained agent support used by SREGym |
| `apps/` | Deployment/evaluation inputs |
| `benchmarks/sregym/adapter`, `protocol`, `runner`, `experiments`, `analysis` | Retained first-party benchmark integration |
| `benchmarks/sregym/agents/crucible` | Retained legacy benchmark agent; not production SDO agent logic or operational memory |
| `third_party/sregym` | Retained external SREGym harness Git submodule |
| Historical bounded operator, shell health checks, trajectory recorder, UI, prompt stack, standalone fault injection | Remove; outside the paper design |
| Legacy Python and controller package names | Removed after moving retained implementation to the canonical SDO paths |

## Required validation

Repository parity should be assessed through contracts rather than a checklist assertion:

1. unit tests for agent runtime, operational memory, contracts, controller installation, and builder boundaries;
2. Go tests for every controller module;
3. architecture checks that reject production-to-benchmark imports;
4. repository-scope checks that reject removed modules and stale naming;
5. Kubernetes smoke tests for images, RBAC, storage, controller rollout, and responder dispatch;
6. SREGym experiments reported separately from production correctness.

The matrix does not claim model quality, deployment success across all applications, incident-repair success, or experimental improvement. Those require recorded live runs and should be reported with their exact configuration and evidence.
