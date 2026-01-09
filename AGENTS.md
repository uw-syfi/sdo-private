# SDS (Self-Defining Systems)

**Context for LLM Agent**

SDS is an AI-native project designed to autonomously explore, validate, and evolve infrastructure by embedding agentic LLMs into the system lifecycle. It primarily consists of an **Application Operator** (an intelligent agent) and the **DeathStarBench** suite (microservices benchmarks) used as target applications.

## Project Structure

```
sds/
├── app_operator/    # Core operator logic.
│   ├── agents/      # Specialized agents (deployer, monitor).
│   └── ...
├── apps/            # Application code (DeathStarBench suite).
│   └── deathstarbench/
│       ├── hotelReservation/ # Go-based microservices app.
│       ├── socialNetwork/    # C++/Python/Go microservices app.
│       └── ...
├── deploy/          # Deployment and operational scripts.
│   └── deathstarbench/
│       └── hotel/   # Deployment scripts for Hotel Reservation.
├── tools/           # Shared tools/utilities (e.g., healthcheck).
└── README.md        # Root project documentation.
```

## 1. Application Operator (`app_operator/`)

The **Application Operator** is a Python tool that autonomously deploys, monitors, and manages applications. It features a "Codex" mode to self-correct deployment scripts and uses LLMs to summarize health checks.

### Setup & Usage

*   **Dependency Management:** Uses `uv` (or `pip`).
*   **Installation:**
    ```bash
    uv sync  # or pip install -e .
    ```
*   **Environment:** Requires `.env` in root with `OPENAI_API_KEY`.

### Key Commands

*   **Default Mode (Codex-assisted Autonomous Deployment):**
    This mode attempts to deploy the application in the specified repository, using an AI agent to automatically fix deployment errors.
    ```bash
    # Uses Codex or Gemini as configured in sds.toml
    python -m app_operator run /path/to/repo
    ```

### Coding Agent Configuration

You can specify which AI provider to use for script generation and fixing by adding an `sds.toml` file to the target repository:

```toml
[agent]
provider = "gemini"  # "gemini" or "codex"
model = "gemini-1.5-pro" # optional
```

### Architecture
The operator uses specialized agents to manage the application lifecycle:

*   **DeploymentAgent (`app_operator/agents/deployer.py`):**
    *   Generates deployment scripts (`deploy.sh`, `health_check.sh`) if missing.
    *   Deploys the application and automatically fixes errors using an AI agent.
    *   Manages the self-healing deployment loop.
*   **AppMonitor (`app_operator/agents/app_monitor.py`):**
    *   Monitors application health at regular intervals.
    *   Uses `HealthCheckTask` to execute checks and `CodingAgent` to analyze results.
*   **Tools (`tools/`):**
    *   `healthcheck.py`: Encapsulates health check execution logic.

## 2. Applications (`apps/deathstarbench/`)

**DeathStarBench** is a suite of cloud microservices benchmarks.

*   **Hotel Reservation:** A Go-based microservice application for booking hotels.
    *   **Tech Stack:** Go, gRPC, Consul, MongoDB, Memcached, Jaeger.
    *   **Ports:** Frontend at `http://localhost:5000`.
*   **Social Network:** A mixed-language social graph application.
*   **Media Microservices:** Service for streaming/processing media.

## 3. Deployment & Operations (`deploy/`)

Manual and script-based deployment logic resides here, often wrapped by the `app_operator`.

### Hotel Reservation (`deploy/deathstarbench/hotel/`)

*   **`deploy.sh`**: Main entry point.
    *   `./deploy.sh start --build`: Build and start.
    *   `./deploy.sh stop`: Shutdown.
    *   `./deploy.sh status`: Check Docker containers.
*   **`health_check.sh`**: Comprehensive system validation.
    *   Checks: Docker status, Port 5000, Database connectivity, Consul registration.

## Development Conventions

*   **Python (Agents):**
    *   Follows strict typing (`mypy`).
    *   Uses `black` for formatting.
    *   Tests via `pytest`.
*   **Microservices:**
    *   Standard `docker-compose` patterns.
    *   Go modules for Go services.
    *   Service discovery via Consul is a common pattern.

## Usage Guide for LLM agents

*   **When debugging deployment:** Check `.sds/deploy.sh` and the generated logs.
*   **When adding a new app:** Simply run the operator on the repository. The `DeploymentAgent` will attempt to generate appropriate scripts automatically.

## Notes from the developers

*   Keep this file up to date when you update the project.
*   Use `uv` and `uv run ...` for running python.
*   When code update impacts the CLI interface, update the README.
*   Remember to format the code after code edits.

### Documentation

*   Update README.md when your changes impact the user-facing behavior.

### Checking syntax correctness

* For python code, use `scripts/check_errors.sh` to check for syntax errors. Add `--fix` to automatically fix errors. For other errors, see if you can fix them yourself. Run test afterwards to make sure there's no regressions.

### Testing

*   We use pytest.
*   Check if tests pass after you've modified the codebase's behavior.
*   When introducing new features or modifying existing behavior, write in a testable way. Update tests on application semantic changes.
