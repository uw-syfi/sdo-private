# Plan: Refactoring sds_operator for Maintainability and Extensibility

This plan outlines steps to improve the readability, maintainability, and extensibility of the `sds_operator` codebase. The primary focus is on reducing code duplication between the `cli_agent` and `langgraph` runtimes and modularizing large files.

## 1. Refactor Shared Logic (DRY Principle)

**Goal:** Centralize core business logic shared across runtimes to ensure consistency and reduce duplication.

*   [ ] **Create `app_operator/services/` module:**
    *   Create a new package directory `app_operator/services/__init__.py`.
*   [ ] **Extract Script Generation:**
    *   Move `generate_scripts`, `_generate_deploy_script`, and `_generate_health_check_script` from `app_operator/cli_agent/agents/deployer.py` to `app_operator/services/script_generator.py`.
    *   Update `cli_agent` deployer to import from the new service.
    *   Update `langgraph` nodes to use the new service if applicable (or ensure they share the logic).
*   [ ] **Extract Error Context:**
    *   Move `_prepare_error_context` from `deployer.py` (and `graph.py` if duplicated) to `app_operator/services/error_analysis.py`.
    *   Refactor both runtimes to use this shared utility.

## 2. Improve Extensibility of Agent Backends

**Goal:** Allow adding new AI providers without modifying the factory code (Open-Closed Principle).

*   [x] **Implement Registry Pattern:**
    *   Update `app_operator/cli_agent/backend/base.py` to include a registry mechanism (e.g., a dictionary mapping provider names to classes).
    *   Add a `@register_provider("name")` decorator.
*   [x] **Update Factory:**
    *   Refactor `app_operator/cli_agent/backend/factory.py` to use the registry for instantiation instead of hardcoded `if/elif` blocks.
*   [x] **Update Existing Backends:**
    *   Decorate existing agent classes (`Codex`, `Gemini`, `Claude`, `Opencode`) with the new registration decorator.

## 3. Modularize `langgraph/graph.py`

**Goal:** Decompose the monolithic `graph.py` file to improve readability.

*   [ ] **Create `app_operator/langgraph/nodes/`:**
    *   Create a new package directory.
*   [ ] **Split Nodes:**
    *   Move `analyze_code` to `app_operator/langgraph/nodes/analyzer.py`.
    *   Move `generate_scripts` to `app_operator/langgraph/nodes/generator.py`.
    *   Move `deploy_attempt` and `fix_errors` to `app_operator/langgraph/nodes/deployer.py`.
    *   Move `health_check`, `monitor_health`, `monitor_analyze` to `app_operator/langgraph/nodes/monitor.py`.
*   [ ] **Update Graph Definition:**
    *   Refactor `app_operator/langgraph/graph.py` to import these node functions.

## 4. Enhance `AppOperator` Composition

**Goal:** Decouple the `AppOperator` from specific phases to allow flexible workflows.

*   [ ] **Define Phase Interface:**
    *   Create an interface/protocol for a workflow phase.
*   [ ] **Refactor `AppOperator`:**
    *   Update `app_operator/cli_agent/operator.py` to iterate through a configured list of phases instead of hardcoded method calls.

## 5. Verification

**Goal:** Ensure refactoring preserves functionality and stability.

*   [ ] **Run Unit Tests:** Execute `uv run pytest tests/unit/` to catch regressions.
*   [ ] **Run Integration Tests:** Execute `uv run pytest tests/integration/`.
*   [ ] **Add New Tests:** Create unit tests for the new `app_operator/services` modules.
