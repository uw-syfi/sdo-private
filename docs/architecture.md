# SDS Architecture

This document explains how the components of SDS fit together, covering providers, runtimes, agent relationships, trajectories, and the full command reference. Start here if you want to understand the system before diving into a specific feature doc.

---

## Component Map

```
libs/agent_cli/          provider abstraction (CodingAgent ABC, AGENT_REGISTRY)
     ├── app_operator/   sds_operator — deploy, monitor, optimize
     │     ├── cli_agent/    runtime: cli_agent (default)
     │     ├── langgraph/    runtime: langgraph
     │     ├── adk/          runtime: adk
     │     ├── trajectory.py recording
     │     ├── dspy_integration/ offline optimization
     │     └── prompts/      Jinja2 + DSPy-optimized templates
     └── lego_agent/     sds_lego_agent — agent workflow generation
```

Both `sds_operator` and `lego_agent` share `libs/agent_cli/` for provider access and read from `sds.toml`, but do not share business logic.

---

## Provider Abstraction

`libs/agent_cli/` provides a `CodingAgent` ABC and a `create_agent_from_config()` factory. Both tools instantiate agents through this layer; no provider-specific code lives in the tools themselves.

Supported providers and required credentials:

| Provider | Env var(s) |
|---|---|
| `gemini` (Vertex AI) | `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` |
| `gemini` (LangChain) | `GOOGLE_API_KEY` |
| `claude` / `claude-code` / `anthropic` | `ANTHROPIC_API_KEY` |
| `codex` / `opencode` / `openai` | `OPENAI_API_KEY` |
| `rlm` | `GOOGLE_APPLICATION_CREDENTIALS` + litellm-compatible model string |

Set credentials in `.env` at the project root.

---

## The Three Runtimes

The runtime controls how each agent call is orchestrated. It is orthogonal to the provider. Switching runtimes requires only changing `[runtime] impl` in `sds.toml`.

### `cli_agent` (default)

Communicates with external coding agents via their CLI interfaces. Broadest provider support: `codex`, `gemini`, `claude`, `claude-code`, `opencode`, `rlm`. No extra dependencies.

### `langgraph`

Orchestrates the deployment and monitoring lifecycle as a stateful graph of LLM-powered nodes using LangChain. Requires `agent.model` to be set. Provider mapping:
- `codex` / `opencode` / `openai` → OpenAI
- `claude` / `claude-code` / `anthropic` → Anthropic
- `gemini` → Gemini via LangChain

Best for complex orchestration where you want explicit state management between steps.

### `adk`

Uses Google's Agent Development Kit. Requires `agent.model` (e.g., `gemini-2.0-flash`) and a `gemini` or `vertex` provider. Provides deterministic, sequential orchestration of agent tasks.

---

## Operator Agents and Their Relationships

The operator always runs agents in this sequence:

```
1. CodeAnalyzerAgent   reads the target codebase
                       writes .sds/code_analysis.md
                              ↓ (passed as context)
2. DeploymentAgent     generates deploy.sh + health_check.sh
                       runs deploy.sh
                       on failure: reads error → generates fix → retries
                       (up to deployment_max_iters)
                              ↓ (only after successful deploy)
3. AppMonitor          runs health_check.sh periodically
                       on failure: triggers re-deploy (back to step 2)
                       (up to monitoring_max_iters)
```

The `CodeAnalyzerAgent` output is not optional context — it is injected into the `DeploymentAgent` prompt as understanding of the app's structure. This is what allows the deployment agent to generate scripts that fit the specific application rather than generic boilerplate.

The `DeploymentAgent` self-healing loop: generate → run → read error → fix → retry. Each iteration is recorded in a trajectory.

Agent call sequence is fixed by the operator. The runtime determines how each individual agent call is invoked, not the order.

---

## Trajectories and the Optimization Loop

Each `sds_operator run` writes trajectory files to `.sds/trajectories/`. A trajectory record contains:
- `phase`: `deployment`, `monitoring`, `code_analysis`, etc.
- `prompt`: the rendered prompt text sent to the agent
- `response`: the agent's response
- `tokens`: input/output token counts
- `success`: whether this call contributed to a successful outcome
- `call_id`: sequential ID for ordering calls within a run

The optimization loop:

```
1. Run deployments       ./sds_operator run <app>
                                ↓
2. Inspect metrics       ./sds_operator analyze-prompts --phase deployment
                                ↓
3. Optimize prompts      ./sds_operator optimize-prompts \
                             --prompts deployer_fix_error --optimizer BootstrapFewShot
                                ↓ (writes to app_operator/prompts/optimized/vN/)
4. Activate              set use_optimized = true in sds.toml
                                ↓
5. Compare               ./sds_operator analyze-prompts \
                             --compare .sds/trajectories:optimized_trajectories
```

Optimized prompts live in versioned directories under `app_operator/prompts/optimized/`. A `latest` symlink points to the most recent version. See `docs/dspy-optimization.md` for the full workflow.

---

## Full Command Reference

### `run`

Deploy and monitor an application with autonomous error fixing.

```bash
./sds_operator run <DIR> [--config <FILE>] [--tui]
```

### `init-exp`

Initialize an isolated experiment copy of an application.

```bash
./sds_operator init-exp <APP_PATH> <EXP_NAME>
```

- `APP_PATH`: source application directory
- `EXP_NAME`: name for the new experiment (written to `exp/<app>/<name>/`)

### `run-exp`

Run all apps in an experiment config in parallel.

```bash
./sds_operator run-exp <EXPERIMENT_NAME_OR_PATH> [--parallel <N>]
```

Reads `exp_config/<name>/config.toml`. After all apps complete, writes `results.json` to the log directory and prints a summary table.

### `analyze-prompts`

Compute metrics on trajectory data: success rates, iteration efficiency, token costs.

```bash
./sds_operator analyze-prompts [OPTIONS]
```

**Options:**

| Option | Description |
|---|---|
| `--trajectories-dir <DIR>` | Trajectory directory (default: `.sds/trajectories`) |
| `--phase <PHASE>` | Filter by phase: `deployment`, `monitoring`, `script_generation`, `exploration` |
| `--model <MODEL>` | Model name for cost calculation (e.g., `claude-sonnet-4-5`) |
| `--format <FORMAT>` | Output format: `table` or `json` (default: `table`) |
| `--compare <DIRS>` | Compare two dirs: `baseline_dir:optimized_dir` |

### `optimize-prompts`

Run offline prompt optimization using DSPy.

```bash
./sds_operator optimize-prompts [OPTIONS]
```

**Options:**

| Option | Description |
|---|---|
| `--prompts <NAMES>` | Prompt names to optimize (required, or use `--list-prompts`) |
| `--trajectories-dir <DIR>` | Trajectory data directory (default: `.sds/trajectories`) |
| `--output-dir <DIR>` | Output directory (default: auto-versioned) |
| `--config <FILE>` | Path to `sds.toml` |
| `--optimizer <TYPE>` | DSPy optimizer: `BootstrapFewShot`, `BootstrapFewShotWithRandomSearch`, `MIPROv2`, `COPRO` |
| `--num-examples <N>` | Training examples (default: from config or 30) |
| `--teacher-model <MODEL>` | Teacher model (default: from config or `claude-sonnet-4-5`) |
| `--dry-run` | Validate inputs without running |
| `--list-prompts` | List available prompts and exit |

**Available prompts:**

| Name | Description |
|---|---|
| `deployer_system` | System instructions for deployment agent |
| `deployer_generate_script` | Generate deployment scripts |
| `deployer_fix_error` | Fix deployment errors |
| `deployer_summarize` | Summarize deployment results |
| `code_analyzer_system` | System instructions for code analysis |
| `code_analyzer_user` | Analyze codebase for deployment |
| `monitor_analyze_health` | Analyze application health |
| `agentflow_system` | System instructions for agentflow |
| `agentflow_user` | Generate agentflow scripts |
| `agentflow_repair` | Repair malformed responses |

### `viz-graph`

Visualize the agent dependency graph (LangGraph runtime only).

```bash
./sds_operator viz-graph [-o graph.png]
```

---

## Experiment Workflow

Experiments allow controlled A/B testing of operator configurations.

### Setup

```bash
# 1. Create isolated copies of the target app
./sds_operator init-exp apps/deathstarbench/hotelReservation my-test-run

# 2. Run the operator on the experiment
./sds_operator run exp/hotelReservation/my-test-run
```

### Multi-experiment parallel runs

```bash
./sds_operator run-exp <exp-name> --parallel 2
```

This reads `exp_config/<exp-name>/config.toml`. Example:

```
exp_config/
├── with-ltm/config.toml
└── without-ltm/config.toml
```

### Per-experiment config overrides

Experiment configs can include any standard `sds.toml` sections alongside `apps`. These are written as `sds.toml` into each experiment directory before the run, overriding the app's own config.

```toml
# exp_config/with-ltm/config.toml
apps = [
    "apps/deathstarbench/hotelReservation",
    "apps/deathstarbench/socialNetwork"
]

[operator.phase]
fix_summary_consolidation = true
```

Any section valid in `sds.toml` (`[agent]`, `[operator]`, `[runtime]`, etc.) can be used as an override.

---

## Operator Phase Flags

`[operator.phase]` in `sds.toml` controls which optional operator capabilities are active. All flags default to a safe, minimal-footprint baseline.

```toml
[operator.phase]
code_analysis = true          # default: true
fix_summary_consolidation = true  # default: true
git_integration = false       # default: false
```

| Flag | Default | Description |
|---|---|---|
| `code_analysis` | `true` | Run `CodeAnalyzerAgent` before deployment. Produces `.sds/code_analysis.md` and injects app structure understanding into the deployer prompt. Disable to skip the analysis step and reduce token usage. |
| `fix_summary_consolidation` | `true` | After the self-healing loop, consolidate fix summaries across iterations into a single structured report. |
| `git_integration` | `false` | Expose the `make_change_on_remote_copy` tool to the agent. When enabled, the agent can push branches and open GitLab MRs from within the operator loop. Requires GitLab credentials. **Opt-in only** — do not enable in local dev environments. |

---

## DSPy Configuration: Canary Deployment and Auto-Rollback

### Full DSPy config block

```toml
[dspy]
use_optimized = false           # Use DSPy-optimized prompts
optimized_version = "latest"    # Version to use ("v1", "v2", "latest")
fallback_to_baseline = true     # Fall back to Jinja2 on errors
enable_online_learning = false  # Enable feedback collection
feedback_sample_rate = 0.1      # Fraction of runs to collect feedback

[dspy.optimization]
optimizer = "BootstrapFewShot"       # DSPy optimizer
teacher_model = "claude-sonnet-4-5"  # Model for optimization
num_examples = 30                    # Training examples
validation_split = 0.2               # Validation data fraction

[dspy.optimization.metric_weights]
success = 0.6      # Deployment success (binary)
efficiency = 0.25  # Iteration efficiency
tokens = 0.15      # Token efficiency
```

### Canary deployment

Roll out optimized prompts to a percentage of deployments:

```toml
[dspy]
use_optimized = true
canary_deployment = true
canary_percentage = 0.2  # 20% of deployments use optimized prompts
```

Routing is deterministic per repository (based on `hash(repo_path)`), ensuring consistent behavior for debugging.

### Auto-rollback

Automatically revert to baseline prompts if performance degrades:

```toml
[dspy.auto_rollback]
enabled = true
success_rate_threshold = 0.05  # Rollback if success rate drops by 5%
evaluation_window = 100        # Evaluate over last 100 runs
```

### Optimized prompt file structure

```
app_operator/prompts/optimized/
├── v1/
│   ├── deployer_fix_error.dspy.json
│   ├── deployer_summarize.dspy.json
│   └── metadata.json
├── v2/
│   └── ...
└── latest -> v2
```
