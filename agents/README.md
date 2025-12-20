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

### List Available Applications

```bash
cd agents
uv run python -m app_operator --list
```

### Deploy and Monitor an Application

```bash
cd agents
uv run python -m app_operator --app-name hotel
```

This will:
1. Deploy the application
2. Run health checks every 30 seconds
3. Print an LLM health summary on every check
4. Stop gracefully on Ctrl+C

### LLM Configuration

Health summaries use `langchain-openai` by default. Environment variables are automatically loaded from a `.env` file in the project root (if present). You can configure via environment variables:

**Option 1: Using a `.env` file (recommended)**

Create a `.env` file in the `agents/` directory:

```bash
OPENAI_API_KEY=your-api-key-here
APP_OPERATOR_LLM_MODEL=gpt-4o-mini  # optional, defaults to gpt-4o-mini
```

**Option 2: Using environment variables**

```bash
export OPENAI_API_KEY="..."
export APP_OPERATOR_LLM_MODEL="gpt-4o-mini"  # optional
```

The operator automatically loads environment variables from `.env` files using `python-dotenv`, so you don't need to manually export them if you use a `.env` file.

### Custom Health Check Interval

```bash
cd agents
uv run python -m app_operator --app-name hotel --interval 30
```

### Stop the Application

Press `Ctrl+C` to trigger graceful shutdown. The operator will:
1. Stop health check monitoring
2. Execute application shutdown
3. Clean up resources
4. Exit

## Architecture

### Abstract Interface Design

The operator uses an abstract `Application` base class that defines three core operations:

```python
class Application(ABC):
    @abstractmethod
    def deploy(self) -> DeploymentResult:
        """Deploy the application."""
        pass
    
    @abstractmethod
    def health_check(self) -> HealthCheckResult:
        """Check application health."""
        pass
    
    @abstractmethod
    def shutdown(self) -> DeploymentResult:
        """Gracefully shutdown the application."""
        pass
```

This abstraction means:
- **No assumptions** about deployment methods (scripts, APIs, containers, etc.)
- Each application chooses its own implementation
- Easy to test with mock implementations
- Clean separation of concerns

### Directory Structure

```
agents/
├── app_operator/
│   ├── __init__.py              # Package marker
│   ├── __main__.py              # CLI entry point
│   ├── operator.py              # Core operator logic
│   ├── application.py           # Abstract Application base class
│   ├── app_registry.py          # Application registry
│   └── apps/
│       ├── __init__.py
│       └── hotel.py             # Hotel application implementation
├── pyproject.toml               # uv project config
└── README.md                    # This file
```

### Components

**Application** - Abstract base class defining the interface for all applications

**ApplicationRegistry** - Maintains a registry of available applications and provides lookup

**ApplicationOperator** - Manages deployment, health monitoring, and shutdown

**HotelApplication** - Concrete implementation for the hotel reservation app

## Adding New Applications

Adding a new application is straightforward and requires no changes to the operator core.

### Step 1: Create Application Class

Create a new file `agents/app_operator/apps/your_app.py`:

```python
from app_operator.application import Application, DeploymentResult, HealthCheckResult

class YourApplication(Application):
    """Your application implementation."""
    
    @property
    def name(self) -> str:
        return "your-app"
    
    @property
    def description(self) -> str:
        return "Your Application Description"
    
    def deploy(self) -> DeploymentResult:
        # Implement your deployment logic
        # Could use: bash scripts, Python code, API calls, 
        # Kubernetes, Docker Compose, etc.
        pass
    
    def health_check(self) -> HealthCheckResult:
        # Implement your health check logic
        # Could use: bash scripts, HTTP endpoints,
        # database queries, etc.
        pass
    
    def shutdown(self) -> DeploymentResult:
        # Implement your shutdown logic
        pass
```

### Step 2: Register the Application

Edit `agents/app_operator/app_registry.py`:

```python
from app_operator.apps.hotel import HotelApplication
from app_operator.apps.your_app import YourApplication  # Add import

class ApplicationRegistry:
    def __init__(self):
        self._apps: dict[str, Type[Application]] = {
            "hotel": HotelApplication,
            "your-app": YourApplication,  # Add this line
        }
```

### Step 3: Run

```bash
cd agents
uv run python -m app_operator --app-name your-app
```

**That's it!** No changes needed to operator logic, CLI, or any other code.

## Example: Different Implementation Approaches

The abstract interface allows different applications to use different implementation strategies:

### Script-Based (Hotel Application)

```python
def deploy(self) -> DeploymentResult:
    result = subprocess.run(["./deploy.sh", "start"], ...)
    return DeploymentResult(success=result.returncode == 0, ...)
```

### HTTP API-Based

```python
def health_check(self) -> HealthCheckResult:
    response = requests.get("http://localhost:8080/health")
    return HealthCheckResult(
        healthy=response.status_code == 200,
        message="Service responding"
    )
```

### Python Function-Based

```python
def deploy(self) -> DeploymentResult:
    try:
        start_services()
        initialize_database()
        return DeploymentResult(success=True, message="Deployed")
    except Exception as e:
        return DeploymentResult(success=False, message=str(e))
```

### Container-Based

```python
def deploy(self) -> DeploymentResult:
    client = docker.from_env()
    container = client.containers.run("myapp:latest", detach=True)
    return DeploymentResult(success=True, message=f"Started {container.id}")
```

## Benefits

1. **Abstraction** - Operator works with any Application, regardless of implementation
2. **Flexibility** - Different apps can use scripts, APIs, Python code, containers, etc.
3. **Testability** - Easy to create mock applications for testing
4. **Extensibility** - Add new apps without touching core operator code
5. **Type Safety** - Full type hints for better IDE support
6. **LLM Summaries** - Health checks are summarized concisely by an LLM for operator-friendly logs

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

## Troubleshooting

### Application deployment fails

Check that all deployment scripts/resources exist and are executable:

```bash
ls -la deploy/deathstarbench/hotel/deploy.sh
ls -la deploy/deathstarbench/hotel/health_check.sh
```

### Health checks always fail

Run the health check script manually to see the error:

```bash
cd deploy/deathstarbench/hotel
./health_check.sh
```

### Permission denied errors

Make scripts executable:

```bash
chmod +x deploy/deathstarbench/hotel/deploy.sh
chmod +x deploy/deathstarbench/hotel/health_check.sh
```

## License

Part of the SDS project.
