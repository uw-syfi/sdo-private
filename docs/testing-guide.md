# Testing Guide

Comprehensive guide for writing and running tests in the SDS project.

## Test Organization

- **Unit tests** (`tests/unit/`): Fast, isolated tests for individual components
- **Integration tests** (`tests/integration/`): Test component interactions and real behavior
- Test organization by component:
  - `tests/unit/config/`: Configuration validation tests
  - `tests/unit/agents/`: Agent-specific tests (deployment, monitoring)
  - `tests/unit/fault_injection/`: Fault injection tests (models, config, compose faults, registry, reporter, injector, data loader)
  - `tests/integration/`: End-to-end scenarios, signal handling, concurrency

## Running Tests

- Run all tests (backend + frontend): `scripts/run_tests.sh`
- Run python tests: `uv run pytest tests/`
- Run specific category: `uv run pytest tests/unit/` or `uv run pytest tests/integration/`
- Run with coverage: `uv run pytest tests/ --cov=app_operator`
- Check if tests pass after you've modified the codebase's behavior.
- Don't run the sds_operator directly to test; it is a long-running process that will not terminate.

## Core Testing Principles

### 1. Test Contracts, Not Implementation Details

Tests should verify **externally visible behavior** (the contract with callers), not internal mechanics.

```python
# ✅ GOOD: Test observable outcomes (return values, file creation, state changes)
def test_deployment_succeeds_after_retry():
    result = deployer.run(max_attempts=3)
    assert result is True
    assert log_file.exists()
    assert "deployment successful" in log_file.read_text()
```

### 2. Prefer Test Doubles Over Mocks for Maintainability

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

### 4. Write Human-Readable, Self-Documenting Tests

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

### 5. Test Edge Cases and Boundary Conditions Thoroughly

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

### 6. Keep Tests Fast Without Compromising Quality

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

### 7. Make Tests Robust to Code Changes

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

### 8. Test One Concept Per Test

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

## Project-Specific Testing Patterns

### Available Test Fixtures (`tests/conftest.py`)

- `test_filesystem`: In-memory filesystem for fast, isolated tests
- `stub_agent`: Minimal agent that returns simple responses
- `error_agent`: Agent that always raises errors
- `timeout_agent`: Agent that simulates timeouts
- `tracking_agent`: Agent that tracks all calls for verification
- `configurable_agent`: Agent with configurable responses
- `repo_with_scripts`: Temp repository with pre-generated working scripts

### Test Agent Doubles (`tests/fixtures/agents.py`)

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

### Filesystem Abstraction

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

### Configuration Validation

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

### Environment Independence

Tests must not assume external binaries are installed:
- Mock `shutil.which` and `subprocess.run` to simulate binary presence/absence
- Use test doubles for agents instead of calling real CLIs
- See `tests/unit/test_agent_factory.py` for mocking examples

## Characteristics of High-Quality Tests

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

## Test Coverage Guidelines

When introducing new features or modifying existing behavior:
- Write tests for the **happy path** (expected behavior)
- Write tests for **error conditions** (permission denied, timeouts, invalid input)
- Write tests for **boundary values** (0, 1, max values)
- Write tests for **edge cases** (special characters, long paths, concurrent operations)
- Update tests on application semantic changes

## What NOT to Test

- Don't test private implementation details (methods starting with `_`)
- Don't test third-party library behavior
- Don't write tests that duplicate other tests
- Don't test obvious getters/setters without logic

## Quick Reference

**When adding a new feature:** Think of what new behavior(s) are being introduced, and how you would test them. Test public behavior, not internal implementation details.

**When fixing bugs:** Think of how to write test(s) to reproduce the issue first and then use them to verify your fix. The test should be part of your fix. If you cannot do so, you must defend your decision.
