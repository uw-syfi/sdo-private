# SDO Python runtime

This package implements the paper-facing lifecycle around the Go controller.

## Public workflow

`sdo operate` is the production entry point:

```text
sdo operate REPOSITORY --namespace NAMESPACE (--goal TEXT | --goal-file PATH)
```

Optional flags select the application name, model, controller/responder/validator images, repository PVC, credentials Secret, bounded deployment attempts, and runtime timeout. Its orchestration:

1. accept a human health objective;
2. deploy the application source to Kubernetes with a fresh coding-agent session;
3. require an attributable source commit and independent deployment verification;
4. create or validate the five `.sdo/` operational-memory artifact classes;
5. build and install the application-specific controller; and
6. leave operation to the controller rather than a bounded shell-monitor loop.

## Package map

- `agent_runtime/lifecycle/` — source deployment, topology capture, independent health judging, and initial memory.
- `agent_runtime/responder/` — incident sessions, broker-facing CLI, credentials, and reflection.
- `operational_memory/` — typed memory models, worktrees, validation, commit broker, and outcomes.
- `contracts/` — structured findings, detector evaluations, and responder incident messages.
- `controller_install/` — production Kubernetes controller installation.

The durable memory root is `.sdo/`. Treat `goal.md`, `arch.md`, `playbooks/`, `diagnostics/`, and `outcomes.jsonl` as separately owned contracts. Provenance and schema files support validation but do not add another agent-owned memory class.

## Development rules

- Write reproducing tests first for bugs and contract tests first for features.
- Preserve fresh-session separation between the deployer and health judge.
- Do not let responders modify human-owned goals, deployer-owned architecture, judge-owned health detectors, or prior controller outcomes.
- Run generated detector code only through isolated validation and the deterministic Go controller path.
- Keep benchmark APIs, verdicts, submission relays, and receipts inside `../benchmarks/sregym/adapter/`.
- Keep existing async event loops intact; make handlers async and await work directly.
- Update `../docs/architecture.md` and the paper-scope matrix when lifecycle or ownership contracts change.

Validate Python edits with the root formatting/lint scripts and focused tests under `tests/unit/sdo/`.
