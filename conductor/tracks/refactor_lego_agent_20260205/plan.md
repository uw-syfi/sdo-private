# Implementation Plan - refactor_lego_agent_20260205

## Phase 1: Project Cleanup and Reorganization
- [ ] Task: Remove residual `agentflow` directory and files.
- [ ] Task: Update all internal imports from `agentflow` to `lego_agent`.
- [ ] Task: Consolidate tests from `tests/unit/agentflow/` to `tests/unit/lego_agent/`.
- [ ] Task: Conductor - User Manual Verification 'Project Cleanup and Reorganization' (Protocol in workflow.md)

## Phase 2: Core Logic Stabilization
- [ ] Task: Verify `lego_agent` runtime and orchestration patterns (`fan_out`, `summarize`, `judge_loop`).
- [ ] Task: Ensure TUI and CLI modes work correctly with the new structure.
- [ ] Task: Fix any broken tests resulting from the rename.
- [ ] Task: Conductor - User Manual Verification 'Core Logic Stabilization' (Protocol in workflow.md)

## Phase 3: Final Validation
- [ ] Task: Run full test suite with coverage report.
- [ ] Task: Verify all documentation (README, AGENTS.md) reflects the `lego_agent` name.
- [ ] Task: Conductor - User Manual Verification 'Final Validation' (Protocol in workflow.md)
