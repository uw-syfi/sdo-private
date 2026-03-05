"""Edge case tests for deployer prompt generation.

Tests for prepare_error_context and create_fix_prompt functions covering:
- Extremely long error messages
- Special characters in paths
- Missing optional parameters
- Edge cases in template rendering
"""

from pathlib import Path

from app_operator.prompts.deployer import (
    create_fix_prompt,
    create_generate_script_prompt,
    prepare_error_context,
)


class TestPrepareErrorContext:
    """Test prepare_error_context function with edge cases."""

    def test_basic_deployment_error(self):
        """Test basic deployment error context."""
        deploy_result = {"exit_code": 1, "success": False}
        health_result = None

        context = prepare_error_context(deploy_result, health_result)

        assert "Deployment Script Result" in context
        assert "Exit Code: 1" in context
        assert "Status: FAILED" in context

    def test_deployment_success_health_failure(self):
        """Test context when deployment succeeds but health check fails."""
        deploy_result = {"exit_code": 0, "success": True}
        health_result = {"exit_code": 1, "success": False}

        context = prepare_error_context(deploy_result, health_result)

        assert "Deployment Script Result" in context
        assert "Status: SUCCESS" in context
        assert "Health Check Result" in context
        assert "Status: FAILED" in context

    def test_extremely_long_error_messages(self):
        """Test handling of extremely long error messages.

        Even though current implementation doesn't include stdout/stderr,
        we verify it doesn't crash with very long outputs.
        """
        deploy_result = {
            "exit_code": 1,
            "success": False,
            "stdout": "a" * 100000,  # 100KB
            "stderr": "b" * 100000,
        }
        health_result = {
            "exit_code": 1,
            "success": False,
            "stdout": "c" * 100000,
            "stderr": "d" * 100000,
        }

        # Should not crash
        context = prepare_error_context(deploy_result, health_result)

        # Should still produce valid context
        assert isinstance(context, str)
        assert len(context) > 0
        assert "Deployment Script Result" in context

    def test_with_log_file_paths(self):
        """Test context includes log file paths when provided."""
        deploy_result = {"exit_code": 1, "success": False}
        health_result = None
        log_path = Path("/repo/.sds/logs/deploy.log")
        health_log_path = Path("/repo/.sds/logs/health.log")

        context = prepare_error_context(deploy_result, health_result, log_path, health_log_path)

        assert str(log_path) in context
        assert str(health_log_path) in context

    def test_special_characters_in_log_paths(self):
        """Test log paths with special characters."""
        deploy_result = {"exit_code": 1, "success": False}
        health_result = None
        # Path with spaces, unicode, and special chars
        log_path = Path("/repo/logs/deploy 日本語 & test.log")
        health_log_path = Path("/repo/logs/health (1).log")

        context = prepare_error_context(deploy_result, health_result, log_path, health_log_path)

        assert str(log_path) in context
        assert str(health_log_path) in context

    def test_none_log_paths(self):
        """Test with None log paths (optional parameters)."""
        deploy_result = {"exit_code": 1, "success": False}
        health_result = {"exit_code": 1, "success": False}

        # Should work with None log paths
        context = prepare_error_context(deploy_result, health_result, None, None)

        assert isinstance(context, str)
        assert "Deployment Script Result" in context
        # Should not mention log paths
        assert "available at:" not in context.lower()

    def test_exit_code_zero_success_true(self):
        """Test with successful exit codes."""
        deploy_result = {"exit_code": 0, "success": True}
        health_result = {"exit_code": 0, "success": True}

        context = prepare_error_context(deploy_result, health_result)

        assert "Exit Code: 0" in context
        assert "Status: SUCCESS" in context

    def test_negative_exit_codes(self):
        """Test with negative exit codes (e.g., timeout = -1)."""
        deploy_result = {"exit_code": -1, "success": False}
        health_result = {"exit_code": -1, "success": False}

        context = prepare_error_context(deploy_result, health_result)

        assert "Exit Code: -1" in context

    def test_very_large_exit_codes(self):
        """Test with very large exit codes."""
        deploy_result = {"exit_code": 999999, "success": False}
        health_result = None

        context = prepare_error_context(deploy_result, health_result)

        assert "Exit Code: 999999" in context


class TestCreateFixPrompt:
    """Test create_fix_prompt function with edge cases."""

    def test_basic_fix_prompt(self):
        """Test basic fix prompt generation."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        prompt = create_fix_prompt(
            repo_path,
            attempt=1,
            max_attempts=3,
            error_context="deployment failed",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        assert str(repo_path) in prompt
        assert "deployment failed" in prompt
        assert "1 of 3" in prompt
        assert str(deploy_script) in prompt
        assert ".sds/health_check.sh" in prompt

    def test_first_attempt_no_previous_summary(self):
        """Test first attempt doesn't reference previous attempts."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        prompt = create_fix_prompt(
            repo_path,
            attempt=1,
            max_attempts=5,
            error_context="error",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        # Should not mention previous attempt for attempt 1
        assert "previous fix attempt" not in prompt.lower()
        assert "attempt #1" not in prompt

    def test_second_attempt_references_previous(self):
        """Test second attempt references previous fix attempt."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        prompt = create_fix_prompt(
            repo_path,
            attempt=2,
            max_attempts=5,
            error_context="error",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        # Should mention previous attempt for attempt > 1
        assert "attempt #2" in prompt.lower()
        assert "previous" in prompt.lower()

    def test_fix_summary_consolidation_disabled_omits_summary_path(self):
        """Test that fix_summary_consolidation=False omits consolidated summary path."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        prompt = create_fix_prompt(
            repo_path,
            attempt=2,
            max_attempts=5,
            error_context="error",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
            fix_summary_consolidation=False,
        )

        assert "fix_summary.md" not in prompt
        assert "fix_summary_1.log" in prompt
        assert "attempt #2" in prompt.lower()

    def test_fix_summary_consolidation_enabled_includes_summary_path(self):
        """Test that fix_summary_consolidation=True (default) includes consolidated summary path."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        prompt = create_fix_prompt(
            repo_path,
            attempt=2,
            max_attempts=5,
            error_context="error",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
            fix_summary_consolidation=True,
        )

        assert "fix_summary.md" in prompt
        assert "fix_summary_1.log" in prompt

    def test_special_characters_in_paths(self):
        """Test paths with special characters."""
        repo_path = Path("/test/repo with spaces & 日本語")
        deploy_script = Path(repo_path / ".sds/deploy.sh")
        health_script = Path(repo_path / ".sds/health_check.sh")

        prompt = create_fix_prompt(
            repo_path,
            attempt=1,
            max_attempts=3,
            error_context="error",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        # Paths should be included
        assert str(repo_path) in prompt

    def test_very_long_error_context(self):
        """Test with very long error context."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        # Create a very long error context
        long_context = "Error: " + "x" * 100000

        prompt = create_fix_prompt(
            repo_path,
            attempt=1,
            max_attempts=3,
            error_context=long_context,
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        # Should include the context (or truncate it gracefully)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_error_context_with_special_characters(self):
        """Test error context with special characters and formatting."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        # Error context with special chars
        error_context = """Error: Permission denied
        Stack trace:
            at func1() <file.py:10>
            at func2() "quoted"
        Special chars: <>&'"\\
        Unicode: 测试 テスト
        """

        prompt = create_fix_prompt(
            repo_path,
            attempt=1,
            max_attempts=3,
            error_context=error_context,
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        # Should include the error context
        assert "Permission denied" in prompt

    def test_fix_prompt_includes_reconciliation_when_code_analysis_exists(self, tmp_path):
        """When code_analysis.md exists, prompt contains architecture reconciliation instructions."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "code_analysis.md").write_text("# Code Analysis Report\n")
        prompt = create_fix_prompt(
            repo_path=tmp_path,
            attempt=1,
            max_attempts=3,
            error_context="container crashed",
            deploy_script_path=sds / "deploy.sh",
            health_check_script_path=sds / "health_check.sh",
        )
        assert "Architecture Reconciliation" in prompt
        assert "code_analysis.md" in prompt

    def test_fix_prompt_omits_reconciliation_when_no_code_analysis(self, tmp_path):
        """When code_analysis.md is absent, no reconciliation instructions appear."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        # code_analysis.md deliberately not created
        prompt = create_fix_prompt(
            repo_path=tmp_path,
            attempt=1,
            max_attempts=3,
            error_context="container crashed",
            deploy_script_path=sds / "deploy.sh",
            health_check_script_path=sds / "health_check.sh",
        )
        assert "Architecture Reconciliation" not in prompt
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_fix_prompt_includes_todo_instructions_when_issues_file_exists(self, tmp_path):
        """When deployment_issues.md exists, prompt contains TODO tracking instructions."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deployment_issues.md").write_text("# Deployment Issues\n\n## TODO\n- [ ] #1 — cert path wrong\n")
        prompt = create_fix_prompt(
            repo_path=tmp_path,
            attempt=1,
            max_attempts=3,
            error_context="container crashed",
            deploy_script_path=sds / "deploy.sh",
            health_check_script_path=sds / "health_check.sh",
        )
        assert "deployment_issues.md" in prompt
        assert "TODO" in prompt
        assert "[x]" in prompt

    def test_fix_prompt_omits_todo_instructions_when_no_issues_file(self, tmp_path):
        """When deployment_issues.md is absent (code_analysis disabled), no TODO instructions appear."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        # deployment_issues.md deliberately not created
        prompt = create_fix_prompt(
            repo_path=tmp_path,
            attempt=1,
            max_attempts=3,
            error_context="container crashed",
            deploy_script_path=sds / "deploy.sh",
            health_check_script_path=sds / "health_check.sh",
        )
        assert "TODO" not in prompt
        assert "[x]" not in prompt
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_max_attempts_boundary_values(self):
        """Test with boundary values for attempt/max_attempts."""
        repo_path = Path("/test/repo")
        deploy_script = Path("/test/repo/.sds/deploy.sh")
        health_script = Path("/test/repo/.sds/health_check.sh")

        # Last attempt
        prompt = create_fix_prompt(
            repo_path,
            attempt=10,
            max_attempts=10,
            error_context="error",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        assert "10 of 10" in prompt

        # First of many
        prompt = create_fix_prompt(
            repo_path,
            attempt=1,
            max_attempts=100,
            error_context="error",
            deploy_script_path=deploy_script,
            health_check_script_path=health_script,
        )

        assert "1 of 100" in prompt


class TestCreateGenerateScriptPrompt:
    """Test create_generate_script_prompt function with edge cases."""

    def test_basic_script_generation_prompt(self):
        """Test basic script generation prompt."""
        prompt = create_generate_script_prompt(
            system_prompt="You are a helpful assistant",
            script_name="deploy.sh",
            repo_context="A Node.js application",
            target_dir="/repo/.sds",
            platform="docker",
        )

        assert "deploy.sh" in prompt
        assert "docker" in prompt
        assert "Node.js" in prompt

    def test_special_characters_in_repo_context(self):
        """Test repo context with special characters."""
        prompt = create_generate_script_prompt(
            system_prompt="System",
            script_name="deploy.sh",
            repo_context="App with <special> & 'chars' \"quotes\" \\backslash",
            target_dir="/repo/.sds",
            platform="kubernetes",
        )

        # Should not crash and should include context
        assert isinstance(prompt, str)
        assert "special" in prompt

    def test_unicode_in_script_name(self):
        """Test script name with unicode characters."""
        prompt = create_generate_script_prompt(
            system_prompt="System",
            script_name="デプロイ.sh",
            repo_context="Context",
            target_dir="/repo/.sds",
            platform="docker",
        )

        assert "デプロイ.sh" in prompt

    def test_very_long_system_prompt(self):
        """Test with very long system prompt."""
        long_system_prompt = "System: " + "x" * 50000

        prompt = create_generate_script_prompt(
            system_prompt=long_system_prompt,
            script_name="deploy.sh",
            repo_context="Context",
            target_dir="/repo/.sds",
            platform="docker",
        )

        # Should handle long prompts
        assert isinstance(prompt, str)

    def test_empty_repo_context(self):
        """Test with empty repo context."""
        prompt = create_generate_script_prompt(
            system_prompt="System",
            script_name="deploy.sh",
            repo_context="",
            target_dir="/repo/.sds",
            platform="docker",
        )

        # Should still generate valid prompt
        assert "deploy.sh" in prompt
        assert "docker" in prompt

    def test_multiline_repo_context(self):
        """Test with multiline repo context."""
        repo_context = """Application structure:
        - Frontend: React
        - Backend: Node.js
        - Database: PostgreSQL
        Dependencies:
        - Express
        - TypeORM
        """

        prompt = create_generate_script_prompt(
            system_prompt="System",
            script_name="deploy.sh",
            repo_context=repo_context,
            target_dir="/repo/.sds",
            platform="kubernetes",
        )

        # Should preserve multiline structure
        assert "React" in prompt
        assert "PostgreSQL" in prompt

    def test_different_platforms(self):
        """Test with different platform values.

        The platform is used to customize the prompt, though it may not appear
        directly in the output.
        """
        platforms = ["docker", "k8s", "docker-compose", "bare-metal"]

        for platform in platforms:
            prompt = create_generate_script_prompt(
                system_prompt="System",
                script_name="deploy.sh",
                repo_context="Context",
                target_dir="/repo/.sds",
                platform=platform,
            )

            # Verify prompt was generated successfully
            assert isinstance(prompt, str)
            assert len(prompt) > 0
            assert "deploy.sh" in prompt
            # Platform may not appear directly but affects prompt structure
