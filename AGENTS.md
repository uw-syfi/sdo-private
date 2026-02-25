# SDS (Self-Defining Systems)

**Context for LLM Agent**

SDS is an AI-native project designed to autonomously explore, validate, and evolve infrastructure by embedding agentic LLMs into the system lifecycle. It primarily consists of an **Application Operator** (an intelligent agent) and the **DeathStarBench** suite (microservices benchmarks) used as target applications.

## Project Structure

```
sds/
├── lego_agent/            # Autonomous script generation module
│   ├── __main__.py       # Entry point for `python -m lego_agent`
│   ├── cli.py            # CLI argument parsing and mode selection
│   ├── engine.py         # Core clarification loop and orchestration engine
│   ├── io.py             # I/O abstractions (ConsoleIO, TextualIO)
│   ├── models.py         # Data models (LegoAgentResponse, LegoAgentResult)
│   ├── runtime.py        # LangGraph agent runtime and orchestration patterns
│   ├── storage.py        # Script storage management
│   ├── server.py         # FastAPI WebSocket server for Web UI
│   ├── tui.py            # Textual-based interactive TUI implementation
│   ├── ui/               # Next.js Web UI
│   │   ├── app/          # App Router components
│   │   └── ...
│   └── prompts/          # Jinja2 prompt templates
│       └── templates/lego_agent/
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
│   ├── fault_injection/  # SREGym-inspired fault injection for training data
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

[operator.phase]  # Phase control (optional)
code_analysis = false  # Disable code analysis phase (default: true)

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

[fault_injection]
enabled = true                    # Enable fault injection for training data (default: false)
num_faults = 2                    # Number of faults per run (1-5, default: 2)
categories = ["misconfiguration", "correlated"]  # Fault categories to include (default: all)
severities = ["low", "medium", "high"]           # Severity levels (default: all)
exclude_faults = []               # Fault IDs to exclude (default: [])
seed = 42                         # Random seed for reproducibility (default: None)
backup_compose = true             # Back up compose files before injection (default: true)
platform = "compose"              # Target platform: "compose" or "k8s" (default: "compose")
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
*   **Fault Injection (`app_operator/fault_injection/`):**
    *   **Models (`models.py`):** Fault, FaultResult, FaultCategory, FaultSeverity enums/dataclasses
    *   **Config (`config.py`):** FaultInjectionConfig with validation (`[fault_injection]` in sds.toml)
    *   **Base (`base.py`):** FaultInjector ABC and ComposeManipulator utility
    *   **Compose Faults (`compose_faults.py`):** 22 Docker Compose fault implementations + COMPOSE_FAULTS catalog
    *   **Registry (`registry.py`):** FaultRegistry for filtering and selecting faults
    *   **Injector (`injector.py`):** FaultInjectionOrchestrator for backup/inject/revert workflow
    *   **Reporter (`reporter.py`):** FaultReport for trajectory metadata bridge
    *   **CLI (`cli.py`):** Standalone CLI (`inject`/`revert`/`list`) for shell script integration

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

### Fault Injection for Training Data

The operator includes an SREGym-inspired fault injection module that modifies Docker Compose files before operator runs to generate diverse training trajectories for GEPA/DSPy optimization.

#### Fault Taxonomy (22 types)

- **Misconfiguration (6):** wrong_port_mapping, missing_env_var, wrong_image_tag, wrong_entrypoint, bad_volume_mount, duplicate_port_conflict
- **Security (3):** removed_auth_config, exposed_debug_port, privileged_container
- **Metastable (5):** resource_limit_cpu, resource_limit_memory, restart_loop_trigger, slow_healthcheck, tmpfs_too_small
- **Correlated (5):** remove_dependency, break_shared_database, cascading_port_change, remove_shared_network, remove_shared_volume
- **Infrastructure (3):** dns_override, init_failure, read_only_rootfs

#### CLI Usage

```bash
# List all available faults
uv run -m app_operator.fault_injection.cli list

# Inject faults into a repo's compose file
uv run -m app_operator.fault_injection.cli inject \
    --repo-path /path/to/app --num-faults 2 --seed 42

# Revert to original compose file
uv run -m app_operator.fault_injection.cli revert --repo-path /path/to/app
```

#### Training Data Collection with Faults

```bash
# Collect training data with fault injection enabled
scripts/collect_training_data.sh -f hotel
scripts/collect_training_data.sh -f --num-faults 3 --fault-seed 100 hotel
```

#### Design

1. **YAML manipulation (not Docker runtime):** Modifies docker-compose.yml before `docker compose up`. Simpler, reproducible, no elevated privileges needed.
2. **Standalone CLI + shell script:** Faults injected before operator runs, keeping operator unaware of faults. Preserves self-healing integrity.
3. **Backup-based revert:** `.sds-fault-backup` file works without git.
4. **Seeded random:** Reproducible fault selection. Shell script uses `seed + run_number` for diversity.
5. **ABC for extensibility:** `FaultInjector` ABC + `Fault.platform` field enables future `K8sFaultInjector` without changing registry/config/orchestrator.

## 2. LegoAgent Module (`lego_agent/`)

The **LegoAgent** module is an autonomous script generation system that uses AI agents to create orchestrated Python scripts for complex multi-agent workflows. It features an interactive TUI, clarification loops, and supports advanced orchestration patterns.

### Key Features

*   **Web UI Mode**: Modern Next.js interface with real-time visualization of agent thinking and tool usage.
*   **Interactive TUI Mode**: Textual-based rich terminal interface (legacy).
*   **Clarification Loop**: Iteratively refines requirements through AI-powered questions before generating scripts.
*   **Orchestration Patterns**: Built-in support for `fan_out`, `summarize`, and `judge_loop` patterns.
*   **Automatic Repo Detection**: Finds project root by searching upward for `.git` or `sds.toml`.
*   **Script Validation**: Validates syntax and required components before execution.
*   **Environment Setup**: Configures `PYTHONPATH` and work directory automatically.

### Architecture

#### Core Components

*   **LegoAgentEngine (`engine.py`)**:
    *   Manages the clarification loop (up to `max_clarifications` rounds).
    *   Integrates with LangGraph for agent execution.
    *   Streams thinking chunks, tool calls, and results.
    *   Validates generated Python scripts before execution.
    *   Handles response parsing and error repair.

*   **Web UI Server (`server.py`)**:
    *   FastAPI application serving a WebSocket endpoint (`/ws`).
    *   **WebIO**: Adapts the `UserIO` protocol to WebSocket events.
    *   Streams "thinking", "tool_start", "tool_end" events to the frontend.
    *   Handles "start" and "answer" events from the frontend.

*   **Web Frontend (`ui/`)**:
    *   Next.js application using Tailwind CSS and React.
    *   **TerminalLog**: Renders the agent's stream in a terminal-like view.
    *   **InputArea**: Dynamic form for prompts and clarification answers.
    *   Connects to the backend via WebSocket.

*   **I/O Abstraction (`io.py`)**:
    *   **UserIO Protocol**: Duck-typed interface for user interaction.
    *   **WebIO**: WebSocket-based implementation for the Web UI.
    *   **ConsoleIO**: ANSI-colored console output for CLI mode.
    *   **TextualIO**: Rich Textual widgets for TUI mode.

*   **LegoAgentTUI (`tui.py`)**:
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
    *   **LegoAgentStorage (`storage.py`)**: Manages script output with timestamped directories.
    *   **LegoAgentResponse/Result (`models.py`)**: Pydantic models for structured data.

*   **Prompt System (`prompts/`)**:
    *   **system.jinja2**: Comprehensive system instructions (338 lines) with:
        - Orchestration pattern documentation
        - Available runtime API (create_agent, fan_out, summarize, judge_loop)
        - Tool descriptions (read_file, write_file, list_files, find_files, search_content, run_command)
        - Pattern examples with code blocks
        - Best practices section
    *   **user.jinja2**: User request with clarification history and validation checklist.
    *   **repair.jinja2**: Error correction prompt for JSON parsing failures.

### Usage

**Web UI Mode (Recommended):**
```bash
./scripts/start_lego_ui.sh
```
This starts the backend on port 8000 and the frontend on port 3000. Open `http://localhost:3000` in your browser.

**Default TUI Mode:**
```bash
uv run -m lego_agent
```

**With Initial Prompt:**
```bash
uv run -m lego_agent --prompt "Scrape hacker news and summarize top 3 AI stories"
```

**CLI Mode (No TUI):**
```bash
uv run -m lego_agent --no-tui --prompt "Your task"
```

**Available Flags:**
- `--prompt`: Initial user prompt (interactive if omitted in TUI mode).
- `--loop-bound`: Maximum iterations for loops (default: 10).
- `--max-clarifications`: Maximum clarification rounds (default: 5).
- `--config`: Path to `sds.toml` (optional, auto-detects repo root).
- `--model`: Override agent model from configuration.
- `--output-dir`: Output directory (default: `lego_agent_runs`).
- `--work-dir`: Execution directory (default: current directory).
- `--no-run`: Generate script but do not execute it.
- `--no-tui`: Use CLI mode instead of TUI.

### Data Flow (Web UI)

```
Browser (Next.js) <── WebSocket ──> FastAPI (server.py)
                                        ↓
                                LegoAgentEngine
                                        ↓
                                    Clarification Loop
                                        ↓
                                    Script Generation
                                        ↓
                                    Execution
```

### Data Flow (TUI/CLI)

```
User Input (TUI or CLI)
    ↓
Config loading (sds.toml)
    ↓
LegoAgentEngine.run_async()
    ├─→ Clarification Loop (rounds 0 to max_clarifications):
    │   ├─→ Render system/user prompts
    │   ├─→ Stream agent thinking (LangGraph events)
    │   ├─→ Stream tool executions
    │   └─→ Parse response (clarify/ready status)
    │
    ├─→ If status="clarify": ask_questions() → next round
    ├─→ If status="ready": validate_script() → write to storage
    │
LegoAgentStorage.write_script()
    ↓
Script Execution (in work_dir)
    ├─→ Set PYTHONPATH to repo root
    ├─→ Stream stdout/stderr with visual formatting
    └─→ Return exit code
```

### Script Validation

Generated scripts must satisfy:
1. Define `MAX_ITERATIONS = {loop_bound}` constant.
2. Import from `lego_agent.runtime` or related modules.
3. Include `if __name__ == "__main__":` block.
4. Be valid Python with proper syntax.

### Output Structure

Scripts are saved in `lego_agent_runs/<timestamp>/lego_agent.py` with:
- Timestamped directory for each run.
- Standalone execution (no external dependencies except `lego_agent.runtime`).
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
*   **When debugging lego_agent scripts:** Check `lego_agent_runs/<timestamp>/lego_agent.py` and examine the clarification history.
*   **When optimizing prompts:** Use `analyze-prompts` to establish baseline metrics, run `optimize-prompts` with `--dry-run` first to validate inputs, then compare results using `analyze-prompts --compare`.
*   **When adding DSPy signatures:** Add the signature class to `app_operator/dspy_integration/signatures.py` and register it in the `SIGNATURES` dict. Include comprehensive field descriptions and docstrings.
*   **When extending metrics:** Modify `app_operator/dspy_integration/metrics.py` and ensure weights in `CompositeMetric` sum to 1.0. Add corresponding tests.
*   **When adding fault types:** Add the `Fault` to `COMPOSE_FAULTS` in `compose_faults.py`, implement the `_inject_*` method in `ComposeFaultInjector`, register it in the dispatch table, and add tests in `test_compose_faults.py`.
*   **When adding a new app:** Simply run the operator on the repository. The `DeploymentAgent` will attempt to generate appropriate scripts automatically.
*   **When adding lego_agent features:** Test both TUI and CLI modes. Verify the generated scripts are syntactically valid and include required components.
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
*   **Frontend tests** (`lego_agent/ui/app/components/__tests__/`): Jest/React Testing Library tests for UI components.
*   Test organization by component:
    *   `tests/unit/config/`: Configuration validation tests
    *   `tests/unit/agents/`: Agent-specific tests (deployment, monitoring)
    *   `tests/unit/lego_agent/`: LegoAgent module tests (engine, runtime, CLI, prompts)
    *   `tests/unit/dspy_tests/`: DSPy integration tests (config, data loader, metrics, optimizer, signatures)
    *   `tests/unit/fault_injection/`: Fault injection tests (models, config, compose faults, registry, reporter, injector, data loader)
    *   `tests/integration/`: End-to-end scenarios, signal handling, concurrency

#### Running Tests

*   Run all tests (backend + frontend): `scripts/run_tests.sh`
*   Run python tests: `uv run pytest tests/`
*   Run specific category: `uv run pytest tests/unit/` or `uv run pytest tests/integration/`
*   Run frontend tests: `cd lego_agent/ui && npm test`
*   Run with coverage: `uv run pytest tests/ --cov=app_operator`
*   Check if tests pass after you've modified the codebase's behavior.
*   Don't run the sds_operator directly to test; it is a long-running process that will not terminate.

#### Writing Tests - Core Principles

**1. Test Contracts, Not Implementation Details**

Tests should verify **externally visible behavior** (the contract with callers), not internal mechanics.

```python
# ✅ GOOD: Test observable outcomes (return values, file creation, state changes)
def test_deployment_succeeds_after_retry():
    result = deployer.run(max_attempts=3)
    assert result is True
    assert log_file.exists()
    assert "deployment successful" in log_file.read_text()
```

**2. Write Testable, Robust, Clean Frontend Code**

For the `lego_agent` UI, we prioritize robustness and testability:

*   **Component Isolation**: Build components (e.g., `TerminalLog`, `InputArea`) that rely on props rather than global state where possible.
*   **Interaction Testing**: Use `@testing-library/react` to test user interactions (clicks, inputs) rather than internal component state.
*   **Robustness**: Ensure components handle loading states, empty data, and error states gracefully (e.g., connection loss).
*   **Clean Code**: Keep components small and focused. Extract logic into hooks (e.g., `useLegoAgent`) to separate concerns from the view layer.

```typescript
// ✅ GOOD: Testing user interaction and prop handling
it('calls onSendPrompt when submitting prompt', () => {
  render(<InputArea onSendPrompt={mockSend} status="connected" />);
  fireEvent.click(screen.getByText('Run'));
  expect(mockSend).toHaveBeenCalled();
});
```

**3. Prefer Test Doubles Over Mocks for Maintainability**


Use **simple test double classes** instead of mock frameworks for clearer, more maintainable tests. Implement features in a test-double-friendly way.

```python
# ✅ GOOD: Test doubles with real behavior
class StubAgent:
    def generate(self, prompt):
        return "fixed script content"

class TrackingAgent:
    def __init__(self):
        self.calls = []
    def generate(self, prompt):
        self.calls.append(prompt)
        return "response"

def test_with_doubles():
    agent = TrackingAgent()
    deployer.run(agent)
    assert len(agent.calls) == 1  # Clear, readable verification

# ❌ AVOID: Complex mocks with assertions
def test_with_mocks():
    mock_agent = Mock()
    mock_agent.generate.return_value = "response"
    deployer.run(mock_agent)
    mock_agent.generate.assert_called_once_with(ANY, timeout=ANY)  # Fragile
```

**Why:** Test doubles are explicit, easier to understand, and don't break when argument order changes or new parameters are added. Mocks should be reserved for external dependencies (subprocess, file I/O).

**4. Write Human-Readable, Self-Documenting Tests**

Test names and structure should clearly communicate **what is being tested and why**.

```python
# ✅ GOOD: Clear name and focused test
def test_deployment_timeout_returns_partial_output():
    """When deployment times out, partial stdout/stderr should be captured."""
    result = deployer.run(timeout=1)
    assert result.exit_code == -1
    assert "Starting deployment" in result.stdout
    assert "timed out" in result.stderr

# ❌ AVOID: Vague names and multiple unrelated assertions
def test_deployment():
    assert deployer.run() is not None
    assert len(deployer.logs) > 0
    assert deployer.config.timeout == 30
    # What is this actually testing?
```

**Why:** Tests serve as living documentation. Clear test names and focused assertions make it obvious what failed and why when tests break.

**5. Test Edge Cases and Boundary Conditions Thoroughly**

Consider **boundary values, error conditions, special inputs, and resource constraints**.

```python
# ✅ GOOD: Comprehensive edge case coverage
def test_interval_boundary_values():
    assert OperatorConfig(interval=1).interval == 1  # Minimum
    assert OperatorConfig(interval=86400).interval == 86400  # Maximum
    with pytest.raises(ValueError):
        OperatorConfig(interval=0)  # Below minimum
    with pytest.raises(ValueError):
        OperatorConfig(interval=86401)  # Above maximum

def test_unicode_filenames():
    fs.write_text(Path("файл_测试_🎉.txt"), "content")
    assert fs.exists(Path("файл_测试_🎉.txt"))

def test_filesystem_permission_denied():
    fs.simulate_permission_error(path)
    with pytest.raises(FileSystemError):
        fs.write_text(path, "content")
```

**Why:** Edge cases are where bugs hide. Thorough edge case testing catches issues before production.

**6. Keep Tests Fast Without Compromising Quality**

Use **in-memory alternatives and test isolation** to maintain speed.

```python
# ✅ GOOD: In-memory filesystem for speed
def test_file_operations():
    fs = InMemoryFilesystem()  # No disk I/O
    fs.write_text(path, "content")
    assert fs.read_text(path) == "content"

# ✅ GOOD: Test doubles for external dependencies
def test_deployment():
    agent = StubAgent()  # No API calls
    deployer = DeploymentAgent(repo, agent)
    result = deployer.run(max_attempts=1)

# ❌ AVOID: Slow tests with real I/O or network
def test_deployment():
    deployer.run()  # Writes to disk, slow
    time.sleep(5)  # Waiting for external service
```

**Why:** Fast tests enable frequent test runs during development. Slow tests discourage running the full test suite, leading to bugs slipping through.

**7. Make Tests Robust to Code Changes**

Tests should **survive refactoring** as long as behavior is preserved.

```python
# ✅ GOOD: Test public API only
def test_deployment_success():
    result = deployer.deploy(repo_path)
    assert result.success is True
    assert result.exit_code == 0

# ❌ AVOID: Test internal structure
def test_deployment():
    assert isinstance(deployer._runner, ProcessRunner)  # Internal detail
    assert deployer._max_retries == 3  # Private attribute
    deployer._internal_helper()  # Private method
```

**Why:** Tests that depend on internal structure break when refactoring, creating maintenance burden and discouraging code improvements.

**8. Test One Concept Per Test**

Each test should verify **one specific behavior or property**.

```python
# ✅ GOOD: Focused, single-purpose tests
def test_deployment_creates_log_file():
    deployer.run()
    assert log_file.exists()

def test_deployment_captures_stdout():
    result = deployer.run()
    assert "deployment started" in result.stdout

def test_deployment_handles_timeout():
    result = deployer.run(timeout=1)
    assert result.timed_out is True

# ❌ AVOID: Testing multiple unrelated behaviors
def test_deployment():
    deployer.run()
    assert log_file.exists()  # File creation
    assert "started" in result.stdout  # Output capture
    assert result.timed_out is False  # Timeout handling
    assert agent.calls > 0  # Agent interaction
    # If this fails, which behavior broke?
```

**Why:** When a focused test fails, it immediately tells you what broke. When a multi-purpose test fails, you need to debug to find which assertion failed and why.

#### Project-Specific Testing Patterns

**Available Test Fixtures** (`tests/conftest.py`):
*   `test_filesystem`: In-memory filesystem for fast, isolated tests
*   `stub_agent`: Minimal agent that returns simple responses
*   `error_agent`: Agent that always raises errors
*   `timeout_agent`: Agent that simulates timeouts
*   `tracking_agent`: Agent that tracks all calls for verification
*   `configurable_agent`: Agent with configurable responses
*   `repo_with_scripts`: Temp repository with pre-generated working scripts

**Test Agent Doubles** (`tests/fixtures/agents.py`):

Prefer test doubles over mocks for agent interactions:
```python
from tests.fixtures.agents import StubAgent, TrackingAgent, ErrorAgent

def test_with_stub():
    agent = StubAgent()  # Simple, predictable behavior
    result = deployer.run(agent)
    assert result is True

def test_interaction_tracking():
    agent = TrackingAgent()  # Records all calls
    deployer.run(agent)
    assert agent.generation_count == 1
    assert "deploy.sh" in agent.calls[0]["prompt"]
```

**Filesystem Abstraction**:

For testing filesystem operations without disk I/O:
```python
from app_operator.filesystem import InMemoryFilesystem

def test_filesystem():
    fs = InMemoryFilesystem()
    fs.write_text(path, "content")
    assert fs.read_text(path) == "content"

def test_permission_error():
    fs = InMemoryFilesystem()
    fs.simulate_permission_error(path)
    with pytest.raises(FileSystemError):
        fs.write_text(path, "content")
```

**Configuration Validation**:

All configuration dataclasses validate in `__post_init__`:
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

**Environment Independence**:

Tests must not assume external binaries are installed:
*   Mock `shutil.which` and `subprocess.run` to simulate binary presence/absence
*   Use test doubles for agents instead of calling real CLIs
*   See `tests/unit/test_agent_factory.py` for mocking examples

#### Characteristics of High-Quality Tests

Good tests exhibit these qualities:

1. **Robust**: Tests survive refactoring and code changes as long as behavior is preserved
   - Test contracts (public API), not implementation details
   - Avoid coupling to internal structure, private methods, or execution order
   - Use test doubles that don't break when signatures change

2. **Human-Readable**: Tests serve as executable documentation
   - Clear, descriptive test names that explain what and why
   - One concept per test for easy debugging
   - Self-documenting assertions that show expected behavior

3. **Fast**: Tests run quickly to encourage frequent execution
   - Use in-memory alternatives (InMemoryFilesystem) instead of disk I/O
   - Use test doubles instead of calling external services or CLIs
   - Isolate tests to avoid setup/teardown overhead

4. **Thorough**: Tests cover the full behavior space
   - Happy path (expected behavior)
   - Error conditions (permission denied, timeouts, invalid input)
   - Boundary values (0, 1, min, max)
   - Edge cases (Unicode, special characters, resource limits, concurrency)

5. **Focused**: Each test verifies one specific property or behavior
   - Single assertion or closely related assertions
   - Clear failure messages that indicate what broke
   - Avoid testing multiple unrelated concepts in one test

6. **Maintainable**: Tests are easy to understand and update
   - Prefer test doubles over complex mocks
   - Use shared fixtures for common setup
   - Avoid duplication across tests

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
