describe('Review Checklist', () => {
  describe('Security', () => {
    it('should check for arbitrary code execution without user confirmation', () => {
      // Test implementation goes here
    });

    it('should check for command injection via unsanitized inputs in shell commands', () => {
      // Test implementation goes here
    });

    it('should check for secrets or credentials committed to the repo', () => {
      // Test implementation goes here
    });

    it('should check for overly permissive file permissions', () => {
      // Test implementation goes here
    });
  });

  describe('Architecture', () => {
    it('should check module boundaries', () => {
      // Test implementation goes here
    });

    it('should check for duplicate logic across modules', () => {
      // Test implementation goes here
    });

    it('should check that abstractions are used consistently', () => {
      // Test implementation goes here
    });

    it('should check that singletons are properly reset in tests', () => {
      // Test implementation goes here
    });
  });

  describe('Code Quality', () => {
    it('should run `bash scripts/check_errors.sh` — all ruff errors must be resolved', () => {
      // Test implementation goes here
    });

    it('should check for magic numbers — constants should be named', () => {
      // Test implementation goes here
    });

    it('should check for overly complex methods (>50 lines or deep nesting)', () => {
      // Test implementation goes here
    });

    it('should check for dead code or commented-out code', () => {
      // Test implementation goes here
    });

    it('should check function return patterns — `(bool, str)` tuples vs exceptions should be consistent within a module', () => {
      // Test implementation goes here
    });
  });

  describe('Configuration', () => {
    it('should have `__post_init__` validation with `TypeError`/`ValueError` for new config fields', () => {
      // Test implementation goes here
    });

    it('should have config defaults in code that match documentation', () => {
      // Test implementation goes here
    });

    it('should not duplicate `OperatorConfig` fields in module-level constants', () => {
      // Test implementation goes here
    });

    it('should check `Config.from_dict()` recognizes new fields', () => {
      // Test implementation goes here
    });
  });

  describe('Testing', () => {
    it('should have tests for new code', () => {
      // Test implementation goes here
    });

    it('should have tests that test contracts, not internal state', () => {
      // Test implementation goes here
    });

    it('should use absolute paths in tests using `InMemoryFilesystem`', () => {
      // Test implementation goes here
    });

    it('should not have duplicate test files', () => {
      // Test implementation goes here
    });

    it('should use `tmp_path` for isolation in tests using `ScriptGeneratingAgent`', () => {
      // Test implementation goes here
    });

    it('should call `reset_loader()` in tests that configure DSPy prompt loading', () => {
      // Test implementation goes here
    });
  });

  describe('Error Handling', () => {
    it('should have custom exceptions that inherit from the hierarchy in `app_operator/exceptions.py`', () => {
      // Test implementation goes here
    });

    it('should justify broad `except Exception` catches', () => {
      // Test implementation goes here
    });

    it('should document failure conditions for agent functions returning `(bool, str)`', () => {
      // Test implementation goes here
    });
  });

  describe('Dependency Injection', () => {
    it('should accept `FileSystemInterface` for classes that touch the filesystem', () => {
      // Test implementation goes here
    });

    it('should have optional dependencies that default to null implementations', () => {
      // Test implementation goes here
    });

    it('should set `CodingAgent.recorder` per-instance', () => {
      // Test implementation goes here
    });
  });

  describe('Project Conventions', () => {
    it('should use type hints in Python code', () => {
      // Test implementation goes here
    });

    it('should be formatted with `autopep8` with `--max-line-length 120`', () => {
      // Test implementation goes here
    });

    it('should have no linting errors from `ruff`', () => {
      // Test implementation goes here
    });

    it('should not have commented-out code', () => {
      // Test implementation goes here
    });

    it('should prefer simple, direct code over unnecessary abstractions', () => {
      // Test implementation goes here
    });
  });
});