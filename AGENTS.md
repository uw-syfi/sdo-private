# SDS (Self-Defining Systems)

**Context for LLM Agent**

SDS is an AI-native project designed to autonomously explore, validate, and evolve infrastructure by embedding agentic LLMs into the system lifecycle. It primarily consists of an **Application Operator** (an intelligent agent) and the **DeathStarBench** suite (microservices benchmarks) used as target applications.

## Project Structure

```
sds/
├── lego_agent/            # Autonomous script generation module
│   ├── __main__.py       # Entry point for `python -m lego_agent`
│   ├── cli.py            # CLI argument parsing and mode selection
│   ├── engine.py         # Core clarification loop and orchestration engine
│   ├── io.py             # I/O abstractions (ConsoleIO, TextualIO)
│   ├── models.py         # Data models (LegoAgentResponse, LegoAgentResult)
│   ├── runtime.py        # LangGraph agent runtime and orchestration patterns
│   ├── storage.py        # Script storage management
│   ├── tui.py            # Textual-based interactive TUI implementation
│   └── prompts/          # Jinja2 prompt templates
│       └── templates/lego_agent/
│           ├── system.jinja2   # System prompt with orchestration docs
│           ├── user.jinja2     # User request template
│           └── repair.jinja2   # Error correction template
├── app_operator/         # Core operator logic
│   ├── cli_agent/        # CLI-based agent implementation
│   │   ├── agents/       # Specialized agents (deployer, monitor, code_analyzer)
│   │   └── backend/      # Coding agent CLI backends (claude, gemini, codex, opencode)
│   ├── langgraph/        # LangGraph-based implementation
│   ├── adk/              # Google ADK-based implementation
│   ├── commands/         # CLI commands (run, init_exp, viz_graph)
│   ├── prompts/          # Jinja2 prompt templates
│   ├── config.py         # Configuration dataclasses
│   ├── exceptions.py     # Custom exception hierarchy
│   ├── filesystem.py     # Filesystem abstraction layer
│   ├── logger.py         # Logging configuration
│   └── trajectory.py     # Agent interaction recording
├── apps/                 # Application code (DeathStarBench suite)
│   └── deathstarbench/
│       ├── hotelReservation/ # Go-based microservices app
│       ├── socialNetwork/    # C++/Python/Go microservices app
│       └── ...
└── README.md             # Root project documentation
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

You can specify which AI provider to use for script generation and fixing by adding an `sds.toml` file to the target repository.

The operator supports three different runtime implementations for the same high-level autonomous deployment and monitoring logic:

*   **CLI Agent (`cli_agent`):** The default implementation. It interacts with the system by calling out to specialized coding agents (like Gemini, Claude, or Codex) through their CLI interfaces. Supports all providers.
*   **LangGraph (`langgraph`):** A modular and stateful implementation built using LangGraph and LangChain. It models the deployment and monitoring process as a graph of specialized nodes and edges.
*   **ADK (`adk`):** Uses Google's Agent Development Kit with Gemini models for deterministic orchestration of agent tasks. Only supports Gemini/Vertex providers.

Example `sds.toml` configuration:

```toml
[agent]
provider = "gemini"  # Valid: "gemini", "codex", "claude", "claude-code", "opencode", "openai", "anthropic"
model = "gemini-1.5-pro" # required for langgraph and adk runtimes
# location = "us-central1" # optional: specify vertex AI location (default: us-central1)

[runtime]
impl = "cli_agent" # or "langgraph", "adk"

[operator]
interval = 30 # Health check interval in seconds (1-86400, default: 30)
monitoring_max_iters = 5 # Maximum health monitoring iterations (>0, default: 5)
deployment_max_iters = 20 # Maximum deployment attempts (>0, default: 20)
agent_fix_timeout = 1800 # Agent fix timeout in seconds (>0, default: 1800 / 30 minutes)
deploy_timeout = 900 # Deployment timeout in seconds (>0, default: 900 / 15 minutes)
agent_timeout = 300 # Agent generation timeout in seconds (>0, default: 300 / 5 minutes)
```

**Note**: Invalid configuration values will raise `ValueError` or `TypeError` with clear error messages at initialization.

### Architecture

The system is organized into two main runtime implementations, sharing core configuration and filesystem abstractions.

#### CLI Agent Architecture (`app_operator/cli_agent/`)
This implementation uses existing coding agent CLIs to manage the application lifecycle:

*   **DeploymentAgent (`app_operator/cli_agent/agents/deployer.py`):**
    *   Generates deployment scripts (`deploy.sh`, `health_check.sh`) if missing.
    *   Deploys the application and automatically fixes errors using an AI agent.
    *   Manages the self-healing deployment loop.
*   **CodeAnalyzerAgent (`app_operator/cli_agent/agents/code_analyzer.py`):**
    *   Analyzes the codebase before deployment.
    *   Generates `.sds/code_analysis.md` and `.sds/deployment_issues.md`.
    *   Identifies potential deployment issues proactively.
*   **AppMonitor (`app_operator/cli_agent/agents/app_monitor.py`):**
    *   Monitors application health at regular intervals.
    *   Uses `HealthCheckTask` to execute checks and `CodingAgent` to analyze results.
*   **Backend Providers (`libs.agent_cli/`):**
    *   Implements AI provider integrations (Claude, Gemini, Codex, Opencode).
    *   Each backend has event parsers for streaming output (`*_events.py`).
    *   Factory pattern (`factory.py`) for creating agent instances.

#### LangGraph Architecture (`app_operator/langgraph/`)
This implementation uses a stateful graph to manage the lifecycle:

*   **Graph Definition (`app_operator/langgraph/graph.py`):** Defines the nodes (deployment, monitoring, error fixing) and the transitions between them.
*   **State Management (`app_operator/langgraph/state.py`):** Maintains the state of the deployment and monitoring process throughout the graph execution.
*   **Tools (`app_operator/langgraph/tools.py`):** Provides the LLM with tools for executing shell commands, reading files, and performing health checks.
*   **LLM Integration (`app_operator/langgraph/llm.py`):** Configures LangChain LLM instances based on provider settings.
*   **Models (`app_operator/langgraph/models.py`):** Pydantic models for structured LLM outputs.
*   **Trajectory Handler (`app_operator/langgraph/trajectory_handler.py`):** Records agent interactions for the LangGraph runtime.

#### Shared Components
*   **Configuration (`app_operator/config.py`):**
    *   `AgentConfig`: AI provider configuration (codex, gemini, claude, claude-code, opencode)
    *   `OperatorConfig`: Operational parameters (intervals, max iterations)
    *   All configs validate on initialization with clear error messages
*   **Exceptions (`app_operator/exceptions.py`):**
    *   Custom exception hierarchy for clear error categorization.
*   **Filesystem (`app_operator/filesystem.py`):**
    *   Abstraction layer for filesystem operations (`RealFilesystem` vs `InMemoryFilesystem`).
*   **Trajectory (`app_operator/trajectory.py`):**
    *   Records agent interactions, prompts, responses, and tool calls.
    *   Saves structured trajectory.json files for analysis.
    *   **Sequential Call IDs:** Each agent call receives a unique sequential ID (1, 2, 3...) for tracking:
        *   Call IDs are stored in the `calls` array with start/end times and phase info
        *   Each conversation in phases (script_generation, deployment, monitoring) includes its `call_id`
        *   For Gemini CLI: metadata files (`sds_call_XXX.json`) are written to correlate sessions
        *   Gemini sessions are collected and matched to call IDs based on timing and metadata
        *   Session files are renamed to `gemini_session_call_XXX.json` for easy correlation
*   **Prompts (`app_operator/prompts/`):**
    *   Jinja2 template system for generating agent prompts.
    *   Templates organized by agent type: `deployer/`, `monitor/`, `code_analyzer/`.
    *   `PromptLoader` class for rendering templates with context.
*   **Commands (`app_operator/commands/`):**
    *   CLI command implementations: `run`, `init_exp`, `viz_graph`.

## 2. LegoAgent Module (`lego_agent/`)

The **LegoAgent** module is an autonomous script generation system that uses AI agents to create orchestrated Python scripts for complex multi-agent workflows. It features an interactive TUI, clarification loops, and supports advanced orchestration patterns.

### Key Features

*   **Interactive TUI Mode**: Textual-based rich terminal interface with real-time streaming output.
*   **Clarification Loop**: Iteratively refines requirements through AI-powered questions before generating scripts.
*   **Orchestration Patterns**: Built-in support for `fan_out`, `summarize`, and `judge_loop` patterns.
*   **Automatic Repo Detection**: Finds project root by searching upward for `.git` or `sds.toml`.
*   **Script Validation**: Validates syntax and required components before execution.
*   **Environment Setup**: Configures `PYTHONPATH` and work directory automatically.

### Architecture

#### Core Components

*   **LegoAgentEngine (`engine.py`)**:
    *   Manages the clarification loop (up to `max_clarifications` rounds).
    *   Integrates with LangGraph for agent execution.
    *   Streams thinking chunks, tool calls, and results.
    *   Validates generated Python scripts before execution.
    *   Handles response parsing and error repair.

*   **I/O Abstraction (`io.py`)**:
    *   **UserIO Protocol**: Duck-typed interface for user interaction.
    *   **ConsoleIO**: ANSI-colored console output for CLI mode.
    *   **TextualIO**: Rich Textual widgets for TUI mode with async support.
    *   Both implement: `read_prompt()`, `ask_questions()`, `render_thinking_chunk()`, `render_tool_start/end()`, etc.

*   **LegoAgentTUI (`tui.py`)**:
    *   Textual App implementation with `RichLog` widget.
    *   Interactive input field for prompts and answers.
    *   Work directory display in header.
    *   Async script execution with live stdout/stderr streaming.
    *   Uses `flexoki` theme for consistent styling.

*   **LangGraphAgent (`runtime.py`)**:
    *   Wraps LangGraph React agent for orchestration.
    *   Implements streaming event handlers for thinking and tool use.
    *   Provides `generate()` sync and `_generate_async()` async methods.
    *   Supports parallelization through `fan_out()` for multi-task execution.

*   **Orchestration Patterns (`runtime.py`)**:
    1. **fan_out()**: Execute multiple independent prompts in parallel.
    2. **summarize()**: Aggregate multiple responses into one.
    3. **judge_loop()**: Iterative refinement with evaluation.
    4. **Combined patterns**: Complex workflows combining multiple patterns.

*   **Storage & Models**:
    *   **LegoAgentStorage (`storage.py`)**: Manages script output with timestamped directories.
    *   **LegoAgentResponse/Result (`models.py`)**: Pydantic models for structured data.

*   **Prompt System (`prompts/`)**:
    *   **system.jinja2**: Comprehensive system instructions (338 lines) with:
        - Orchestration pattern documentation
        - Available runtime API (create_agent, fan_out, summarize, judge_loop)
        - Tool descriptions (read_file, write_file, list_files, find_files, search_content, run_command)
        - Pattern examples with code blocks
        - Best practices section
    *   **user.jinja2**: User request with clarification history and validation checklist.
    *   **repair.jinja2**: Error correction prompt for JSON parsing failures.

### CLI Usage

**Default TUI Mode:**
```bash
uv run -m lego_agent
```

**With Initial Prompt:**
```bash
uv run -m lego_agent --prompt "Scrape hacker news and summarize top 3 AI stories"
```

**CLI Mode (No TUI):**
```bash
uv run -m lego_agent --no-tui --prompt "Your task"
```

**Available Flags:**
- `--prompt`: Initial user prompt (interactive if omitted in TUI mode).
- `--loop-bound`: Maximum iterations for loops (default: 10).
- `--max-clarifications`: Maximum clarification rounds (default: 5).
- `--config`: Path to `sds.toml` (optional, auto-detects repo root).
- `--model`: Override agent model from configuration.
- `--output-dir`: Output directory (default: `lego_agent_runs`).
- `--work-dir`: Execution directory (default: current directory).
- `--no-run`: Generate script but don't execute it.
- `--no-tui`: Use CLI mode instead of TUI.

### Data Flow

```
User Input (TUI or CLI)
    ↓
Config loading (sds.toml)
    ↓
LegoAgentEngine.run_async()
    ├─→ Clarification Loop (rounds 0 to max_clarifications):
    │   ├─→ Render system/user prompts
    │   ├─→ Stream agent thinking (LangGraph events)
    │   ├─→ Stream tool executions
    │   └─→ Parse response (clarify/ready status)
    │
    ├─→ If status="clarify": ask_questions() → next round
    ├─→ If status="ready": validate_script() → write to storage
    │
LegoAgentStorage.write_script()
    ↓
Script Execution (in work_dir)
    ├─→ Set PYTHONPATH to repo root
    ├─→ Stream stdout/stderr with visual formatting
    └─→ Return exit code
```

### Script Validation

Generated scripts must satisfy:
1. Define `MAX_ITERATIONS = {loop_bound}` constant.
2. Import from `lego_agent.runtime` or related modules.
3. Include `if __name__ == "__main__":` block.
4. Be valid Python with proper syntax.

### Output Structure

Scripts are saved in `lego_agent_runs/<timestamp>/lego_agent.py` with:
- Timestamped directory for each run.
- Standalone execution (no external dependencies except `lego_agent.runtime`).
- `MAX_ITERATIONS` constant for loop bounding.
- Orchestration tools: `fan_out()`, `summarize()`, `judge_loop()`.

## 3. Applications (`apps/deathstarbench/`)

**DeathStarBench** is a suite of cloud microservices benchmarks used as target applications for the SDS operator.

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

*   **When debugging deployment:** Check `.sds/deploy.sh` and the generated logs in `.sds/logs/`.
*   **When debugging lego_agent scripts:** Check `lego_agent_runs/<timestamp>/lego_agent.py` and examine the clarification history.
*   **When adding a new app:** Simply run the operator on the repository. The `DeploymentAgent` will attempt to generate appropriate scripts automatically.
*   **When adding lego_agent features:** Test both TUI and CLI modes. Verify the generated scripts are syntactically valid and include required components.
*   **When adding a new feature:** Think of what new behavior(s) are being introduced, and how you would test them. Test public behavior, not internal implementation details.
*   **When fixing bugs:** Think of how to write test(s) to reproduce the issue first and then use them to verify your fix. The test should be part of your fix. If you cannot do so, you must defend your decision.

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

Always do the following after you're done with your code edits:

*   **Formatting:** Run `@scripts/format_code.sh` on python code edits.
*   **Linting:** Run `@scripts/check_errors.sh` on python code edits. Add `--fix` to automatically fix errors.

### Testing

#### Test Organization

*   **Unit tests** (`tests/unit/`): Fast, isolated tests for individual components
*   **Integration tests** (`tests/integration/`): Test component interactions and real behavior
*   Test organization by component:
    *   `tests/unit/config/`: Configuration validation tests
    *   `tests/unit/agents/`: Agent-specific tests (deployment, monitoring)
    *   `tests/unit/lego_agent/`: LegoAgent module tests (engine, runtime, CLI, prompts)
    *   `tests/integration/`: End-to-end scenarios, signal handling, concurrency

#### Running Tests

*   Run all tests: `uv run pytest tests/`
*   Run specific category: `uv run pytest tests/unit/` or `uv run pytest tests/integration/`
*   Run with coverage: `uv run pytest tests/ --cov=app_operator`
*   Check if tests pass after you've modified the codebase's behavior.
*   Don't run the sds_operator directly to test; it is a long-running process that will not terminate.

#### Writing Tests - Core Principles

**1. Test Contracts, Not Implementation Details**

Tests should verify **externally visible behavior** (the contract with callers), not internal mechanics.

```python
# ✅ GOOD: Test observable outcomes (return values, file creation, state changes)
def test_deployment_succeeds_after_retry():
    result = deployer.run(max_attempts=3)
    assert result is True
    assert log_file.exists()
    assert "deployment successful" in log_file.read_text()

# ❌ AVOID: Test internal method calls or private details
def test_deployment():
    mock_deployer._fix_with_agent.assert_called_once()  # Brittle! Couples to implementation
    assert deployer._retry_count == 2  # Private detail, not part of contract
```

**Why:** Tests coupled to implementation break when refactoring code structure, even if behavior remains identical. Testing the contract ensures tests remain valid as long as the external API is unchanged.

**2. Test Properties and Invariants, Not Execution Paths**

Focus on **what properties must hold**, not the specific code path taken to achieve them.

```python
# ✅ GOOD: Test desired properties
def test_deployment_creates_required_files():
    deployer.run()
    assert (repo / ".sds" / "deploy.sh").exists()
    assert (repo / ".sds" / "health_check.sh").exists()
    # Property: all required files exist, regardless of how they were created

# ❌ AVOID: Test specific execution sequence
def test_deployment():
    assert deployer._generate_deploy_script() is called first
    assert deployer._generate_health_check() is called second
    # Too specific - if we change order or combine generation, test breaks
```

**Why:** Testing properties makes tests resilient to refactoring. Code can be reorganized, optimized, or rewritten as long as it maintains required properties.

**3. Prefer Test Doubles Over Mocks for Maintainability**

Use **simple test double classes** instead of mock frameworks for clearer, more maintainable tests. Implement features in a test-double-friendly way.

```python
# ✅ GOOD: Test doubles with real behavior
class StubAgent:
    def generate(self, prompt):
        return "fixed script content"

class TrackingAgent:
    def __init__(self):
        self.calls = []
    def generate(self, prompt):
        self.calls.append(prompt)
        return "response"

def test_with_doubles():
    agent = TrackingAgent()
    deployer.run(agent)
    assert len(agent.calls) == 1  # Clear, readable verification

# ❌ AVOID: Complex mocks with assertions
def test_with_mocks():
    mock_agent = Mock()
    mock_agent.generate.return_value = "response"
    deployer.run(mock_agent)
    mock_agent.generate.assert_called_once_with(ANY, timeout=ANY)  # Fragile
```

**Why:** Test doubles are explicit, easier to understand, and don't break when argument order changes or new parameters are added. Mocks should be reserved for external dependencies (subprocess, file I/O).

**4. Write Human-Readable, Self-Documenting Tests**

Test names and structure should clearly communicate **what is being tested and why**.

```python
# ✅ GOOD: Clear name and focused test
def test_deployment_timeout_returns_partial_output():
    """When deployment times out, partial stdout/stderr should be captured."""
    result = deployer.run(timeout=1)
    assert result.exit_code == -1
    assert "Starting deployment" in result.stdout
    assert "timed out" in result.stderr

# ❌ AVOID: Vague names and multiple unrelated assertions
def test_deployment():
    assert deployer.run() is not None
    assert len(deployer.logs) > 0
    assert deployer.config.timeout == 30
    # What is this actually testing?
```

**Why:** Tests serve as living documentation. Clear test names and focused assertions make it obvious what failed and why when tests break.

**5. Test Edge Cases and Boundary Conditions Thoroughly**

Consider **boundary values, error conditions, special inputs, and resource constraints**.

```python
# ✅ GOOD: Comprehensive edge case coverage
def test_interval_boundary_values():
    assert OperatorConfig(interval=1).interval == 1  # Minimum
    assert OperatorConfig(interval=86400).interval == 86400  # Maximum
    with pytest.raises(ValueError):
        OperatorConfig(interval=0)  # Below minimum
    with pytest.raises(ValueError):
        OperatorConfig(interval=86401)  # Above maximum

def test_unicode_filenames():
    fs.write_text(Path("файл_测试_🎉.txt"), "content")
    assert fs.exists(Path("файл_测试_🎉.txt"))

def test_filesystem_permission_denied():
    fs.simulate_permission_error(path)
    with pytest.raises(FileSystemError):
        fs.write_text(path, "content")
```

**Why:** Edge cases are where bugs hide. Thorough edge case testing catches issues before production.

**6. Keep Tests Fast Without Compromising Quality**

Use **in-memory alternatives and test isolation** to maintain speed.

```python
# ✅ GOOD: In-memory filesystem for speed
def test_file_operations():
    fs = InMemoryFilesystem()  # No disk I/O
    fs.write_text(path, "content")
    assert fs.read_text(path) == "content"

# ✅ GOOD: Test doubles for external dependencies
def test_deployment():
    agent = StubAgent()  # No API calls
    deployer = DeploymentAgent(repo, agent)
    result = deployer.run(max_attempts=1)

# ❌ AVOID: Slow tests with real I/O or network
def test_deployment():
    deployer.run()  # Writes to disk, slow
    time.sleep(5)  # Waiting for external service
```

**Why:** Fast tests enable frequent test runs during development. Slow tests discourage running the full test suite, leading to bugs slipping through.

**7. Make Tests Robust to Code Changes**

Tests should **survive refactoring** as long as behavior is preserved.

```python
# ✅ GOOD: Test public API only
def test_deployment_success():
    result = deployer.deploy(repo_path)
    assert result.success is True
    assert result.exit_code == 0

# ❌ AVOID: Test internal structure
def test_deployment():
    assert isinstance(deployer._runner, ProcessRunner)  # Internal detail
    assert deployer._max_retries == 3  # Private attribute
    deployer._internal_helper()  # Private method
```

**Why:** Tests that depend on internal structure break when refactoring, creating maintenance burden and discouraging code improvements.

**8. Test One Concept Per Test**

Each test should verify **one specific behavior or property**.

```python
# ✅ GOOD: Focused, single-purpose tests
def test_deployment_creates_log_file():
    deployer.run()
    assert log_file.exists()

def test_deployment_captures_stdout():
    result = deployer.run()
    assert "deployment started" in result.stdout

def test_deployment_handles_timeout():
    result = deployer.run(timeout=1)
    assert result.timed_out is True

# ❌ AVOID: Testing multiple unrelated behaviors
def test_deployment():
    deployer.run()
    assert log_file.exists()  # File creation
    assert "started" in result.stdout  # Output capture
    assert result.timed_out is False  # Timeout handling
    assert agent.calls > 0  # Agent interaction
    # If this fails, which behavior broke?
```

**Why:** When a focused test fails, it immediately tells you what broke. When a multi-purpose test fails, you need to debug to find which assertion failed and why.

#### Project-Specific Testing Patterns

**Available Test Fixtures** (`tests/conftest.py`):
*   `test_filesystem`: In-memory filesystem for fast, isolated tests
*   `stub_agent`: Minimal agent that returns simple responses
*   `error_agent`: Agent that always raises errors
*   `timeout_agent`: Agent that simulates timeouts
*   `tracking_agent`: Agent that tracks all calls for verification
*   `configurable_agent`: Agent with configurable responses
*   `repo_with_scripts`: Temp repository with pre-generated working scripts

**Test Agent Doubles** (`tests/fixtures/agents.py`):

Prefer test doubles over mocks for agent interactions:
```python
from tests.fixtures.agents import StubAgent, TrackingAgent, ErrorAgent

def test_with_stub():
    agent = StubAgent()  # Simple, predictable behavior
    result = deployer.run(agent)
    assert result is True

def test_interaction_tracking():
    agent = TrackingAgent()  # Records all calls
    deployer.run(agent)
    assert agent.generation_count == 1
    assert "deploy.sh" in agent.calls[0]["prompt"]
```

**Filesystem Abstraction**:

For testing filesystem operations without disk I/O:
```python
from app_operator.filesystem import InMemoryFilesystem

def test_filesystem():
    fs = InMemoryFilesystem()
    fs.write_text(path, "content")
    assert fs.read_text(path) == "content"

def test_permission_error():
    fs = InMemoryFilesystem()
    fs.simulate_permission_error(path)
    with pytest.raises(FileSystemError):
        fs.write_text(path, "content")
```

**Configuration Validation**:

All configuration dataclasses validate in `__post_init__`:
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

**Environment Independence**:

Tests must not assume external binaries are installed:
*   Mock `shutil.which` and `subprocess.run` to simulate binary presence/absence
*   Use test doubles for agents instead of calling real CLIs
*   See `tests/unit/test_agent_factory.py` for mocking examples

#### Characteristics of High-Quality Tests

Good tests exhibit these qualities:

1. **Robust**: Tests survive refactoring and code changes as long as behavior is preserved
   - Test contracts (public API), not implementation details
   - Avoid coupling to internal structure, private methods, or execution order
   - Use test doubles that don't break when signatures change

2. **Human-Readable**: Tests serve as executable documentation
   - Clear, descriptive test names that explain what and why
   - One concept per test for easy debugging
   - Self-documenting assertions that show expected behavior

3. **Fast**: Tests run quickly to encourage frequent execution
   - Use in-memory alternatives (InMemoryFilesystem) instead of disk I/O
   - Use test doubles instead of calling external services or CLIs
   - Isolate tests to avoid setup/teardown overhead

4. **Thorough**: Tests cover the full behavior space
   - Happy path (expected behavior)
   - Error conditions (permission denied, timeouts, invalid input)
   - Boundary values (0, 1, min, max)
   - Edge cases (Unicode, special characters, resource limits, concurrency)

5. **Focused**: Each test verifies one specific property or behavior
   - Single assertion or closely related assertions
   - Clear failure messages that indicate what broke
   - Avoid testing multiple unrelated concepts in one test

6. **Maintainable**: Tests are easy to understand and update
   - Prefer test doubles over complex mocks
   - Use shared fixtures for common setup
   - Avoid duplication across tests

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
