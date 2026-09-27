# Testing guide

SDO spans Python orchestration, Go controller modules, generated detector workspaces, and optional Kubernetes smoke tests. Validate the narrowest affected contract first, then expand in proportion to risk.

## Test organization

- `tests/unit/sdo/` — agent-runtime, operational-memory, contract, and controller-installation behavior.
- `tests/unit/benchmarks/sregym/adapter/` — benchmark-adapter transport and receipt behavior.
- `tests/unit/controller/` — Python controller-builder validation and workspace generation.
- `tests/unit/libs/` — shared-library contracts.
- `tests/integration/` — cross-package and external-process behavior.
- `controller/sdk/*_test.go` — detector SDK and test snapshots.
- `controller/core/*_test.go` — detector execution and validation.
- `controller/runtime/*_test.go` — scheduling, persistence, batching, dispatch, broker effects, and state.

## Core commands

```bash
# Format and static checks
bash scripts/format_code.sh
bash scripts/check_errors.sh

# Python
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/ --cov=sdo --cov=controller

# Go controller modules
(cd controller/sdk && go test ./...)
(cd controller/core && go test ./...)
(cd controller/runtime && go test ./...)
```

Use `scripts/run_tests.sh` for the repository-wide suite.

Agent turns are tested with agentshim's `FakeExecutor` through `tests/structured_turns.py`, which replays each provider's real stream format. Tests marked `live_agents` run the real Codex and Claude Code CLIs and are opt-in; cheap models keep them inexpensive:

```bash
SDO_RUN_LIVE_AGENTS=1 SDO_LIVE_CODEX_MODEL=gpt-6-luna SDO_LIVE_CLAUDE_MODEL=haiku \
  uv run pytest -m live_agents tests/
``` Live Codex or Kubernetes tests may require explicit markers, credentials, images, a cluster, and longer timeouts; do not infer production readiness from skipped external tests.

## Test the paper contracts

### Lifecycle separation

Use backend test doubles to prove that source deployment, deployer assessment, and each health-judge round receive only their declared inputs. Verify fresh session identifiers, bounded correction feedback, source commit attribution, and independent acceptance.

### Operational-memory ownership

Create temporary Git repositories with `.sdo/` fixtures. Test:

- human ownership of `goal.md`;
- deployer ownership and topology freshness of `arch.md`;
- responder-only updates under `playbooks/` and incident detectors;
- judge ownership of health detectors;
- append-only controller outcomes;
- path containment, symlink rejection, and commit attribution.

### Detector validation

Generated Go must compile and test in an isolated workspace. Include a matching case and a near-miss case for each incident signature. Validate manifest ownership, class, watches, persistence, batching, provenance, and playbook paths.

### Controller behavior

Prefer deterministic fake clocks, snapshot sources, dispatchers, and state stores. Cover firing and clearing thresholds, debounce windows, restart recovery, idempotent dispatch, exact incident correlation, leader loss, and broker-effect sequencing.

### Benchmark boundary

Tests for `benchmarks/sregym/adapter/` should prove that benchmark resources are added through the runtime extension interface and that strict receipts derive from durable controller/broker evidence. Add architecture tests that reject imports from SREGym packages in production modules.

## Test style

- Write a failing reproducer before fixing a bug.
- Test observable contracts and persisted evidence, not private fields or call order that is not part of the protocol.
- Prefer small explicit fakes over deep mock graphs.
- Use `tmp_path` and temporary Git repositories for repository mutations.
- Mock external subprocess boundaries in unit tests; do not require local `kubectl`, Docker, Go downloads, or model APIs unless the test is explicitly external.
- Assert failure paths: malformed structured responses, timeouts, stale commits, ownership violations, invalid detector code, interrupted rollouts, and duplicate acknowledgements.
- Keep one behavior per test and use names that state the invariant.

## Live validation

Kubernetes smoke scripts validate a different layer from unit tests: image contents, RBAC, storage, network policy, repository synchronization, generated controller startup, responder jobs, and cleanup. Record the exact cluster, images, model, application commit, and command whenever reporting a live result.

Run the reproducible Claude Haiku incident example with:

```bash
bash scripts/run_sdo_example_kind.sh
```

It requires Docker, Kind, `kubectl`, `uv`, and either `ANTHROPIC_API_KEY` or an authenticated Claude Code configuration. The default keeps lifecycle generation deterministic while using Haiku for the real response and same-session reflection. `SDO_SMOKE_REAL_LIFECYCLE=1` opts into model-generated initial memory and detectors as a less deterministic external integration check.

A green unit suite establishes repository contracts. It does not establish that every target application deploys successfully or that an agent repairs every incident.
