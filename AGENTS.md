# SDS (Self-Defining Systems)

SDS autonomously explores, validates, and evolves infrastructure using agentic LLMs. It consists of an **Application Operator** and the **DeathStarBench** microservices benchmarks as target applications.

## Project Structure

```
sds/
├── app_operator/         # Core operator logic
│   ├── cli_agent/        # Default: CLI-based agent (deployer, monitor, code_analyzer)
│   │   └── backend/      # Provider backends (claude, gemini, codex, opencode)
│   ├── langgraph/        # LangGraph-based implementation
│   ├── adk/              # Google ADK implementation (Gemini only)
│   ├── commands/         # CLI commands (run, init_exp, viz_graph, analyze_prompts, optimize_prompts)
│   ├── dspy_integration/ # DSPy prompt optimization pipeline
│   ├── fault_injection/  # Docker Compose fault injection for training data
│   ├── prompts/          # Jinja2 prompt templates (deployer/, monitor/, code_analyzer/)
│   ├── config.py         # Configuration dataclasses
│   ├── exceptions.py     # Custom exception hierarchy
│   ├── filesystem.py     # Filesystem abstraction (RealFilesystem, InMemoryFilesystem)
│   └── trajectory.py     # Agent interaction recording
├── lego_agent/           # Autonomous script generation (Web UI + TUI + CLI)
│   ├── engine.py         # Clarification loop and orchestration
│   ├── runtime.py        # LangGraph agent + fan_out/summarize/judge_loop patterns
│   ├── server.py         # FastAPI WebSocket server for Web UI
│   ├── ui/               # Next.js Web UI
│   └── prompts/          # Jinja2 prompt templates
├── libs/agent_cli/       # Shared AI provider CLI integrations
├── apps/deathstarbench/  # Target applications (hotelReservation, socialNetwork, ...)
└── tests/                # unit/, integration/
```

## Application Operator

### Key Commands

```bash
uv run -m app_operator run /path/to/app        # deploy + monitor
uv run -m app_operator analyze-prompts         # baseline metrics
uv run -m app_operator optimize-prompts \
    --prompts deployer_fix_error --optimizer BootstrapFewShot
```

### sds.toml Configuration

```toml
[agent]
provider = "gemini"  # gemini | codex | claude | claude-code | opencode | openai | anthropic
model = "gemini-1.5-pro"

[runtime]
impl = "cli_agent"  # cli_agent | langgraph | adk

[operator]
interval = 30               # health check interval (1-86400)
monitoring_max_iters = 5
deployment_max_iters = 20
agent_fix_timeout = 1800
deploy_timeout = 900
agent_timeout = 900

[operator.phase]  # Phase control (optional)
code_analysis = false  # Disable code analysis phase (default: true)
fix_summary_consolidation = false  # default: true

# DSPy Prompt Optimization (optional)
[dspy]
use_optimized = false
optimized_version = "latest"
fallback_to_baseline = true

[fault_injection]
enabled = false
num_faults = 2
categories = ["misconfiguration", "correlated"]
seed = 42
```

Invalid config values raise `ValueError`/`TypeError` at initialization.

### Architecture Overview

- **DeploymentAgent** (`cli_agent/agents/deployer.py`): Generates deploy.sh/health_check.sh, self-healing loop.
- **CodeAnalyzerAgent** (`cli_agent/agents/code_analyzer.py`): Proactive codebase analysis, generates `.sds/code_analysis.md`.
- **AppMonitor** (`cli_agent/agents/app_monitor.py`): Periodic health checks.
- **Trajectory** (`trajectory.py`): Records all agent calls with sequential IDs, saves to `.sds/trajectories/*.json`.
- **PromptLoader** (`prompts/__init__.py`): Supports both Jinja2 (default) and DSPy-optimized rendering with fallback.

See [`docs/dspy-optimization.md`](docs/dspy-optimization.md) and [`docs/fault-injection.md`](docs/fault-injection.md) for details.

## LegoAgent

See [`docs/lego-agent.md`](docs/lego-agent.md) for full details.

```bash
./scripts/start_lego_ui.sh                      # Web UI (recommended)
uv run -m lego_agent                            # TUI mode
uv run -m lego_agent --no-tui --prompt "task"  # CLI mode
```

## Development Conventions

- **Python**: type hints, `autopep8` formatting, `ruff` linting, `pytest` tests
- **Config**: dataclasses with `__post_init__` validation (`TypeError`/`ValueError`)
- **Exceptions**: custom hierarchy in `app_operator/exceptions.py`
- **Microservices**: docker-compose, Go modules, Consul service discovery

## Usage Guide for LLM Agents

- **Debugging deployment**: Check `.sds/deploy.sh` and `.sds/logs/`
- **Debugging lego_agent**: Check `lego_agent_runs/<timestamp>/lego_agent.py`
- **Optimizing prompts**: `analyze-prompts` baseline → `optimize-prompts --dry-run` → compare
- **Adding DSPy signatures**: Add to `dspy_integration/signatures.py`, register in `SIGNATURES` dict
- **Extending metrics**: Modify `dspy_integration/metrics.py`, ensure weights sum to 1.0
- **Adding fault types**: Add to `COMPOSE_FAULTS`, implement `_inject_*`, register in dispatch table, add tests
- **Adding a new feature**: Think about tests first. Test public behavior, not internal details.
- **Fixing bugs**: Write a reproducing test first, then fix. Test must be part of the fix.

## Remote repo access

Use `glab` command (if available) to access the remote repo on GitLab, including issues and merge requests.

## Notes from Developers

- Use `uv` and `uv run ...` for Python.
- Keep this file concise — detailed docs live in `docs/`.
- When code changes impact CLI, update README.md.
- When removing code, delete it — do not comment it out.

## Code Validation (after every code edit)

```bash
bash scripts/format_code.sh   # autopep8 formatting
bash scripts/check_errors.sh  # ruff linting (add --fix to auto-fix)
```

## Testing

See [`docs/testing-guide.md`](docs/testing-guide.md) for full guidance.

```bash
scripts/run_tests.sh                      # all tests
uv run pytest tests/                      # python only
uv run pytest tests/ --cov=app_operator  # with coverage
```

**Key principles**: test contracts not internals; prefer test doubles over mocks; use `InMemoryFilesystem` for speed; one concept per test; cover happy path + errors + boundaries.

Available fixtures in `tests/conftest.py`: `test_filesystem`, `stub_agent`, `error_agent`, `timeout_agent`, `tracking_agent`, `configurable_agent`, `repo_with_scripts`.
