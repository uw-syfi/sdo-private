# Application Operator

Core operator that deploys and monitors target applications using agentic LLMs.

## Key Commands

```bash
uv run -m app_operator run /path/to/app        # deploy + monitor
uv run -m app_operator analyze-prompts         # baseline metrics
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error --optimizer BootstrapFewShot
```

## sds.toml Configuration

Full schema in `config.py`. Invalid values raise `ValueError`/`TypeError` at initialization.

```toml
[agent]
backend = "gemini"  # gemini | codex | claude | claude-code | opencode | openai | anthropic | hybrid | rlm-official | subagent
model = "gemini-1.5-pro"

[runtime]
impl = "cli_agent"  # cli_agent | pydantic_ai

[operator.phase]  # optional — disable phases
code_analysis = false          # default: true
fix_summary_consolidation = false  # default: true

[features]
git_integration = false        # default: false — see docs/feature-flags.md
```

See [`../docs/feature-flags.md`](../docs/feature-flags.md) for all feature flags.

## Architecture

- **DeploymentAgent** (`cli_agent/agents/deployer.py`): Generates deploy.sh/health_check.sh, self-healing loop.
- **CodeAnalyzerAgent** (`cli_agent/agents/code_analyzer.py`): Proactive codebase analysis, generates `.sds/code_analysis.md`.
- **AppMonitor** (`cli_agent/agents/app_monitor.py`): Periodic health checks.
- **Trajectory** (`trajectory/`): Records all agent calls with sequential IDs, saves to `.sds/trajectories/*.json`.
- **PromptLoader** (`prompts/__init__.py`): Supports both Jinja2 (default) and DSPy-optimized rendering with fallback.

See [`../docs/dspy-optimization.md`](../docs/dspy-optimization.md) and [`../docs/fault-injection.md`](../docs/fault-injection.md) for details.

## Usage Guide

- **Debugging deployment**: Check `.sds/deploy.sh` and `.sds/logs/`
- **Optimizing prompts**: `analyze-prompts` baseline → `optimize-prompts --dry-run` → compare
- **Adding DSPy signatures**: Add to `dspy_integration/signatures.py`, register in `SIGNATURES` dict
- **Extending metrics**: Modify `dspy_integration/metrics.py`, ensure weights sum to 1.0
- **Adding fault types**: Add to `COMPOSE_FAULTS`, implement `_inject_*`, register in dispatch table, add tests

## Notes

- When changing trajectory format (`trajectory/`) or experiment log/result structures (`commands/run_exp.py`), update the `analyze-experiment` skill references in `../.agents/skills/analyze-experiment/references/`.
- When adding or moving feature flags in `config.py`, update `../docs/feature-flags.md` to match.
