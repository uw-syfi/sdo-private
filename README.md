# SDS

SDS (Self-Defining Systems) is an AI-native approach that embeds agentic LLMs into the full systems lifecycle—specification, design, implementation, and operation—to autonomously explore, validate, and evolve infrastructure.

## Overview

This repository contains applications, tools, and infrastructure code for the SDS project.

## Project Structure

SDS consists of multiple AI-powered tools for autonomous infrastructure management and orchestration:

- **Agentflow**: An autonomous script generation tool that uses AI agents to create orchestrated Python scripts for complex multi-agent workflows. Features an interactive TUI, clarification loops, and built-in orchestration patterns (fan_out, summarize, judge_loop).

- **Application Operator**: An intelligent deployment and monitoring tool that autonomously deploys applications, self-corrects deployment errors, and performs continuous health monitoring using AI agents. Supports three runtime implementations (CLI Agent, LangGraph, ADK) and multiple AI providers.

```
sds/
├── agentflow/            # Autonomous script generation module
│   ├── cli.py            # CLI and TUI mode selection
│   ├── engine.py         # Core clarification loop engine
│   ├── runtime.py        # LangGraph agent runtime with orchestration patterns
│   ├── tui.py            # Textual-based interactive TUI
│   └── prompts/          # Jinja2 prompt templates
├── app_operator/         # Core operator logic
│   ├── cli_agent/        # CLI-based agent implementation
│   │   ├── agents/       # Specialized agents (deployer, monitor, code_analyzer)
│   │   └── backend/      # Coding agent CLI backends (claude, gemini, codex, opencode)
│   ├── langgraph/        # LangGraph-based implementation
│   ├── adk/              # Google ADK-based implementation
│   ├── commands/         # CLI commands (run, init_exp, viz_graph)
│   └── prompts/          # Jinja2 prompt templates
├── apps/                 # Application code and configurations
├── scripts/              # Helper scripts (formatting, checks)
├── sds_operator          # CLI tool for running the operator
└── tests/                # Unit and integration tests
```

## Application Operator

The Application Operator (`app_operator`) is a tool for deploying and monitoring applications with automated health checks and graceful lifecycle management. It also includes AI-powered script generation to automatically create deployment and health check scripts for any application repository.

SDS provides three runtime implementations of the Application Operator:
- **CLI Agent Runtime (`cli_agent`)**: Default implementation that communicates with external coding agents via their CLI interfaces (supports all providers).
- **LangGraph Runtime (`langgraph`)**: Orchestrates the deployment and monitoring lifecycle as a stateful graph of LLM-powered nodes using LangChain.
- **ADK Runtime (`adk`)**: Uses Google's Agent Development Kit with Gemini models for deterministic orchestration of agent tasks.

### Key Features

- **AI-Powered Script Generation**: Automatically generate deployment and health check scripts for any repository using LLM analysis
- **Automated Deployment**: Deploy applications with a single command
- **Continuous Health Monitoring**: Periodic health checks with intelligent summaries
- **Graceful Lifecycle Management**: Clean startup and shutdown handling
- **LLM-Powered Insights**: Health check summaries generated using OpenAI models

### Quick Start

#### Installation

From the project root:

```bash
uv sync
```

Or using pip:

```bash
pip install -e .
```

#### Environment Setup

1. Create a `.env` file in the project root with the necessary API keys and configuration.

For OpenAI (required for Codex agent):
```bash
OPENAI_API_KEY=your_api_key_here
```

For Gemini Agent (Vertex AI):
```bash
GOOGLE_APPLICATION_CREDENTIALS="path/to/your/credentials.json"
GOOGLE_CLOUD_PROJECT="your_project_id"
GOOGLE_CLOUD_LOCATION="global"
```

For Gemini via LangChain:
```bash
GOOGLE_API_KEY=your_api_key_here
```

For Anthropic via LangChain:
```bash
ANTHROPIC_API_KEY=your_api_key_here
```

2. Configure the agent and operator settings by copying `sds.example.toml` to `sds.toml` in the project root:

```bash
cp sds.example.toml sds.toml
```

Then edit `sds.toml` to configure your settings:

```toml
[agent]
provider = "codex"  # or "gemini", "claude", "opencode", "openai", "anthropic"
model = "gpt-4o-mini" # required for langgraph and adk runtimes
# location = "us-central1" # optional: specify vertex AI location (default: us-central1)

[operator]
interval = 30 # Health check interval in seconds (default: 30)
monitoring_max_iters = 5 # Maximum number of health monitoring iterations (default: 5)
deployment_max_iters = 20 # Maximum deployment attempts (default: 20)

[deployment]
platform = "docker" # Deployment platform: "docker" or "k8s" (default: "docker")
target = "local"    # Deployment target: "local" or "remote" (default: "local")

[runtime]
impl = "cli_agent" # "cli_agent", "langgraph", or "adk"

# DSPy Prompt Optimization (optional)
[dspy]
use_optimized = false           # Use DSPy-optimized prompts
optimized_version = "latest"    # Version to use (e.g., "v1", "latest")
fallback_to_baseline = true     # Fall back to Jinja2 on errors
enable_online_learning = false  # Enable feedback collection
feedback_sample_rate = 0.1      # Fraction of runs to collect feedback

[dspy.optimization]
optimizer = "BootstrapFewShot"  # DSPy optimizer to use
teacher_model = "claude-sonnet-4-5"  # Model for optimization
num_examples = 30               # Number of training examples
validation_split = 0.2          # Validation data fraction

[dspy.optimization.metric_weights]
success = 0.6      # Weight for deployment success
efficiency = 0.25  # Weight for iteration efficiency
tokens = 0.15      # Weight for token efficiency

[dspy.auto_rollback]
enabled = true                  # Enable automatic rollback
success_rate_threshold = 0.05   # Rollback if success rate drops by this fraction
evaluation_window = 100         # Number of recent runs to evaluate
```

**Runtime Requirements**

**LangGraph runtime:**
- Set `[runtime] impl = "langgraph"` to use the LangGraph implementation.
- When using LangGraph, you must set both `agent.provider` and `agent.model`.
- Provider mapping in LangGraph:
  - `codex`/`opencode`/`openai` → OpenAI
  - `claude`/`claude-code`/`anthropic` → Anthropic
  - `gemini` → Gemini (via LangChain)

**ADK runtime:**
- Set `[runtime] impl = "adk"` to use Google's Agent Development Kit implementation.
- Must set `agent.model` (e.g., "gemini-2.0-flash").
- Only supports `gemini` or `vertex` providers.
- Requires `google-adk` package.
- Provides deterministic orchestration of agent tasks.

**CLI Agent runtime (default):**
- Set `[runtime] impl = "cli_agent"` to use the CLI-based implementation.
- Communicates with external coding agents via their CLI interfaces.
- Supports all providers: `codex`, `gemini`, `claude`, `claude-code`, `opencode`.

### Experiment Workflow

#### 1. Create an Experiment

First, create an isolated experiment environment from an existing application using `init-exp`.

```bash
./sds_operator init-exp apps/target-app my-experiment-name
```
This copies the application to `exp/<app-name>/<exp-name>`, removes existing git history and SDS configurations, and initializes a new git repository.

#### 2. Run the Experiment

Next, use the `run` command to deploy and monitor the experiment using an AI agent.

```bash
./sds_operator run exp/target-app/my-experiment-name
```

**What it does:**
- **Auto-Scripting**: Automatically generates deployment and health check scripts if they are missing. Scripts are created in `<app-dir>/.sds`.
- **Self-Healing Deployment**: If a deployment fails, the AI agent analyzes the error logs, identifies the root cause, and automatically fixes the scripts before retrying (up to 5 attempts).
- **AI-Powered Analysis**: Provides intelligent analysis of health check results to suggest improvements.
- **Trajectory Recording**: All agent interactions are recorded with sequential call IDs for analysis and debugging.

**Outputs:**

The operator creates the following in the `<app-dir>/.sds` directory:
- `deploy.sh` and `health_check.sh`: Generated deployment and health check scripts
- `logs/`: Deployment and monitoring logs
- `trajectories/`: Agent interaction recordings
  - `trajectory_YYYYMMDD-HHMMSS.json`: Complete interaction history with sequential call IDs
  - `trajectory.json`: Symlink to the latest trajectory file
  - `gemini_sessions/`: Gemini CLI session files (when using Gemini agent), correlated with call IDs

**Trajectory Structure:**

Each trajectory file contains:
- `metadata`: Run information (run_id, timestamps, status)
- `calls`: Sequential list of all agent calls with call_id, phase, start/end times, and context
- Phase arrays (`exploration`, `script_generation`, `deployment`, `monitoring`): Conversations grouped by phase, each with its call_id
- `gemini_sessions`: Gemini CLI sessions matched to call IDs (when using Gemini agent)

This structure makes it easy to trace specific agent calls and correlate them with external session logs.

### Command Reference

#### `run` - Deploy and Monitor

Deploy an application with autonomous error fixing and AI-powered health monitoring.

**Usage:**
```bash
./sds_operator run <DIR> [options]
```

**Arguments:**
- `DIR`: Path to the repository directory (required, positional argument)

**Options:**
- `--config <FILE>`: Path to configuration file (default: `sds.toml` in target dir)

#### `viz-graph` - Visualize Dependency Graph

Visualize the agent's dependency graph in LangGraph.

**Usage:**
```bash
./sds_operator viz-graph [options]
```

**Options:**
- `--output <FILE>`, `-o <FILE>`: Output file path (e.g., `graph.png`, `graph.mermaid`).
  - If `.png` extension is used, generates a PNG image (requires internet access).
  - Otherwise, saves the Mermaid syntax text.
  - If omitted, prints Mermaid syntax to stdout.

#### `init-exp` - Initialize Experiment

Initialize a new experiment from an existing application.

**Usage:**
```bash
./sds_operator init-exp <APP_PATH> <EXP_NAME>
```

**Arguments:**
- `APP_PATH`: Path to the source application directory
- `EXP_NAME`: Name of the new experiment

#### `analyze-prompts` - Analyze Prompt Performance

Analyze trajectory data to compute metrics on prompt performance, including success rates, iteration efficiency, and token costs.

**Usage:**
```bash
# Analyze all trajectories
./sds_operator analyze-prompts

# Filter by phase
./sds_operator analyze-prompts --phase deployment

# Calculate costs
./sds_operator analyze-prompts --model claude-sonnet-4-5

# Compare baseline vs optimized
./sds_operator analyze-prompts --compare baseline_dir:optimized_dir

# JSON output
./sds_operator analyze-prompts --format json
```

**Options:**
- `--trajectories-dir <DIR>`: Directory containing trajectory files (default: `.sds/trajectories`)
- `--phase <PHASE>`: Filter by phase (deployment, monitoring, script_generation, exploration)
- `--model <MODEL>`: Model name for cost calculation (e.g., `claude-sonnet-4-5`)
- `--format <FORMAT>`: Output format: `table` or `json` (default: `table`)
- `--compare <DIRS>`: Compare two directories (format: `baseline_dir:optimized_dir`)

#### `optimize-prompts` - Optimize Prompts with DSPy

Run offline prompt optimization using DSPy to improve deployment success rates and efficiency.

**Usage:**
```bash
# List available prompts
./sds_operator optimize-prompts --list-prompts

# Optimize specific prompts
./sds_operator optimize-prompts --prompts deployer_fix_error deployer_summarize

# Dry run (validate inputs only)
./sds_operator optimize-prompts --prompts deployer_fix_error --dry-run

# Custom optimizer and settings
./sds_operator optimize-prompts \
    --prompts deployer_fix_error \
    --optimizer MIPROv2 \
    --teacher-model claude-opus-4-5 \
    --num-examples 50
```

**Options:**
- `--prompts <NAMES>`: Prompt names to optimize (required)
- `--trajectories-dir <DIR>`: Trajectory data directory (default: `.sds/trajectories`)
- `--output-dir <DIR>`: Output directory for optimized prompts (default: auto-versioned)
- `--config <FILE>`: Path to sds.toml configuration file
- `--optimizer <TYPE>`: DSPy optimizer (BootstrapFewShot, BootstrapFewShotWithRandomSearch, MIPROv2, COPRO)
- `--num-examples <N>`: Number of training examples (default: from config or 30)
- `--teacher-model <MODEL>`: Teacher model for optimization (default: from config or claude-sonnet-4-5)
- `--dry-run`: Validate inputs without running optimization
- `--list-prompts`: List available prompts and exit

**Available Prompts:**
- `deployer_system` - System instructions for deployment agent
- `deployer_generate_script` - Generate deployment scripts
- `deployer_fix_error` - Fix deployment errors
- `deployer_summarize` - Summarize deployment results
- `code_analyzer_system` - System instructions for code analyzer
- `code_analyzer_user` - Analyze codebase for deployment
- `monitor_analyze_health` - Analyze application health
- `agentflow_system` - System instructions for agentflow
- `agentflow_user` - Generate agentflow scripts
- `agentflow_repair` - Repair malformed responses

### DSPy Prompt Optimization Workflow

The Application Operator supports offline prompt optimization using DSPy to improve deployment success rates and efficiency by learning from historical trajectory data.

#### Quick Start

1. **Run deployments** to generate trajectory data:
   ```bash
   ./sds_operator run apps/my-app
   ```

2. **Analyze baseline performance**:
   ```bash
   ./sds_operator analyze-prompts --phase deployment
   ```

3. **Optimize prompts**:
   ```bash
   ./sds_operator optimize-prompts \
       --prompts deployer_fix_error deployer_summarize \
       --optimizer BootstrapFewShot
   ```

4. **Enable optimized prompts** in `sds.toml`:
   ```toml
   [dspy]
   use_optimized = true
   optimized_version = "latest"
   fallback_to_baseline = true
   ```

5. **Test and compare**:
   ```bash
   ./sds_operator run apps/my-app
   ./sds_operator analyze-prompts --compare .sds/trajectories:optimized_trajectories
   ```

#### Optimization Metrics

DSPy optimization uses a composite metric combining:
- **Deployment Success (60%)**: Binary metric for successful deployment
- **Iteration Efficiency (25%)**: Rewards fewer iterations to success
- **Token Efficiency (15%)**: Rewards lower token usage

Metric weights are configurable in `sds.toml` under `[dspy.optimization.metric_weights]`.

#### Canary Deployment

Gradually roll out optimized prompts to a percentage of deployments:

```toml
[dspy]
use_optimized = true
canary_deployment = true
canary_percentage = 0.2  # 20% of deployments use optimized prompts
```

Routing is deterministic per repository (based on `hash(repo_path)`), ensuring consistent behavior for debugging.

#### Auto-Rollback

Automatically rollback to baseline prompts if performance degrades:

```toml
[dspy.auto_rollback]
enabled = true
success_rate_threshold = 0.05  # Rollback if success rate drops by 5%
evaluation_window = 100        # Evaluate over last 100 runs
```

#### File Structure

After optimization, prompts are saved in versioned directories:

```
app_operator/prompts/optimized/
├── v1/
│   ├── deployer_fix_error.dspy.json     # Optimized module with demos
│   ├── deployer_summarize.dspy.json     # Optimized module with demos
│   └── metadata.json                    # Optimization metadata
├── v2/
│   └── ...
└── latest -> v2                          # Symlink to latest version
```

#### Troubleshooting

**No training examples found:**
- Ensure you've run deployments to generate trajectory data in `.sds/trajectories/`
- Check that trajectories contain the phase you're optimizing (e.g., `deployment`)

**Optimization fails:**
- Run with `--dry-run` first to validate inputs
- Check teacher model API access (credentials, rate limits)
- Reduce `--num-examples` if optimization is slow

**Optimized prompts not used:**
- Verify `use_optimized = true` in `sds.toml`
- Check that optimized version exists: `ls app_operator/prompts/optimized/`
- Review logs for fallback warnings

**Performance degraded:**
- Compare metrics: `analyze-prompts --compare baseline_dir:optimized_dir`
- Try different optimizer: `MIPROv2`, `COPRO`, `BootstrapFewShotWithRandomSearch`
- Increase training examples: `--num-examples 50`

### Agentflow Module

The Agentflow module allows you to autonomously generate orchestrated Python scripts for complex tasks using AI agents. It prompts for a user specification, runs a clarification loop, and produces a standalone script.

**Usage:**

Run the agentflow module using `uv` or directly with python. The module launches an interactive TUI (Terminal UI) by default:

```bash
uv run -m agentflow
```

Or provide a prompt directly:

```bash
uv run -m agentflow --prompt "Scrape hacker news and summarize top 3 AI stories" --loop-bound 10
```

For traditional CLI mode without the TUI:

```bash
uv run -m agentflow --no-tui --prompt "Your task description"
```

**Options:**

- `--prompt`: Initial user prompt (interactive if omitted in TUI mode, reads from stdin in CLI mode).
- `--loop-bound`: Maximum iterations for loops in the generated script (default: 10).
- `--max-clarifications`: Maximum rounds of clarification questions (default: 5).
- `--config`: Path to `sds.toml` (optional, auto-detects repo root).
- `--model`: Override the agent model defined in configuration.
- `--output-dir`: Directory to save generated scripts (default: `agentflow_runs`).
- `--work-dir`: Directory to execute the generated script in (default: current directory).
- `--no-run`: Generate script but do not execute it.
- `--no-tui`: Run in standard CLI mode instead of interactive TUI mode.

**Features:**

- **Interactive TUI Mode**: Rich terminal interface with real-time output streaming, thinking process visualization, and tool execution display.
- **Automatic Repo Detection**: Automatically finds the project root by searching for `.git` or `sds.toml`.
- **Clarification Loop**: Iteratively refines requirements through AI-powered questions before generating the final script.
- **Script Validation**: Validates generated Python scripts for syntax and required components.
- **Environment Setup**: Automatically configures `PYTHONPATH` and work directory for script execution.

**Output:**

Generated scripts are saved in `agentflow_runs/<timestamp>/agentflow.py`. These scripts are standalone and include:
- `MAX_ITERATIONS` constant for loop bounding.
- Imports from `agentflow.runtime` for agent orchestration tools (`fan_out`, `summarize`, `judge_loop`).

