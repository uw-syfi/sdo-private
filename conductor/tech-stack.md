# Technology Stack

This document outlines the core technologies, frameworks, and tools used in the SDS (Self-Defining Systems) project.

## Core Language and Runtime
- **Python (>= 3.10):** The primary programming language for the operator, agents, and orchestration logic.
- **uv:** Utilized for high-performance dependency management, virtual environment handling, and tool execution.

## AI and Agent Orchestration
- **LangChain:** Provides the foundation for LLM interaction and chain construction.
- **LangGraph:** Used for building stateful, multi-actor applications with LLMs, specifically for the `langgraph` operator runtime.
- **Google ADK (Agent Development Kit):** Employed for deterministic orchestration of agent tasks, particularly in the `adk` runtime.

## Interface and User Experience
- **Click:** The framework for creating the project's command-line interface.
- **Textual:** Used to build rich, interactive Terminal User Interfaces (TUIs) for both the operator and `lego_agent`.
- **Jinja2:** The templating engine for generating dynamic agent prompts and deployment scripts.

## Utilities and Infrastructure
- **Loguru:** Provides structured and configurable logging across the application.
- **python-dotenv:** Manages environment variables and sensitive configuration (like API keys).
- **tomli:** Handles TOML configuration file parsing.

## Quality Assurance and Tooling
- **Pytest:** The primary testing framework for unit and integration tests.
- **Ruff:** Used for extremely fast Python linting and code transformation.
- **autopep8:** Ensures adherence to PEP 8 coding style.
- **Hypothesis:** Employed for property-based testing of core logic.
- **Pytest-cov:** Generates test coverage reports to ensure code quality.

## Target Application Environment
- **Go:** Primary language for many target microservices (e.g., Hotel Reservation).
- **Docker & Docker-compose:** The standard for containerization and local orchestration of the microservices being managed.
