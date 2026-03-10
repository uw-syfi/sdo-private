# Feature Flags

Feature flags are split across two `sds.toml` sections:

- **`[operator.phase]`** — phase toggles that skip entire operator pipeline stages
- **`[features]`** — cross-cutting capability flags that apply across runtimes

```toml
[operator.phase]
code_analysis = true              # default: true
fix_summary_consolidation = true  # default: true
health_monitoring = true          # default: true

[features]
git_integration = false           # default: false
```

---

## Phase Toggles (`[operator.phase]`)

### `code_analysis`

**Default:** `true`

Runs `CodeAnalyzerAgent` before the deployment phase. The agent reads the target codebase and writes `.sds/code_analysis.md`, which is then injected into the deployer prompt to provide app-specific context.

Disable to skip the analysis step and reduce token usage:

```toml
[operator.phase]
code_analysis = false
```

---

### `fix_summary_consolidation`

**Default:** `true`

Enables the hypothesis-driven deployment progress document (`.sds/deployment_progress.md`). When enabled, the Error Fixer writes a hypothesis (root cause, fix plan, success criteria) before making any edits, and the Health Judge reads and validates those criteria after each attempt, recording confirmed/refuted outcomes. The accumulated history prevents re-trying approaches that have already been disproved.

Disable to skip the progress document entirely:

```toml
[operator.phase]
fix_summary_consolidation = false
```

---

### `health_monitoring`

**Default:** `true`

Runs periodic health checks after successful deployment. Disable to skip all post-deployment monitoring:

```toml
[operator.phase]
health_monitoring = false
```

---

## Capability Flags (`[features]`)

### `git_integration`

**Default:** `false`

Exposes the `make_change_on_remote_copy` tool to the agent. When enabled, the agent can push branches and open GitLab MRs from within the operator loop.

Requires GitLab credentials to be configured. **Opt-in only** — do not enable in local dev environments where pushing to a remote repository is undesirable.

```toml
[features]
git_integration = true
```
