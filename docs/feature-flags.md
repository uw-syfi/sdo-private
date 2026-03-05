# Feature Flags

Feature flags live under `[operator.phase]` in `sds.toml`. They control optional operator capabilities and default to a safe, minimal-footprint baseline — opt in explicitly when needed.

```toml
[operator.phase]
code_analysis = true              # default: true
fix_summary_consolidation = true  # default: true
git_integration = false           # default: false
```

---

## Flags

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

After the self-healing deployment loop, consolidates per-iteration fix summaries into a single structured report. Useful for auditing what changes the agent made across retries.

Disable if you only care about raw trajectories and want to skip the summary step:

```toml
[operator.phase]
fix_summary_consolidation = false
```

---

### `git_integration`

**Default:** `false`

Exposes the `make_change_on_remote_copy` tool to the agent. When enabled, the agent can push branches and open GitLab MRs from within the operator loop.

Requires GitLab credentials to be configured. **Opt-in only** — do not enable in local dev environments where pushing to a remote repository is undesirable.

```toml
[operator.phase]
git_integration = true
```
