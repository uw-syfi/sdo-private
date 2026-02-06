# SDS (Self-Defining Systems)

**Context for LLM Agent**

SDS is an AI-native project designed to autonomously explore, validate, and evolve infrastructure by embedding agentic LLMs into the system lifecycle. It primarily consists of an **Application Operator** (an intelligent agent) and the **DeathStarBench** suite (microservices benchmarks) used as target applications.

## Project Structure

```
sds/
├── agentflow/            # Autonomous script generation module
│   ├── __main__.py       # Entry point for `python -m agentflow`
│   ├── cli.py            # CLI argument parsing and mode selection
│   ├── engine.py         # Core clarification loop and orchestration engine
│   ├── io.py             # I/O abstractions (ConsoleIO, TextualIO)
│   ├── models.py         # Data models (AgentflowResponse, AgentflowResult)
│   ├── runtime.py        # LangGraph agent runtime and orchestration patterns
│   ├── storage.py        # Script storage management
│   ├── tui.py            # Textual-based interactive TUI implementation
│   └── prompts/          # Jinja2 prompt templates
│       └── templates/agentflow/
│           ├── system.jinja2   # System prompt with orchestration docs
│           ├── user.jinja2     # User request template
│           └── repair.jinja2   # Error correction template
├── app_operator/         # Core operator logic
│   ├── cli_agent/        # CLI-based agent implementation
│   │   ├── agents/       # Specialized agents (deployer, monitor, code_analyzer)
│   │   └── backend/      # Coding agent CLI backends (claude, gemini, codex, opencode)
│   ├── langgraph/        # LangGraph-based implementation
│   ├── adk/              # Google ADK-based implementation
│   ├── commands/         # CLI commands (run, init_exp, viz_graph)
│   ├── prompts/          # Jinja2 prompt templates
│   ├── config.py         # Configuration dataclasses
│   ├── exceptions.py     # Custom exception hierarchy
│   ├── filesystem.py     # Filesystem abstraction layer
│   ├── logger.py         # Logging configuration
│   └── trajectory.py     # Agent interaction recording
├── apps/                 # Application code (DeathStarBench suite)
│   └── deathstarbench/
│       ├── hotelReservation/ # Go-based microservices app
│       ├── socialNetwork/    # C++/Python/Go microservices app
│       └── ...
└── README.md             # Root project documentation
```

## 1. Application Operator (`app_operator/`)

The **Application Operator** is a Python tool that autonomously deploys, monitors, and manages applications. It uses AI agents (Codex, Gemini, or Claude) to self-correct deployment scripts and summarize health checks.

### Setup & Usage

*   **Dependency Management:** Uses `uv` (or `pip`).
*   **Installation:**
    ```bash
    uv sync  # or pip install -e .
    ```
*   **Environment:** Requires `.env` in root with `OPENAI_API_KEY`.

### Key Commands

*   **Default Mode (AI-assisted Autonomous Deployment):**
    This mode attempts to deploy the application in the specified repository, using an AI agent to automatically fix deployment errors.
    ```bash
    # Uses Codex, Gemini, or Claude as configured in sds.toml
    ./sds_operator /path/to/repo
    ```

### Coding Agent Configuration

You can specify which AI provider to use for script generation and fixing by adding an `sds.toml` file to the target repository.

The operator supports three different runtime implementations for the same high-level autonomous deployment and monitoring logic:

*   **CLI Agent (`cli_agent`):** The default implementation. It interacts with the system by calling out to specialized coding agents (like Gemini, Claude, or Codex) through their CLI interfaces. Supports all providers.
*   **LangGraph (`langgraph`):** A modular and stateful implementation built using LangGraph and LangChain. It models the deployment and monitoring process as a graph of specialized nodes and edges.
*   **ADK (`adk`):** Uses Google's Agent Development Kit with Gemini models for deterministic orchestration of agent tasks. Only supports Gemini/Vertex providers.

Example `sds.toml` configuration:

```toml
[agent]
provider = "gemini"  # Valid: "gemini", "codex", "claude", "claude-code", "opencode", "openai", "anthropic"
model = "gemini-1.5-pro" # required for langgraph and adk runtimes
# location = "us-central1" # optional: specify vertex AI location (default: us-central1)

[runtime]
impl = "cli_agent" # or "langgraph", "adk"

[operator]
interval = 30 # Health check interval in seconds (1-86400, default: 30)
monitoring_max_iters = 5 # Maximum health monitoring iterations (>0, default: 5)
deployment_max_iters = 20 # Maximum deployment attempts (>0, default: 20)
agent_fix_timeout = 1800 # Agent fix timeout in seconds (>0, default: 1800 / 30 minutes)
deploy_timeout = 900 # Deployment timeout in seconds (>0, default: 900 / 15 minutes)
agent_timeout = 300 # Agent generation timeout in seconds (>0, default: 300 / 5 minutes)

# DSPy Prompt Optimization (optional)
[dspy]
use_optimized = false           # Use DSPy-optimized prompts (default: false)
optimized_version = "latest"    # Version to use (e.g., "v1", "latest")
fallback_to_baseline = true     # Fall back to Jinja2 on errors (default: true)
enable_online_learning = false  # Enable feedback collection (default: false)
feedback_sample_rate = 0.1      # Fraction of runs to collect feedback (0.0-1.0, default: 0.1)
canary_deployment = false       # Enable canary rollout (default: false)
canary_percentage = 0.0         # Percentage for canary (0.0-1.0, default: 0.0)

[dspy.optimization]
optimizer = "BootstrapFewShot"  # DSPy optimizer: BootstrapFewShot, BootstrapFewShotWithRandomSearch, MIPROv2, COPRO
teacher_model = "claude-sonnet-4-5"  # Teacher model for optimization
num_examples = 30               # Number of training examples (positive integer)
validation_split = 0.2          # Validation data fraction (0.0-1.0)

[dspy.optimization.metric_weights]
success = 0.5        # Weight for deployment success (must sum to 1.0)
efficiency = 0.25    # Weight for iteration efficiency
tokens = 0.15        # Weight for token efficiency
health_check = 0.1   # Weight for health check script quality (prevents reward hacking)

[dspy.auto_rollback]
enabled = true                  # Enable automatic rollback on degradation
success_rate_threshold = 0.05   # Rollback if success rate drops by this fraction (0.0-1.0)
evaluation_window = 100         # Number of recent runs to evaluate (positive integer)
```

**Note**: Invalid configuration values will raise `ValueError` or `TypeError` with clear error messages at initialization.

### Architecture

The system is organized into two main runtime implementations, sharing core configuration and filesystem abstractions.

#### CLI Agent Architecture (`app_operator/cli_agent/`)
This implementation uses existing coding agent CLIs to manage the application lifecycle:

*   **DeploymentAgent (`app_operator/cli_agent/agents/deployer.py`):**
    *   Generates deployment scripts (`deploy.sh`, `health_check.sh`) if missing.
    *   Deploys the application and automatically fixes errors using an AI agent.
    *   Manages the self-healing deployment loop.
*   **CodeAnalyzerAgent (`app_operator/cli_agent/agents/code_analyzer.py`):**
    *   Analyzes the codebase before deployment.
    *   Generates `.sds/code_analysis.md` and `.sds/deployment_issues.md`.
    *   Identifies potential deployment issues proactively.
*   **AppMonitor (`app_operator/cli_agent/agents/app_monitor.py`):**
    *   Monitors application health at regular intervals.
    *   Uses `HealthCheckTask` to execute checks and `CodingAgent` to analyze results.
*   **Backend Providers (`libs.agent_cli/`):**
    *   Implements AI provider integrations (Claude, Gemini, Codex, Opencode).
    *   Each backend has event parsers for streaming output (`*_events.py`).
    *   Factory pattern (`factory.py`) for creating agent instances.

#### LangGraph Architecture (`app_operator/langgraph/`)
This implementation uses a stateful graph to manage the lifecycle:

*   **Graph Definition (`app_operator/langgraph/graph.py`):** Defines the nodes (deployment, monitoring, error fixing) and the transitions between them.
*   **State Management (`app_operator/langgraph/state.py`):** Maintains the state of the deployment and monitoring process throughout the graph execution.
*   **Tools (`app_operator/langgraph/tools.py`):** Provides the LLM with tools for executing shell commands, reading files, and performing health checks.
*   **LLM Integration (`app_operator/langgraph/llm.py`):** Configures LangChain LLM instances based on provider settings.
*   **Models (`app_operator/langgraph/models.py`):** Pydantic models for structured LLM outputs.
*   **Trajectory Handler (`app_operator/langgraph/trajectory_handler.py`):** Records agent interactions for the LangGraph runtime.

#### Shared Components
*   **Configuration (`app_operator/config.py`):**
    *   `AgentConfig`: AI provider configuration (codex, gemini, claude, claude-code, opencode)
    *   `OperatorConfig`: Operational parameters (intervals, max iterations)
    *   All configs validate on initialization with clear error messages
*   **Exceptions (`app_operator/exceptions.py`):**
    *   Custom exception hierarchy for clear error categorization.
*   **Filesystem (`app_operator/filesystem.py`):**
    *   Abstraction layer for filesystem operations (`RealFilesystem` vs `InMemoryFilesystem`).
*   **Trajectory (`app_operator/trajectory.py`):**
    *   Records agent interactions, prompts, responses, and tool calls.
    *   Saves structured trajectory.json files for analysis.
    *   **Sequential Call IDs:** Each agent call receives a unique sequential ID (1, 2, 3...) for tracking:
        *   Call IDs are stored in the `calls` array with start/end times and phase info
        *   Each conversation in phases (script_generation, deployment, monitoring) includes its `call_id`
        *   For Gemini CLI: metadata files (`sds_call_XXX.json`) are written to correlate sessions
        *   Gemini sessions are collected and matched to call IDs based on timing and metadata
        *   Session files are renamed to `gemini_session_call_XXX.json` for easy correlation
*   **Prompts (`app_operator/prompts/`):**
    *   Jinja2 template system for generating agent prompts.
    *   Templates organized by agent type: `deployer/`, `monitor/`, `code_analyzer/`.
    *   `PromptLoader` class for rendering templates with context.
*   **Commands (`app_operator/commands/`):**
    *   CLI command implementations: `run`, `init_exp`, `viz_graph`, `analyze_prompts`, `optimize_prompts`.
*   **DSPy Integration (`app_operator/dspy_integration/`):**
    *   **Configuration (`config.py`):** DSPy optimization settings, auto-rollback config, metric weights
    *   **Data Loader (`data_loader.py`):** Load training examples from trajectory files with phase filtering
    *   **Metrics (`metrics.py`):** DeploymentSuccessMetric, IterationEfficiencyMetric, TokenEfficiencyMetric, CompositeMetric
    *   **Metrics Aggregator (`metrics_aggregator.py`):** Compute statistics and compare optimization versions
    *   **Optimizer (`optimizer.py`):** Orchestrate DSPy optimization workflow with multiple optimizer support
    *   **Signatures (`signatures.py`):** DSPy signatures for all 10 SDS prompts (deployer, monitor, agentflow)
    *   **Cost Calculation (`cost.py`):** Token cost calculation for Claude, GPT, and Gemini models
    *   **Feedback (`feedback.py`):** Online learning feedback collection
    *   **Monitor (`monitor.py`):** Performance monitoring for auto-rollback

### DSPy Prompt Optimization

The Application Operator includes DSPy integration for offline prompt optimization. This allows you to automatically improve deployment success rates and efficiency by learning from historical trajectory data.

**Implementation Status**:
- ✅ **Phase 1-3**: Data pipeline, metrics, optimizer (COMPLETED)
- ✅ **Phase 4.1**: Field mappings, module loading, PromptLoader extension (COMPLETED)
- ✅ **Phase 4.2**: Runtime integration - agents use DSPy config (COMPLETED)
- ✅ **Phase 4.3**: Trajectory integration for version tracking (COMPLETED)
- ✅ **Phase 4.4**: Optimizer saves actual DSPy modules (COMPLETED)
- ⏳ **Phase 4.5-4.6**: Integration tests, documentation (PENDING)

#### Key Features

*   **Offline Optimization:** Use DSPy to optimize prompts based on past deployment trajectories
*   **Multiple Optimizers:** Support for BootstrapFewShot, BootstrapFewShotWithRandomSearch, MIPROv2, COPRO
*   **Comprehensive Metrics:** Track success rates, iteration efficiency, and token costs
*   **Version Management:** Auto-versioned optimized prompts with metadata
*   **Runtime Integration:** ✅ PromptLoader supports DSPy with automatic fallback to Jinja2
*   **Canary Deployment:** ✅ Deterministic hash-based routing (same repo → same version)
*   **Trajectory Tracking:** ✅ Records prompt version and fallback events
*   **Auto-Rollback:** Automatic rollback on performance degradation (pending Phase 4.4)
*   **Online Learning:** Optional feedback collection during production runs (pending Phase 4.4)

#### Workflow

1. **Run the operator** to generate trajectory data:
   ```bash
   uv run -m app_operator run /path/to/app
   ```

2. **Analyze current performance** to establish baseline:
   ```bash
   uv run -m app_operator analyze-prompts --phase deployment
   ```

3. **Optimize prompts** using DSPy:
   ```bash
   uv run -m app_operator optimize-prompts \
       --prompts deployer_fix_error deployer_summarize \
       --optimizer BootstrapFewShot
   ```

4. **Enable optimized prompts** in `sds.toml`:
   ```toml
   [dspy]
   use_optimized = true
   optimized_version = "latest"
   ```

5. **Test optimized version** and compare:
   ```bash
   uv run -m app_operator run /path/to/app
   uv run -m app_operator analyze-prompts \
       --compare baseline_dir:optimized_dir
   ```

#### Available Prompts

The DSPy integration provides signatures for 10 prompts across different agent types:

**Deployer Prompts (4):**
- `deployer_system` - System instructions for deployment agent
- `deployer_generate_script` - Generate deploy.sh and health_check.sh
- `deployer_fix_error` - Fix deployment errors (most critical for optimization)
- `deployer_summarize` - Summarize deployment results

**Code Analyzer Prompts (2):**
- `code_analyzer_system` - System instructions for code analysis
- `code_analyzer_user` - Analyze codebase for deployment

**Monitor Prompts (1):**
- `monitor_analyze_health` - Analyze application health

**Agentflow Prompts (3):**
- `agentflow_system` - System instructions for script generation
- `agentflow_user` - Generate Python script from user request
- `agentflow_repair` - Repair malformed JSON responses

#### Metrics

The optimization process uses a composite metric combining four factors:

1. **Deployment Success (50% weight):** Binary metric for successful deployment
2. **Iteration Efficiency (25% weight):** Rewards fewer iterations to success
3. **Token Efficiency (15% weight):** Rewards lower token usage
4. **Health Check Quality (10% weight):** Validates health check scripts are non-trivial to prevent reward hacking

The **Health Check Quality metric** prevents "reward hacking" by ensuring generated `health_check.sh` scripts actually perform meaningful checks rather than trivial always-passing scripts (e.g., `#!/bin/bash\nexit 0`). It evaluates:
- Script complexity (minimum 20 lines of actual code)
- Presence of real check commands (curl, nc, docker, redis-cli, mongo, etc.)
- Diversity of check types (ports, endpoints, containers, databases)

Metric weights are configurable in `sds.toml` under `[dspy.optimization.metric_weights]`.

#### Data Pipeline

```
Trajectory Files (.sds/trajectories/*.json)
    ↓
TrajectoryDataLoader → Extract examples by phase
    ↓
MetricsAggregator → Compute statistics
    ↓
PromptOptimizer → Run DSPy optimization
    ↓
Save versioned prompts (app_operator/prompts/optimized/vN/)
```

#### Runtime Architecture (Phase 4)

**PromptLoader Rendering Flow**:
```
agent calls render(template_name, **kwargs)
    ↓
Check: use_optimized? canary routing?
    ↓
├─→ YES: _render_dspy()
│     ├─→ Load optimized module (cached)
│     ├─→ Map kwargs → DSPy signature fields
│     ├─→ Invoke DSPy module
│     ├─→ Extract output field
│     ├─→ On Error: Fallback to Jinja2
│     └─→ Record version in trajectory
│
└─→ NO: _render_jinja2()
      └─→ Record version in trajectory
```

**Key Components**:
- **Field Mappings** (`field_mappings.py`): Maps Jinja2 kwargs to DSPy InputFields with auto + explicit mappings
- **Module Loader** (`loader.py`): Loads and caches DSPy modules with version resolution ("latest" → vN)
- **PromptLoader** (`prompts/__init__.py`): Extended to support both Jinja2 and DSPy rendering
- **Trajectory** (`trajectory.py`): Records `prompt_version` and `fallback_occurred` for each conversation

**Canary Deployment**:
- Deterministic routing: `hash(repo_path) % 100 / 100.0 < canary_percentage`
- Same repo always gets same version (predictable debugging)
- Configurable percentage in `sds.toml`

**Fallback Strategy**:
- Transparent fallback on any DSPy error (missing file, corrupted JSON, invocation error)
- Logged with context for debugging
- Recorded in trajectory for analysis

**Runtime Integration** (Phase 4.2):
- **CLI Agent Runtime**: All agents (DeploymentAgent, AppMonitor, CodeAnalyzerAgent) accept and use `dspy_config`
- **LangGraph Runtime**: Loader initialized with `config.dspy` in `build_graph()`
- **Configuration Flow**: `sds.toml` → `Config.dspy` → Agent constructors → `get_loader(dspy_config)`
- **Prompt Helpers**: All prompt functions (deployer, monitor, code_analyzer) pass `dspy_config` to loader
- **Defensive Programming**: Error handling for mock configs in tests (canary_percentage validation)

## 2. Agentflow Module (`agentflow/`)

The **Agentflow** module is an autonomous script generation system that uses AI agents to create orchestrated Python scripts for complex multi-agent workflows. It features an interactive TUI, clarification loops, and supports advanced orchestration patterns.

### Key Features

*   **Interactive TUI Mode**: Textual-based rich terminal interface with real-time streaming output.
*   **Clarification Loop**: Iteratively refines requirements through AI-powered questions before generating scripts.
*   **Orchestration Patterns**: Built-in support for `fan_out`, `summarize`, and `judge_loop` patterns.
*   **Automatic Repo Detection**: Finds project root by searching upward for `.git` or `sds.toml`.
*   **Script Validation**: Validates syntax and required components before execution.
*   **Environment Setup**: Configures `PYTHONPATH` and work directory automatically.

### Architecture

#### Core Components

*   **AgentflowEngine (`engine.py`)**:
    *   Manages the clarification loop (up to `max_clarifications` rounds).
    *   Integrates with LangGraph for agent execution.
    *   Streams thinking chunks, tool calls, and results.
    *   Validates generated Python scripts before execution.
    *   Handles response parsing and error repair.

*   **I/O Abstraction (`io.py`)**:
    *   **UserIO Protocol**: Duck-typed interface for user interaction.
    *   **ConsoleIO**: ANSI-colored console output for CLI mode.
    *   **TextualIO**: Rich Textual widgets for TUI mode with async support.
    *   Both implement: `read_prompt()`, `ask_questions()`, `render_thinking_chunk()`, `render_tool_start/end()`, etc.

*   **AgentflowTUI (`tui.py`)**:
    *   Textual App implementation with `RichLog` widget.
    *   Interactive input field for prompts and answers.
    *   Work directory display in header.
    *   Async script execution with live stdout/stderr streaming.
    *   Uses `flexoki` theme for consistent styling.

*   **LangGraphAgent (`runtime.py`)**:
    *   Wraps LangGraph React agent for orchestration.
    *   Implements streaming event handlers for thinking and tool use.
    *   Provides `generate()` sync and `_generate_async()` async methods.
    *   Supports parallelization through `fan_out()` for multi-task execution.

*   **Orchestration Patterns (`runtime.py`)**:
    1. **fan_out()**: Execute multiple independent prompts in parallel.
    2. **summarize()**: Aggregate multiple responses into one.
    3. **judge_loop()**: Iterative refinement with evaluation.
    4. **Combined patterns**: Complex workflows combining multiple patterns.

*   **Storage & Models**:
    *   **AgentflowStorage (`storage.py`)**: Manages script output with timestamped directories.
    *   **AgentflowResponse/Result (`models.py`)**: Pydantic models for structured data.

*   **Prompt System (`prompts/`)**:
    *   **system.jinja2**: Comprehensive system instructions (338 lines) with:
        - Orchestration pattern documentation
        - Available runtime API (create_agent, fan_out, summarize, judge_loop)
        - Tool descriptions (read_file, write_file, list_files, find_files, search_content, run_command)
        - Pattern examples with code blocks
        - Best practices section
    *   **user.jinja2**: User request with clarification history and validation checklist.
    *   **repair.jinja2**: Error correction prompt for JSON parsing failures.

### CLI Usage

**Default TUI Mode:**
```bash
uv run -m agentflow
```

**With Initial Prompt:**
```bash
uv run -m agentflow --prompt "Scrape hacker news and summarize top 3 AI stories"
```

**CLI Mode (No TUI):**
```bash
uv run -m agentflow --no-tui --prompt "Your task"
```

**Available Flags:**
- `--prompt`: Initial user prompt (interactive if omitted in TUI mode).
- `--loop-bound`: Maximum iterations for loops (default: 10).
- `--max-clarifications`: Maximum clarification rounds (default: 5).
- `--config`: Path to `sds.toml` (optional, auto-detects repo root).
- `--model`: Override agent model from configuration.
- `--output-dir`: Output directory (default: `agentflow_runs`).
- `--work-dir`: Execution directory (default: current directory).
- `--no-run`: Generate script but don't execute it.
- `--no-tui`: Use CLI mode instead of TUI.

### Data Flow

```
User Input (TUI or CLI)
    ↓
Config loading (sds.toml)
    ↓
AgentflowEngine.run_async()
    ├─→ Clarification Loop (rounds 0 to max_clarifications):
    │   ├─→ Render system/user prompts
    │   ├─→ Stream agent thinking (LangGraph events)
    │   ├─→ Stream tool executions
    │   └─→ Parse response (clarify/ready status)
    │
    ├─→ If status="clarify": ask_questions() → next round
    ├─→ If status="ready": validate_script() → write to storage
    │
AgentflowStorage.write_script()
    ↓
Script Execution (in work_dir)
    ├─→ Set PYTHONPATH to repo root
    ├─→ Stream stdout/stderr with visual formatting
    └─→ Return exit code
```

### Script Validation

Generated scripts must satisfy:
1. Define `MAX_ITERATIONS = {loop_bound}` constant.
2. Import from `agentflow.runtime` or related modules.
3. Include `if __name__ == "__main__":` block.
4. Be valid Python with proper syntax.

### Output Structure

Scripts are saved in `agentflow_runs/<timestamp>/agentflow.py` with:
- Timestamped directory for each run.
- Standalone execution (no external dependencies except `agentflow.runtime`).
- `MAX_ITERATIONS` constant for loop bounding.
- Orchestration tools: `fan_out()`, `summarize()`, `judge_loop()`.

## 3. Applications (`apps/deathstarbench/`)

**DeathStarBench** is a suite of cloud microservices benchmarks used as target applications for the SDS operator.

*   **Hotel Reservation:** A Go-based microservice application for booking hotels.
    *   **Tech Stack:** Go, gRPC, Consul, MongoDB, Memcached, Jaeger.
    *   **Ports:** Frontend at `http://localhost:5000`.
*   **Social Network:** A mixed-language social graph application.
*   **Media Microservices:** Service for streaming/processing media.

## Development Conventions

*   **Python (Agents):**
    *   Uses type hints.
    *   Uses `autopep8` for formatting.
    *   Uses `ruff` for static analysis.
    *   Tests via `pytest`.
    *   Dataclasses use `__post_init__` for validation (see `app_operator/config.py`).
    *   Custom exception hierarchy in `app_operator/exceptions.py` for clear error handling.
*   **Microservices:**
    *   Standard `docker-compose` patterns.
    *   Go modules for Go services.
    *   Service discovery via Consul is a common pattern.

## Usage Guide for LLM agents

*   **When debugging deployment:** Check `.sds/deploy.sh` and the generated logs in `.sds/logs/`.
*   **When debugging agentflow scripts:** Check `agentflow_runs/<timestamp>/agentflow.py` and examine the clarification history.
*   **When optimizing prompts:** Use `analyze-prompts` to establish baseline metrics, run `optimize-prompts` with `--dry-run` first to validate inputs, then compare results using `analyze-prompts --compare`.
*   **When adding DSPy signatures:** Add the signature class to `app_operator/dspy_integration/signatures.py` and register it in the `SIGNATURES` dict. Include comprehensive field descriptions and docstrings.
*   **When extending metrics:** Modify `app_operator/dspy_integration/metrics.py` and ensure weights in `CompositeMetric` sum to 1.0. Add corresponding tests.
*   **When adding a new app:** Simply run the operator on the repository. The `DeploymentAgent` will attempt to generate appropriate scripts automatically.
*   **When adding agentflow features:** Test both TUI and CLI modes. Verify the generated scripts are syntactically valid and include required components.
*   **When adding a new feature:** Think of what new behavior(s) are being introduced, and how you would test them. Test public behavior, not internal implementation details.
*   **When fixing bugs:** Think of how to write test(s) to reproduce the issue first and then use them to verify your fix. The test should be part of your fix. If you cannot do so, you must defend your decision.

## Notes from the developers

*   Keep this file up to date when you update the project.
*   Use `uv` and `uv run ...` for running python.
*   When code update impacts the CLI interface, update the README.
*   Remember to format the code after code edits using `@scripts/format_code.sh`.

### Documentation

*   Update README.md when your changes impact the user-facing behavior.

### Code hygiene

* When removing code, do not comment it out, just remove it.

### Code Validation

Always do the following after you're done with your code edits:

*   **Formatting:** Run `@scripts/format_code.sh` on python code edits.
*   **Linting:** Run `@scripts/check_errors.sh` on python code edits. Add `--fix` to automatically fix errors.

### Testing

#### Test Organization

*   **Unit tests** (`tests/unit/`): Fast, isolated tests for individual components
*   **Integration tests** (`tests/integration/`): Test component interactions and real behavior
*   Test organization by component:
    *   `tests/unit/config/`: Configuration validation tests
    *   `tests/unit/agents/`: Agent-specific tests (deployment, monitoring)
    *   `tests/unit/agentflow/`: Agentflow module tests (engine, runtime, CLI, prompts)
    *   `tests/unit/dspy_tests/`: DSPy integration tests (config, data loader, metrics, optimizer, signatures)
    *   `tests/integration/`: End-to-end scenarios, signal handling, concurrency

#### Running Tests

*   Run all tests: `uv run pytest tests/`
*   Run specific category: `uv run pytest tests/unit/` or `uv run pytest tests/integration/`
*   Run with coverage: `uv run pytest tests/ --cov=app_operator`
*   Check if tests pass after you've modified the codebase's behavior.
*   Don't run the sds_operator directly to test; it is a long-running process that will not terminate.

#### Writing Tests - Best Practices

**1. Test Behavior, Not Implementation**
```python
# ✅ GOOD: Test observable outcomes
def test_deployment_succeeds_after_retry(repo_with_scripts):
    operator = AppOperator(str(repo), agent=agent, max_deployment_attempts=3)
    exit_code = operator.run()
    assert exit_code == 0
    assert (repo / ".sds" / "logs" / "deploy_attempt_2.log").exists()

# ❌ AVOID: Test internal method calls (brittle)
def test_deployment():
    mock_deployer._fix_with_agent.assert_called_once()  # Fragile!
```

**2. Use Test Fixtures**

Available fixtures in `tests/conftest.py`:
*   `test_filesystem`: In-memory filesystem for fast, isolated tests
*   `stub_agent`: Minimal agent that returns simple responses
*   `error_agent`: Agent that always raises errors
*   `timeout_agent`: Agent that simulates timeouts
*   `tracking_agent`: Agent that tracks all calls for verification
*   `configurable_agent`: Agent with configurable responses
*   `repo_with_scripts`: Temp repository with pre-generated working scripts

**3. Test Agent Doubles**

Use test doubles from `tests/fixtures/agents.py` instead of mocking:
```python
from tests.fixtures.agents import StubAgent, TrackingAgent, ErrorAgent

def test_deployment_with_agent(tmp_path):
    agent = StubAgent()  # Simple, predictable behavior
    deployer = DeploymentAgent(tmp_path, agent)
    result = deployer.run(max_attempts=1)
    assert result is True

def test_agent_interaction(tmp_path):
    agent = TrackingAgent()  # Tracks calls for verification
    deployer = DeploymentAgent(tmp_path, agent)
    deployer.run(max_attempts=2)
    assert agent.generation_count == 1
```

**4. Filesystem Abstraction**

For testing filesystem operations without disk I/O:
```python
from app_operator.filesystem import InMemoryFilesystem

def test_with_memory_filesystem():
    fs = InMemoryFilesystem()
    fs.simulate_permission_error(path)  # Test error handling
    # ... test code that uses filesystem
```

**5. Configuration Validation**

All configuration dataclasses validate in `__post_init__`:
*   `AgentConfig`: Validates provider (must be in VALID_PROVIDERS), model type
*   `OperatorConfig`: Validates interval (1-86400s), max_iters (positive integers)
*   Invalid configs raise `TypeError` or `ValueError` with clear messages

When adding new config fields:
```python
@dataclass
class MyConfig:
    field: int = 10

    def __post_init__(self):
        if not isinstance(self.field, int):
            raise TypeError(f"field must be int, got {type(self.field).__name__}")
        if self.field <= 0:
            raise ValueError(f"field must be positive, got {self.field}")
```

**6. Exception Handling**

Use custom exceptions from `app_operator/exceptions.py`:
*   `ConfigurationError`: Configuration issues
*   `DeploymentError`: Deployment failures (includes exit_code, attempt)
*   `FileSystemError`: Filesystem operation failures
*   `ProcessError`: Process execution errors (includes timeout flag)
*   `AgentError`: Agent-related failures

Example:
```python
from app_operator.exceptions import DeploymentError

if result.exit_code != 0:
    raise DeploymentError(
        "Deployment failed",
        exit_code=result.exit_code,
        attempt=current_attempt
    )
```

**7. Environment Independence**

Tests must not assume external binaries (e.g., `npm`, `docker`, agents) are installed in the environment.
*   Mock `shutil.which` and `subprocess.run` to simulate binary presence/absence.
*   See `tests/unit/test_agent_factory.py` for examples of mocking agent binaries.

#### Test Coverage Guidelines

When introducing new features or modifying existing behavior:
*   Write tests for the **happy path** (expected behavior)
*   Write tests for **error conditions** (permission denied, timeouts, invalid input)
*   Write tests for **boundary values** (0, 1, max values)
*   Write tests for **edge cases** (special characters, long paths, concurrent operations)
*   Update tests on application semantic changes

#### What NOT to Test

*   Don't test private implementation details (methods starting with `_`)
*   Don't test third-party library behavior
*   Don't write tests that duplicate other tests
*   Don't test obvious getters/setters without logic
