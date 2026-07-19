---
name: review-codebase
description: Review the SDO codebase for correctness, security, paper-architecture alignment, production-versus-SRE-Gym boundaries, code quality, and convention adherence. Use for repository or subsystem audits and /review-codebase; use review-mr for a specific branch or merge-request diff.
---

# Review the SDO codebase

Produce evidence-backed, numbered issue proposals. Do not file issues or change code unless the user explicitly asks.

## Scope

Unless narrowed by the user, review:

- `app_operator/lifecycle`, `memory`, `protocol`, `responder`, and `runtime` as production orchestration;
- `controller/sdk`, `core`, `runtime`, and `builder` as the production controller;
- retained shared libraries under `libs/`;
- `app_operator/sdo_sregym`, `sregym_agents`, `libs/sregym_lib`, and `bench/sregym` as benchmark-only code;
- relevant tests, scripts, configuration, and documentation.

Treat `apps/` as evaluation input unless the user requests an application review. Check that production modules do not import benchmark code and that removed bounded-operator concepts have not reappeared.

## Process

1. Read [references/review-checklist.md](references/review-checklist.md).
2. Read applicable `AGENTS.md` files and the code-to-paper matrix.
3. Map public entry points, ownership boundaries, persistent state, and external effects before selecting findings.
4. Trace important claims through tests and call sites; distinguish implemented contracts from live-cluster assumptions.
5. Run `bash scripts/check_errors.sh` and focused tests when feasible.
6. Report only actionable findings with exact paths and line numbers.

## Output

```markdown
# Code review: <scope>

## Summary
<overall assessment and validation performed>

## Proposed issues

### #1: <title>
- Severity: critical | high | medium | low
- Labels: bug, security, architecture, testing, reliability, config, docs
- Locations: `path:line`
- Problem: <observable risk and evidence>
- Proposed fix: <concrete change>
- Acceptance criteria: <verifiable checks>

## Statistics
- Files reviewed: N
- Issues: N by severity
```

Keep each issue self-contained, logically narrow, actionable, and verifiable. Critical means a security, data-loss, or fundamental correctness risk; high means substantial reliability or architecture failure; medium means moderate risk or maintainability debt; low means a contained improvement.

## GitLab issues

File nothing until explicitly authorized. When authorized, use `glab issue create` with the proposed title, labels, and a body containing Problem, Proposed Fix, and Acceptance Criteria. Preserve issue numbering so the user can select findings unambiguously.
