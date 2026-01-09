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

1. Create a `.env` file in the project root with your OpenAI API key (required for health check summaries):

```bash
OPENAI_API_KEY=your_api_key_here
```

2. Configure the agent and operator settings using `sds.toml` in the target repository (or use the default configuration):

```toml
[agent]
provider = "codex"  # or "gemini"
model = "gpt-4o-mini" # optional

[operator]
interval = 30 # Health check interval in seconds
```

### Commands

#### Deploy and Monitor an App with AI-Assisted Self-Healing

 It uses an AI agent (Codex or Gemini) to deploy and monitor applications.

**What it does:**
- **Auto-Scripting**: Automatically generates deployment and health check scripts if they are missing. Scripts are created in `<app-dir>/.sds`.
- **Self-Healing Deployment**: If a deployment fails, the AI agent analyzes the error logs, identifies the root cause, and automatically fixes the scripts before retrying (up to 5 attempts).
- **AI-Powered Analysis**: Provides intelligent analysis of health check results to suggest improvements.

```bash
./sds_operator /path/to/repository
```


### Command Reference

#### Deploy and Monitor a Repository

Deploy an application with autonomous error fixing and AI-powered health monitoring.

**Arguments:**
- `DIR`: Path to the repository directory (required, positional argument)

**Options:**
- `--config <FILE>`: Path to configuration file (default: `sds.toml` in target dir)

**Example:**
```bash
./sds_operator /path/to/repository
```

