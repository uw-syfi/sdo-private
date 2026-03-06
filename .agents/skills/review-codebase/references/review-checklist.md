# Review Checklist

Use this checklist when reviewing the SDS codebase. Not every item applies to every review — focus on what is relevant to the scope.

## Security

- [ ] Check for arbitrary code execution without user confirmation (e.g., `subprocess.run` on generated code)
- [ ] Check for command injection via unsanitized inputs in shell commands
- [ ] Check for secrets or credentials committed to the repo
- [ ] Check for overly permissive file permissions

## Architecture

- [ ] Check module boundaries: `lego_agent` should not import directly from `app_operator` internals (known issue: `engine.py` imports from `app_operator.adk.tools`, `app_operator.langgraph.llm`, `app_operator.config`)
- [ ] Check for duplicate logic across modules (e.g., `_generate_deploy_script` / `_generate_health_check_script` in `deployer.py`)
- [ ] Check that abstractions are used consistently — avoid `isinstance` checks on concrete types when an interface exists (e.g., `isinstance(self.filesystem, RealFilesystem)`)
- [ ] Check that singletons (e.g., `PromptLoader`) are properly reset in tests

## Code Quality

- [ ] Run `bash scripts/check_errors.sh` — all ruff errors must be resolved
- [ ] Check for magic numbers — constants should be named
- [ ] Check for overly complex methods (>50 lines or deep nesting)
- [ ] Check for dead code or commented-out code (should be deleted per project conventions)
- [ ] Check function return patterns — `(bool, str)` tuples vs exceptions should be consistent within a module

## Configuration

- [ ] New config fields must have `__post_init__` validation with `TypeError`/`ValueError`
- [ ] Config defaults in code must match documentation in `CLAUDE.md` / `sds.toml` examples
- [ ] Module-level constants (e.g., `AGENT_FIX_TIMEOUT_SECS`) should not duplicate `OperatorConfig` fields — prefer config values at all call sites
- [ ] Check `Config.from_dict()` recognizes new fields (no silent drops)

## Testing

- [ ] New code should have tests — check coverage for the changed files
- [ ] Tests should test contracts (return values, exceptions, side effects), not internal state
- [ ] Tests using `InMemoryFilesystem` should pass absolute paths to avoid `resolve()` touching real disk
- [ ] Check for test file duplication between `tests/unit/` root and `tests/unit/<subsystem>/` subdirectories
- [ ] Check that tests using `ScriptGeneratingAgent` use `tmp_path` for isolation
- [ ] Verify `reset_loader()` is called in tests that configure DSPy prompt loading

## Error Handling

- [ ] Custom exceptions should inherit from the hierarchy in `app_operator/exceptions.py`
- [ ] Broad `except Exception` catches should be justified — check if specific exceptions should be caught instead
- [ ] Agent functions returning `(bool, str)` should document failure conditions

## Dependency Injection

- [ ] Classes that touch the filesystem should accept `FileSystemInterface`
- [ ] Optional dependencies should default to null implementations (`NullTrajectoryRecorder`, `NullOperatorUI`)
- [ ] Check that `CodingAgent.recorder` is set per-instance, not leaked across instances via class-level attributes

## Project Conventions

- [ ] Python code uses type hints
- [ ] Formatting: `autopep8` with `--max-line-length 120`
- [ ] Linting: `ruff` with no errors
- [ ] No commented-out code — delete removed code entirely
- [ ] No unnecessary abstractions — prefer simple, direct code
