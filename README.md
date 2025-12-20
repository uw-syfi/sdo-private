# SDS

SDS (Self-Defining Systems) is an AI-native approach that embeds agentic LLMs into the full systems lifecycle—specification, design, implementation, and operation—to autonomously explore, validate, and evolve infrastructure.

## Overview

This repository contains applications, tools, and infrastructure code for the SDS project.

## Project Structure

```
sds/
├── agents/          # Application operators and management tools
├── apps/            # Application code and configurations
└── deploy/          # Deployment scripts and configurations
```

## Application Operator

The Application Operator (`app_operator`) is a tool for deploying and monitoring applications with automated health checks and graceful lifecycle management.

### Quick Start

#### Installation

From the `agents` directory:

```bash
cd agents
pip install -e .
```

Or using `uv`:

```bash
cd agents
uv pip install -e .
```

#### Environment Setup

Create a `.env` file in the project root with your OpenAI API key (required for health check summaries):

```bash
OPENAI_API_KEY=your_api_key_here
```

Optionally, customize the LLM model:

```bash
APP_OPERATOR_LLM_MODEL=gpt-4o-mini  # default
```

### Commands

#### List Available Applications

View all registered applications:

```bash
python -m app_operator --list
```

or using the short form:

```bash
python -m app_operator -l
```

This will display:
- Application names
- Descriptions
- Total count of available applications

#### Deploy and Monitor an Application

Deploy an application and start monitoring its health:

```bash
python -m app_operator --app-name <APP_NAME>
```

or using the short form:

```bash
python -m app_operator -a <APP_NAME>
```

**Example:**

```bash
python -m app_operator --app-name hotel
```

This will:
1. Deploy the application
2. Start periodic health checks (default: every 30 seconds)
3. Display health check summaries with LLM-generated insights
4. Continue monitoring until stopped

#### Customize Health Check Interval

Set a custom interval between health checks (in seconds):

```bash
python -m app_operator --app-name hotel --interval 60
```

or:

```bash
python -m app_operator -a hotel -i 60
```

The interval must be at least 1 second. Default is 30 seconds.

#### Stopping the Operator

Press `Ctrl+C` (SIGINT) to gracefully shutdown:
- Stops health check monitoring
- Shuts down the application
- Cleans up resources
- Exits cleanly

### Command Reference

#### `--list`, `-l`
List all available applications.

**Example:**
```bash
python -m app_operator --list
```

#### `--app-name <NAME>`, `-a <NAME>`
**Required** (unless using `--list`). Name of the application to deploy and monitor.

**Example:**
```bash
python -m app_operator --app-name hotel
```

#### `--interval <SECONDS>`, `-i <SECONDS>`
Interval between health checks in seconds. Must be ≥ 1. Default: 30.

**Example:**
```bash
python -m app_operator --app-name hotel --interval 45
```

### How It Works

1. **Deployment**: The operator calls the application's `deploy()` method to start all necessary services.

2. **Health Monitoring**: After deployment, the operator runs periodic health checks:
   - Executes the application's `health_check()` method
   - Uses an LLM to generate concise, operator-friendly summaries
   - Displays results with timestamps and check numbers
   - Continues until shutdown is requested

3. **Graceful Shutdown**: On SIGINT/SIGTERM:
   - Stops the monitoring loop
   - Calls the application's `shutdown()` method
   - Cleans up resources
   - Exits with appropriate status codes

### Examples

#### Basic Usage

```bash
# List available applications
python -m app_operator --list

# Deploy and monitor the hotel application
python -m app_operator --app-name hotel

# Stop with Ctrl+C
```

#### Custom Health Check Interval

```bash
# Check health every 10 seconds
python -m app_operator --app-name hotel --interval 10

# Check health every 2 minutes
python -m app_operator --app-name hotel --interval 120
```

#### Using Installed Command

If installed as a package, you can use the `operator` command directly:

```bash
operator --list
operator --app-name hotel
operator -a hotel -i 60
```

### Exit Codes

- `0`: Success (normal shutdown or successful operation)
- `1`: Failure (deployment failed, invalid arguments, or unexpected errors)

### Troubleshooting

#### "Unknown application" Error

If you see this error, use `--list` to see available applications:

```bash
python -m app_operator --list
```

#### Health Check Failures

Health check failures are displayed in the monitoring output. The operator will continue running and monitoring even if individual checks fail, allowing you to observe recovery or persistent issues.

#### LLM Summary Failures

If LLM summarization fails (e.g., API key issues), the operator will:
- Continue running normally
- Use a fallback summary format
- Display an error message indicating LLM unavailability

### Operator Project Structure

```
agents/
  app_operator/
    __main__.py      # CLI entry point
    operator.py      # Main operator logic
    app_registry.py  # Application registry
    application.py   # Base application class
    apps/            # Application implementations
      hotel.py       # Hotel application
```

## See Also

For more detailed documentation on the application operator, see `agents/README.md`.
