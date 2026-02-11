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
./sds_lego_agent --no-tui --prompt "Improve application test coverage to >= 80%" --work-dir .

# Or using uv directly
uv run -m lego_agent --no-tui --prompt "Your task description" --work-dir .
```

### Legacy TUI Mode
> **Note**: The TUI mode is deprecated and will be removed in future versions.

```bash
uv run -m lego_agent
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

### `run-exp`
Run multiple experiments in parallel.
```bash
./sds_operator run-exp <EXPERIMENT_NAME_OR_PATH> [--parallel <N>]
```

### `viz-graph`
Visualize the agent's dependency graph (for LangGraph runtime).
```bash
./sds_operator viz-graph [-o graph.png]
```