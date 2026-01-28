# SDS

SDS (Self-Defining Systems) is an AI-native approach that embeds agentic LLMs into the full systems lifecycle—specification, design, implementation, and operation—to autonomously explore, validate, and evolve infrastructure.

## Overview

This repository contains applications, tools, and infrastructure code for the SDS project.

## Project Structure

```
sds/
├── app_operator/    # Application operators and management tools
│   ├── agents/      # Specialized agents (deployer, monitor)
│   └── ...
├── apps/            # Application code and configurations
├── scripts/         # Helper scripts (formatting, checks)
├── sds_operator     # CLI tool for running the operator
├── tests/           # Unit tests
└── tools/           # Shared tools/utilities
```

## Application Operator

The Application Operator (`app_operator`) is a tool for deploying and monitoring applications with automated health checks and graceful lifecycle management. It also includes AI-powered script generation to automatically create deployment and health check scripts for any application repository.

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
model = "gpt-4o-mini" # required for langgraph runtime

[operator]
interval = 30 # Health check interval in seconds (default: 30)
monitoring_max_iters = 5 # Maximum number of health monitoring iterations (default: 5)
deployment_max_iters = 5 # Maximum deployment attempts (default: 5)

[deployment]
platform = "docker" # Deployment platform: "docker" or "k8s" (default: "docker")
target = "local"    # Deployment target: "local" or "remote" (default: "local")

[runtime]
impl = "cli_agent" # "cli_agent" or "langgraph"
```

**LangGraph runtime requirements**
- Set `[runtime] impl = "langgraph"` to use the LangGraph implementation.
- When using LangGraph, you must set both `agent.provider` and `agent.model`.
- Provider mapping in LangGraph:
  - `codex`/`opencode`/`openai` → OpenAI
  - `claude`/`claude-code`/`anthropic` → Anthropic
  - `gemini` → Gemini

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

#### `init-exp` - Initialize Experiment

Initialize a new experiment from an existing application.

**Usage:**
```bash
./sds_operator init-exp <APP_PATH> <EXP_NAME>
```

**Arguments:**
- `APP_PATH`: Path to the source application directory
- `EXP_NAME`: Name of the new experiment

### AI-Assisted Contribution Workflow

SDS supports AI-assisted contributions using coding agent skills (currently available in Claude, Gemini, and Opencode agents).

**Workflow:**

1. **Create a branch**: Use the `git-branch` skill to create a new feature branch
2. **Make commits**: Use the `git-commit` skill to create commits with AI-generated messages
3. **Prepare PR**: Use the `pr-prepare` skill to generate a comprehensive PR description
4. **Open PR**: Create a merge request on GitLab with the generated content

**Example:**

```bash
# In Claude (use "/" prefix):
/git-branch "add monitoring alerts"
# ... edit files ...
/git-commit
/pr-prepare  # Automatically pushes branch to remote

# In opencode or gemini-cli (no "/" prefix needed):
git-branch "add monitoring alerts"
# ... edit files ...
git-commit
pr-prepare  # Automatically pushes branch to remote

# Then create MR on GitLab using the generated title and description
```
