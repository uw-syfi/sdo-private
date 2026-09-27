# SDO (Self-Defining Operator)

SDO is the research prototype described in `sdo_paper/`: an agentic system that deploys an application from source, derives independently checked detectors from a human health objective, and then operates the application through a continuously running Kubernetes controller.

The production design has one entry point and one durable memory model. SREGym integrations remain in this repository as benchmark adapters; they are not part of the production runtime.

## System at a glance

```text
sdo operate
  -> source deployer
  -> independent health judge
  -> .sdo operational memory
  -> generated Go detectors
  -> Kubernetes controller
  -> isolated incident responder
  -> validated commits and outcome-driven reflection
```

The main packages are:

- `sdo/agent_runtime/lifecycle/`: source deployment, architecture capture, and health-detector bootstrap.
- `sdo/operational_memory/`: the transactional commit broker, validation, worktrees, and outcome records.
- `sdo/contracts/` and `sdo/agent_runtime/responder/`: structured controller/responder requests and fresh or resumed coding-agent sessions.
- `sdo/controller_install/`: installation of the production controller on Kubernetes.
- `controller/sdk/`: the public Go detector API.
- `controller/core/`: detector execution and snapshot validation.
- `controller/runtime/`: scheduling, finding persistence, batching, responder dispatch, and durable controller state.
- `controller/builder/`: validation and generation of an application-specific controller from `.sdo/diagnostics`.
- `benchmarks/sregym/`: first-party benchmark adapters, protocol clients, runners, experiments, analysis, and legacy agents.
- `third_party/sregym/`: the external SREGym harness Git submodule.

Production packages must not depend on benchmark packages. The reusable SDO lifecycle, responder, contracts, and operational-memory implementation lives under `sdo/`; Crucible is retained only as a legacy benchmark agent under `benchmarks/sregym/agents/`.

## Operational memory

Each operated application carries five durable artifact classes under `.sdo/`:

```text
.sdo/
├── goal.md                 # human-owned health objective
├── arch.md                 # deployer-owned source/topology summary
├── playbooks/              # responder-owned procedures
├── diagnostics/            # Go health and incident detectors + manifest
└── outcomes.jsonl          # controller-owned append-only outcomes
```

`schema-version` and lifecycle provenance support validation of those five artifact classes. Ownership rules are enforced by the commit broker: responders cannot rewrite the goal, health detectors, architecture, or prior outcomes.

## Quick start

```bash
git clone --recursive <repository-url>
cd <repository-directory>
uv sync
uv run sdo operate --help
```

Operate an application with an inline objective or a checked-in objective file:

```bash
uv run sdo operate /path/to/application \
  --namespace application \
  --goal-file /path/to/health-objective.md
```

The complete interface is:

```text
sdo operate REPOSITORY --namespace NAMESPACE (--goal TEXT | --goal-file PATH)
  [--application NAME] [--agent-provider {codex,claude}] [--model MODEL]
  [--controller-image IMAGE] [--responder-image IMAGE] [--validator-image IMAGE]
  [--repository-pvc PVC] [--credentials-secret SECRET]
  [--repair-policy {commit,recorded-actions}]
  [--attempts N] [--timeout-seconds N]
```

The application name defaults to the repository directory name. The model defaults to `SDO_MODEL`, or `gpt-5.4` when unset; deployment attempts default to 3 and the timeout to 1800 seconds. Default images are `sdo-controller:v0.1.0`, `sdo-responder:v0.1.0`, and `sdo-detector-validator:v0.1.0`. The default PVC is `sdo-application-repository` and the default credentials Secret is `sdo-codex-credentials`.

The production path requires a Git worktree, Kubernetes access, those images, and model credentials. The health objective is human-supplied rather than inferred from benchmark verdicts.

To exercise one complete incident in a disposable Kind cluster with Claude Haiku:

```bash
bash scripts/run_sdo_example_kind.sh
```

The example creates an application with a missing ConfigMap, runs the real Go controller and a real Haiku responder, verifies recovery independently, brokers the source repair, records the outcome, and resumes the same Haiku session for reflection. It uses deterministic prebuilt lifecycle artifacts so the example isolates the incident path. Set `SDO_SMOKE_REAL_LIFECYCLE=1` to also ask fresh Haiku sessions to generate the initial architecture and health detector.

For local validation without a live cluster:

```bash
bash scripts/format_code.sh
bash scripts/check_errors.sh
uv run pytest tests/unit/
```

See [docs/architecture.md](docs/architecture.md) for boundaries and lifecycle details, [docs/testing-guide.md](docs/testing-guide.md) for validation commands, and [docs/sdo-paper-parity-implementation-plan.md](docs/sdo-paper-parity-implementation-plan.md) for the concise code-to-paper scope matrix.

## Repository scope

The repository intentionally retains:

- the production SDO implementation;
- target applications used for source-deployment evaluation;
- SREGym benchmark code, adapters, experiment configuration, and analysis tools;
- tests, build definitions, and documentation required by those components.

Old bounded deploy-and-monitor operators, generated shell health checks, standalone fault-injection tooling, and historical trajectory formats are outside the paper design and are not part of the supported system.
