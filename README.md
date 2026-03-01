# SDS (Self-Defining Systems)

SDS is an AI-native approach that embeds agentic LLMs into the full systems lifecycle—specification, design, implementation, and operation—to autonomously explore, validate, and evolve infrastructure.

## Components

The repository consists of two primary tools:

- **sds_operator**: An intelligent deployment and monitoring tool that autonomously manages applications. It generates deployment/health scripts, self-corrects errors, and performs continuous monitoring using AI agents.
- **lego_agent (experimental)**: An autonomous script generation tool that uses AI agents to create orchestrated Python scripts for complex multi-agent workflows using patterns like `fan_out`, `summarize`, and `judge_loop`.

---

## Getting Started

### Installation

Clone the repository with submodules recursively to include target applications:

```bash
git clone --recursive git@gitlab.cs.washington.edu:syslab/sds.git
cd sds
uv sync
```

### Environment Setup

1. **API Keys**: Create a `.env` file in the project root with your keys (OpenAI, Gemini/Vertex, Anthropic).
2. **Configuration**: Copy the example configuration and edit it to select your preferred agent provider and runtime:
   ```bash
   cp sds.example.toml sds.toml
   ```

---

## Running SDS Operator

The `sds_operator` manages the deployment and health lifecycle of applications.

### Single Application Run
To deploy and monitor a specific application directory (this will auto-generate scripts if missing):
```bash
./sds_operator run apps/deathstarbench/hotelReservation
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
For controlled experiments, use the `init-exp` and `run` commands:

1. **Initialize**: Create an isolated experiment environment from an existing app.
   ```bash
   ./sds_operator init-exp apps/deathstarbench/hotelReservation my-test-run
   ```
2. **Run**: Execute the operator on the created experiment.
   ```bash
   ./sds_operator run exp/hotelReservation/my-test-run
   ```

### Multi-Experiment Runs
To orchestrate multiple experiments in parallel using a configuration file:
```bash
./sds_operator run-exp <exp-name> --parallel 2
```
This looks for configuration in `exp_config/<exp-name>/config.toml`. See `exp_config/example/config.toml` for an example.

#### Per-experiment config overrides
Experiment configs can include any standard `sds.toml` sections alongside `apps`. These sections are written as `sds.toml` into each experiment directory before the run, overriding the app's own config.

Example — run an A/B test with and without `fix_summary_consolidation`:
```
exp_config/
├── with-ltm/config.toml
└── without-ltm/config.toml
```

```toml
# exp_config/with-ltm/config.toml
apps = [
    "apps/deathstarbench/hotelReservation",
    "apps/deathstarbench/socialNetwork"
]

[operator.phase]
fix_summary_consolidation = true
```

```bash
./sds_operator run-exp with-ltm --parallel 2
./sds_operator run-exp without-ltm --parallel 2
```

Any section valid in `sds.toml` (`[agent]`, `[operator]`, `[runtime]`, etc.) can be used.

---

## Running LegoAgent (Experimental)

LegoAgent uses an interactive clarification loop to refine requirements before generating and running an agent workflow graph.

### Web UI Mode (Recommended)
Launch the modern web interface to interact with the agent:
```bash
./scripts/start_lego_ui.sh
```
This will start the backend server and frontend application. Open `http://localhost:3000` in your browser.

### Terminal Mode (CLI)
Run the agent directly from the terminal without the UI. Note that `--work-dir` is required to specify where the generated script will run.

```bash
# Using the wrapper script
./sds_lego_agent --prompt "Improve application test coverage to >= 80%" --work-dir .

# Or using uv directly
uv run -m lego_agent --prompt "Your task description" --work-dir .
```

---

## Component Details

### SDS Operator Runtimes
SDS provides three runtime implementations of the Application Operator, selectable in `sds.toml`:

- **CLI Agent Runtime (`cli_agent`)**: The default implementation. It communicates with external coding agents (like Gemini, Claude, or Codex) via their CLI interfaces.
- **LangGraph Runtime (`langgraph`)**: Orchestrates the deployment and monitoring lifecycle as a stateful graph of LLM-powered nodes using LangChain.
- **ADK Runtime (`adk`)**: Uses Google's Agent Development Kit with Gemini models for deterministic orchestration of agent tasks.

### Operator Outputs & Trajectories
The operator creates a `.sds/` directory in the target application with:
- `deploy.sh` and `health_check.sh`: AI-generated scripts.
- `logs/`: Detailed logs for every deployment and monitoring attempt.
- `trajectories/`: Structured JSON recordings of all agent interactions, including sequential call IDs and correlation with external session logs (e.g., Gemini sessions).

### LegoAgent Features & Orchestration
LegoAgent is designed for complex task automation:
- **Clarification Loop**: AI-powered questions to resolve ambiguities before script generation.
- **Orchestration Patterns**: Built-in support for `fan_out` (parallel execution), `summarize` (aggregation), and `judge_loop` (iterative refinement).
- **Validation**: Generated scripts are validated for syntax and safety before execution.

---

## Command Reference

### `run`
Deploy and monitor an application with autonomous error fixing.
```bash
./sds_operator run <DIR> [--config <FILE>] [--tui]
```

### `init-exp`
Initialize a new experiment from an existing application.
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
- `code_analyzer_system` - System instructions for code analysis
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

### `run-exp`
Run multiple experiments in parallel.
```bash
./sds_operator run-exp <EXPERIMENT_NAME_OR_PATH> [--parallel <N>]
```

The experiment config (`exp_config/<name>/config.toml`) lists apps and can include any `sds.toml` sections (e.g., `[agent]`, `[operator]`, `[operator.phase]`) that will be applied to all apps in the experiment. See [Per-experiment config overrides](#per-experiment-config-overrides) for details.

After all apps complete, `run-exp` writes a `results.json` to the log directory with per-app deployment iterations and status, and prints a summary table to the console.

### `viz-graph`
Visualize the agent's dependency graph (for LangGraph runtime).
```bash
./sds_operator viz-graph [-o graph.png]
```