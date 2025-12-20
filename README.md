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

The Application Operator (`app_operator`) is a tool for deploying and monitoring applications with automated health checks and graceful lifecycle management. It also includes AI-powered script generation to automatically create deployment and health check scripts for any application repository.

### Key Features

- **AI-Powered Script Generation**: Automatically generate deployment and health check scripts for any repository using LLM analysis
- **Automated Deployment**: Deploy applications with a single command
- **Continuous Health Monitoring**: Periodic health checks with intelligent summaries
- **Graceful Lifecycle Management**: Clean startup and shutdown handling
- **LLM-Powered Insights**: Health check summaries generated using OpenAI models

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

#### Generate Deployment Scripts

Automatically generate deployment and health check scripts for any application repository:

```bash
python -m app_operator generate-scripts /path/to/repository
```

**Example:**

```bash
python -m app_operator generate-scripts ~/projects/my-app
```

This will:
1. Analyze the repository structure (Docker, Kubernetes, build files, etc.)
2. Use an LLM (via the `codex` CLI tool) to generate two comprehensive bash scripts:
   - `.sds/deploy.sh` - Deployment script with start, stop, restart, status, logs, build, and cleanup commands
   - `.sds/health_check.sh` - Health monitoring script with container, port, endpoint, database, and performance checks
3. Make both scripts executable and place them in the `.sds` subdirectory

**Features:**
- Automatically detects deployment method (Docker Compose, Kubernetes, etc.)
- Generates production-ready scripts with proper error handling
- Includes colored output and helpful status messages
- Uses relative paths for portability
- Adapts to application type (microservices, monolith, Node.js, Python, Go, etc.)

**Requirements:**
- `codex` binary must be available in PATH
- Repository must exist and be accessible

**Customizing the AI Model:**

```bash
python -m app_operator generate-scripts /path/to/repository --model gpt-4o
```

The generated scripts follow best practices:
- Proper bash error handling (`set -e`)
- Color-coded output for readability
- Command-line argument parsing with help text
- Comprehensive prerequisite checks
- Relative path resolution using `SCRIPT_DIR`
- No hardcoded credentials or absolute paths

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

#### `generate-scripts <DIR>`
Generate deployment and health check scripts for a repository using AI.

**Arguments:**
- `<DIR>`: Path to the repository directory (required)

**Options:**
- `--model <MODEL>`: Specify the AI model to use (optional, defaults to `gpt-4o-mini`)

**Example:**
```bash
python -m app_operator generate-scripts /path/to/repository
python -m app_operator generate-scripts ~/my-app --model gpt-4o
```

#### `list` or `--list`, `-l`
List all available applications.

**Example:**
```bash
python -m app_operator list
python -m app_operator --list  # legacy
```

#### `run --app-name <NAME>` or `--app-name <NAME>`, `-a <NAME>`
**Required** (unless using `list` or `generate-scripts`). Name of the application to deploy and monitor.

**Example:**
```bash
python -m app_operator run --app-name hotel
python -m app_operator --app-name hotel  # legacy
```

#### `--interval <SECONDS>`, `-i <SECONDS>`
Interval between health checks in seconds. Must be ≥ 1. Default: 30. Used with `run` command.

**Example:**
```bash
python -m app_operator run --app-name hotel --interval 45
python -m app_operator --app-name hotel --interval 45  # legacy
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

#### Generating Scripts for a New Application

```bash
# Generate deployment scripts for a repository
python -m app_operator generate-scripts /path/to/my-application

# Generate scripts with a specific AI model
python -m app_operator generate-scripts ~/projects/microservices-app --model gpt-4o

# After generation, the scripts will be in the .sds directory
ls /path/to/my-application/.sds/
# Output: deploy.sh  health_check.sh

# Test the generated deployment script
cd /path/to/my-application/.sds
./deploy.sh start

# Test the generated health check script
./health_check.sh
```

#### Basic Usage

```bash
# List available applications
python -m app_operator list

# Deploy and monitor the hotel application
python -m app_operator run --app-name hotel

# Stop with Ctrl+C
```

#### Custom Health Check Interval

```bash
# Check health every 10 seconds
python -m app_operator run --app-name hotel --interval 10

# Check health every 2 minutes
python -m app_operator run --app-name hotel --interval 120
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

#### "codex binary not found in PATH" Error

If you see this error when using `generate-scripts`, ensure the `codex` CLI tool is installed and available:

```bash
# Check if codex is installed
which codex

# If not installed, install it according to your system's instructions
```

The `codex` binary is required for AI-powered script generation.

#### Script Generation Failures

If script generation fails:
- Ensure the target directory exists and is readable
- Check that you have write permissions for the target directory
- Verify the `codex` tool is working: `codex --version`
- Try specifying a different model: `--model gpt-4o`
- Check the terminal output for detailed error messages from the codex tool

#### Generated Scripts Not Working

If generated scripts don't work as expected:
- Review the generated scripts in the `.sds` directory
- Check that the scripts correctly identified your deployment method
- Manually adjust the scripts if needed (they're standard bash scripts)
- Report issues with the repository structure that caused incorrect generation

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
    __main__.py          # CLI entry point
    operator.py          # Main operator logic
    script_generator.py  # AI-powered script generation
    app_registry.py      # Application registry
    application.py       # Base application class
    apps/                # Application implementations
      hotel.py           # Hotel application
```

## See Also

For more detailed documentation on the application operator, see `agents/README.md`.
