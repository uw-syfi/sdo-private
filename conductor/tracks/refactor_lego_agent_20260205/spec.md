# Specification - refactor_lego_agent_20260205

## Overview
This track aims to finalize the transition of the experimental orchestration tool from its legacy name `agentflow` to its new name `lego_agent`. This includes physical file moves, internal import updates, test consolidation, and verification of core functionality.

## Goals
- Complete removal of `agentflow` legacy code.
- Consistent naming across the codebase, tests, and documentation.
- 100% pass rate for existing tests after migration.
- Verified functionality of TUI and CLI modes for `lego_agent`.

## Success Criteria
- No files or directories named `agentflow` remain in the repository root (except for historical context in documentation if necessary).
- All tests in `tests/unit/lego_agent/` pass.
- `uv run -m lego_agent` launches the TUI successfully.
- `./sds_lego_agent --help` executes without errors.
