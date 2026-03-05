# Common Deployment Failure Patterns

## Pattern 1: Repeated Identical Failures (Agent Stuck)

**Signature:** Same error in consecutive attempt logs. Agent applies fixes but the error persists.

**Root causes:** Agent fixes symptom not cause, doesn't read error logs carefully, or misidentifies the failing component.

**What to look for in trajectory:**
- Same stderr/stdout error text across deployment entries
- Agent's assistant messages show it's not referencing the actual error output
- Fix attempts are superficial (e.g., changing a port number when the issue is a missing service)

## Pattern 2: Cascading Failures

**Signature:** Each fix reveals a new error in a different service.

**Root causes:** Incomplete code analysis, missing dependency graph understanding, no holistic view of the system.

**What to look for:**
- Different services fail on each attempt
- Agent fixes are reactive (fix what broke) rather than proactive (fix the architecture)
- `code_analysis.md` or `deployment_issues.md` is incomplete or missing key services

## Pattern 3: Platform Mismatch

**Signature:** `deploy.sh` mixes `docker compose` and `kubectl` commands.

**What to look for:**
- Tool calls showing edits to deploy.sh that add kubectl to a docker-compose deployment or vice versa
- "command not found" errors for the wrong platform

## Pattern 4: Startup Order Race Conditions

**Signature:** "connection refused" errors that appear intermittently. Service A can't reach Service B.

**What to look for:**
- Non-deterministic failures (same command works on retry)
- Missing `depends_on` with health conditions in docker-compose
- Agent doesn't add wait/retry logic

## Pattern 5: Missing Environment Variables

**Signature:** `KeyError`, `undefined variable`, or service crash on startup.

**What to look for:**
- Agent doesn't read application config files (application.properties, .env, config.json)
- docker-compose.yml missing `environment:` section
- Agent adds env vars one at a time across multiple attempts instead of comprehensively

## Pattern 6: Health Check Targeting Wrong Endpoints

**Signature:** Deployment succeeds but health check fails with 404 or connection refused.

**What to look for:**
- health_check.sh uses wrong port, wrong hostname, or wrong endpoint path
- Agent fixes health_check.sh multiple times (a detour if the app was already healthy)

## Pattern 7: Build Failures

**Signature:** `docker compose up` fails during image build. COPY errors, dependency install failures.

**What to look for:**
- Dockerfile references files not in build context
- Agent doesn't verify file paths before fixing Dockerfile
- Missing `--build` flag (uses cached broken image)

## Pattern 8: Agent Detours

**Signature:** Agent spends time on activities unrelated to the current error.

**Examples of detours:**
- Reading many files without acting on them
- Modifying application source code when the issue is in deployment config
- Repeatedly checking status without acting on results
- Undoing a previous fix and trying a different approach (thrashing)
- Exploring the codebase extensively when the error message already points to the fix

**What to look for:**
- Long sequences of Read/Grep tool calls with no subsequent Edit/Write
- Tool calls to files unrelated to the error
- Agent modifying source code (Go/Java/Python files) rather than deployment config
- Back-and-forth edits to the same file

## Healthy vs Unhealthy Summary

| Indicator | Healthy | Unhealthy |
|-----------|---------|-----------|
| Attempt count | 1-3 | 4+ or max reached |
| Error progression | Different error each attempt | Same error repeats |
| Fix strategy | Read error → identify cause → targeted fix | Guess → try → repeat |
| Platform commands | Consistent (all docker or all kubectl) | Mixed platforms |
| Code analysis quality | Comprehensive, all services listed | Incomplete, missing services |
| Agent tool usage | Read error logs → fix config | Skip logs → modify source code |
| Time per attempt | Decreasing (converging) | Increasing (diverging) |
