# SDS Operator

Application deployment and monitoring operator with automated health checks.

## Overview

The SDS Operator is a Python module that manages application lifecycle through a clean, abstract interface. It handles deployment, continuous health monitoring, and graceful shutdown of applications.

**Key Features:**
- Abstract application interface for flexible implementations
- Automated health checks at configurable intervals
- LangGraph-based monitoring loop with LLM status summaries
- Automatic environment variable loading from `.env` files
- Graceful shutdown on Ctrl+C (SIGINT/SIGTERM)
- Easy extensibility for multiple applications

## Installation

The operator uses `uv` for package management. To set up:

```bash
cd agents
uv sync
```

## Usage




## Coding Agent Mode

The operator includes a "Coding Agent Mode" that can autonomously generate deployment scripts and self-fix deployment errors. This mode can be powered by either **Gemini CLI** or **Codex CLI**.

### Autonomous Deployment

```bash
cd agents
uv run python -m app_operator /path/to/your/repository
```

This will:
1. Analyze the repository structure.
2. Generate `deploy.sh` and `health_check.sh` scripts if they don't exist.
3. Attempt to deploy the application.
4. If deployment fails, the coding agent will analyze the errors and automatically fix the scripts.
5. Once deployed, the agent will analyze health check results to provide optimization suggestions.

### Configuration (`sds.toml`)

You can configure which coding agent to use by creating an `sds.toml` or `config.toml` file in the root of the target repository:

```toml
[agent]
provider = "gemini"  # Options: "gemini" or "codex" (default)
model = "gemini-pro" # Optional: Override the default model
```

### Script Generation

To just generate scripts without deploying:

```bash
cd agents
uv run python -m app_operator generate-scripts /path/to/your/repository
```

## Development

### Running Tests

```bash
cd agents
uv run python -m pytest
```

### Type Checking

```bash
cd agents
uv run mypy app_operator/
```

### Code Formatting

```bash
cd agents
uv run black app_operator/
```



## License

Part of the SDS project.
