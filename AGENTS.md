# SDS (Self-Defining Systems)

**Context for LLM Agent**

SDS is an AI-native project designed to autonomously explore, validate, and evolve infrastructure by embedding agentic LLMs into the system lifecycle. It primarily consists of an **Application Operator** (an intelligent agent) and the **DeathStarBench** suite (microservices benchmarks) used as target applications.

## Project Structure

```
sds/
├── agents/          # Python-based Application Operator and tools.
│   ├── app_operator/ # Core operator logic, agent, and registry.
│   └── ...
├── apps/            # Application code (DeathStarBench suite).
│   └── deathstarbench/
│       ├── hotelReservation/ # Go-based microservices app.
│       ├── socialNetwork/    # C++/Python/Go microservices app.
│       └── ...
├── deploy/          # Deployment and operational scripts.
│   └── deathstarbench/
│       └── hotel/   # Deployment scripts for Hotel Reservation.
└── README.md        # Root project documentation.
```

## 1. Application Operator (`agents/`)

The **Application Operator** is a Python tool that autonomously deploys, monitors, and manages applications. It features "Codex" mode to self-correct deployment scripts and uses LLMs to summarize health checks.

### Setup & Usage

*   **Dependency Management:** Uses `uv` (or `pip`).
*   **Installation:**
    ```bash
    cd agents
    uv sync  # or pip install -e .
    ```
*   **Environment:** Requires `.env` in `agents/` or root with `OPENAI_API_KEY`.

### Key Commands

*   **List Applications:**
    ```bash
    python -m app_operator --list
    ```
*   **Run Operator (Deploy & Monitor):**
    ```bash
    python -m app_operator --app-name hotel
    ```
*   **Codex Mode (Autonomous Deployment):**
    ```bash
    python -m app_operator codex /path/to/repo
    ```
*   **Generate Scripts:**
    ```bash
    python -m app_operator generate-scripts /path/to/repo
    ```

### Architecture
*   **Abstract Base Class:** `Application` (in `application.py`) defines `deploy()`, `health_check()`, and `shutdown()`.
*   **Registry:** `app_registry.py` maps names (e.g., "hotel") to implementation classes.
*   **Extensibility:** New apps can be added by subclassing `Application` and registering them.

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

*   **When asked to "deploy hotel":** Prefer using the `app_operator` (`python -m app_operator --app-name hotel`) as it wraps the underlying scripts and provides AI monitoring.
*   **When debugging deployment:** Check `deploy/deathstarbench/hotel/deploy.sh` and the generated logs.
*   **When adding a new app:** You will need to implement a subclass in `agents/app_operator/apps/` and register it, pointing it to the underlying deployment scripts.
*   Keep this file up to date when you update the project.
