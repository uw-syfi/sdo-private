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
├── tools/           # Shared tools/utilities (e.g., healthcheck).
└── README.md        # Root project documentation.
```

## 1. Application Operator (`app_operator/`)

The **Application Operator** is a Python tool that autonomously deploys, monitors, and manages applications. It uses AI agents (Codex, Gemini, or Claude) to self-correct deployment scripts and summarize health checks.

### Setup & Usage

*   **Dependency Management:** Uses `uv` (or `pip`).
*   **Installation:**
    ```bash
    uv sync  # or pip install -e .
    ```
*   **Environment:** Requires `.env` in root with `OPENAI_API_KEY`.

### Key Commands

*   **Default Mode (AI-assisted Autonomous Deployment):**
    This mode attempts to deploy the application in the specified repository, using an AI agent to automatically fix deployment errors.
    ```bash
    # Uses Codex, Gemini, or Claude as configured in sds.toml
    ./sds_operator /path/to/repo
    ```

### Coding Agent Configuration

You can specify which AI provider to use for script generation and fixing by adding an `sds.toml` file to the target repository:

```toml
[agent]
provider = "gemini"  # Valid: "gemini", "codex", "claude", "claude-code", "opencode"
model = "gemini-1.5-pro" # optional

[operator]
interval = 30 # Health check interval in seconds (1-86400, default: 30)
monitoring_max_iters = 5 # Maximum health monitoring iterations (>0, default: 5)
deployment_max_iters = 5 # Maximum deployment attempts (>0, default: 5)
```

**Note**: Invalid configuration values will raise `ValueError` or `TypeError` with clear error messages at initialization.

### Architecture
The operator uses specialized agents to manage the application lifecycle:

*   **DeploymentAgent (`app_operator/agents/deployer.py`):**
    *   Generates deployment scripts (`deploy.sh`, `health_check.sh`) if missing.
    *   Deploys the application and automatically fixes errors using an AI agent.
    *   Manages the self-healing deployment loop.
*   **AppMonitor (`app_operator/agents/app_monitor.py`):**
    *   Monitors application health at regular intervals.
    *   Uses `HealthCheckTask` to execute checks and `CodingAgent` to analyze results.
*   **Configuration (`app_operator/config.py`):**
    *   `AgentConfig`: AI provider configuration (codex, gemini, claude, claude-code, opencode)
    *   `OperatorConfig`: Operational parameters (intervals, max iterations)
    *   All configs validate on initialization with clear error messages
*   **Exceptions (`app_operator/exceptions.py`):**
    *   Custom exception hierarchy for clear error categorization
    *   Exceptions include context (exit codes, attempt numbers, timeout flags)
*   **Filesystem (`app_operator/filesystem.py`):**
    *   Abstraction layer for filesystem operations
    *   `RealFilesystem`: Production implementation
    *   `InMemoryFilesystem`: Testing implementation (fast, isolated)
*   **Tools (`tools/`):**
    *   `healthcheck.py`: Encapsulates health check execution logic.

## 2. Applications (`apps/deathstarbench/`)

**DeathStarBench** is a suite of cloud microservices benchmarks.

*   **Hotel Reservation:** A Go-based microservice application for booking hotels.
    *   **Tech Stack:** Go, gRPC, Consul, MongoDB, Memcached, Jaeger.
    *   **Ports:** Frontend at `http://localhost:5000`.
*   **Social Network:** A mixed-language social graph application.
*   **Media Microservices:** Service for streaming/processing media.

## Development Conventions

*   **Python (Agents):**
    *   Uses type hints.
    *   Uses `autopep8` for formatting.
    *   Uses `ruff` for static analysis.
    *   Tests via `pytest`.
    *   Dataclasses use `__post_init__` for validation (see `app_operator/config.py`).
    *   Custom exception hierarchy in `app_operator/exceptions.py` for clear error handling.
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
*   Remember to format the code after code edits using `@scripts/format_code.sh`.

### Documentation

*   Update README.md when your changes impact the user-facing behavior.

### Code hygiene

* When removing code, do not comment it out, just remove it.

### Code Validation

*   **Formatting:** Run `@scripts/format_code.sh` on python code edits.
*   **Linting:** Run `@scripts/check_errors.sh` on python code edits. Add `--fix` to automatically fix errors.

### Testing

#### Test Organization

*   **Unit tests** (`tests/unit/`): Fast, isolated tests for individual components
*   **Integration tests** (`tests/integration/`): Test component interactions and real behavior
*   Test organization by component:
    *   `tests/unit/config/`: Configuration validation tests
    *   `tests/unit/agents/`: Agent-specific tests (deployment, monitoring)
    *   `tests/integration/`: End-to-end scenarios, signal handling, concurrency

#### Running Tests

*   Run all tests: `uv run pytest tests/`
*   Run specific category: `uv run pytest tests/unit/` or `uv run pytest tests/integration/`
*   Run with coverage: `uv run pytest tests/ --cov=app_operator`
*   Check if tests pass after you've modified the codebase's behavior.
*   Don't run the sds_operator directly to test; it is a long-running process that will not terminate.

#### Writing Tests - Best Practices

**1. Test Behavior, Not Implementation**
```python
# ✅ GOOD: Test observable outcomes
def test_deployment_succeeds_after_retry(repo_with_scripts):
    operator = AppOperator(str(repo), agent=agent, max_deployment_attempts=3)
    exit_code = operator.run()
    assert exit_code == 0
    assert (repo / ".sds" / "logs" / "deploy_attempt_2.log").exists()

# ❌ AVOID: Test internal method calls (brittle)
def test_deployment():
    mock_deployer._fix_with_agent.assert_called_once()  # Fragile!
```

**2. Use Test Fixtures**

Available fixtures in `tests/conftest.py`:
*   `test_filesystem`: In-memory filesystem for fast, isolated tests
*   `stub_agent`: Minimal agent that returns simple responses
*   `error_agent`: Agent that always raises errors
*   `timeout_agent`: Agent that simulates timeouts
*   `tracking_agent`: Agent that tracks all calls for verification
*   `configurable_agent`: Agent with configurable responses
*   `repo_with_scripts`: Temp repository with pre-generated working scripts

**3. Test Agent Doubles**

Use test doubles from `tests/fixtures/agents.py` instead of mocking:
```python
from tests.fixtures.agents import StubAgent, TrackingAgent, ErrorAgent

def test_deployment_with_agent(tmp_path):
    agent = StubAgent()  # Simple, predictable behavior
    deployer = DeploymentAgent(tmp_path, agent)
    result = deployer.run(max_attempts=1)
    assert result is True

def test_agent_interaction(tmp_path):
    agent = TrackingAgent()  # Tracks calls for verification
    deployer = DeploymentAgent(tmp_path, agent)
    deployer.run(max_attempts=2)
    assert agent.generation_count == 1
```

**4. Filesystem Abstraction**

For testing filesystem operations without disk I/O:
```python
from app_operator.filesystem import InMemoryFilesystem

def test_with_memory_filesystem():
    fs = InMemoryFilesystem()
    fs.simulate_permission_error(path)  # Test error handling
    # ... test code that uses filesystem
```

**5. Configuration Validation**

All configuration dataclasses validate in `__post_init__`:
*   `AgentConfig`: Validates provider (must be in VALID_PROVIDERS), model type
*   `OperatorConfig`: Validates interval (1-86400s), max_iters (positive integers)
*   Invalid configs raise `TypeError` or `ValueError` with clear messages

When adding new config fields:
```python
@dataclass
class MyConfig:
    field: int = 10

    def __post_init__(self):
        if not isinstance(self.field, int):
            raise TypeError(f"field must be int, got {type(self.field).__name__}")
        if self.field <= 0:
            raise ValueError(f"field must be positive, got {self.field}")
```

**6. Exception Handling**

Use custom exceptions from `app_operator/exceptions.py`:
*   `ConfigurationError`: Configuration issues
*   `DeploymentError`: Deployment failures (includes exit_code, attempt)
*   `FileSystemError`: Filesystem operation failures
*   `ProcessError`: Process execution errors (includes timeout flag)
*   `AgentError`: Agent-related failures

Example:
```python
from app_operator.exceptions import DeploymentError

if result.exit_code != 0:
    raise DeploymentError(
        "Deployment failed",
        exit_code=result.exit_code,
        attempt=current_attempt
    )
```

#### Test Coverage Guidelines

When introducing new features or modifying existing behavior:
*   Write tests for the **happy path** (expected behavior)
*   Write tests for **error conditions** (permission denied, timeouts, invalid input)
*   Write tests for **boundary values** (0, 1, max values)
*   Write tests for **edge cases** (special characters, long paths, concurrent operations)
*   Update tests on application semantic changes

#### What NOT to Test

*   Don't test private implementation details (methods starting with `_`)
*   Don't test third-party library behavior
*   Don't write tests that duplicate other tests
*   Don't test obvious getters/setters without logic
