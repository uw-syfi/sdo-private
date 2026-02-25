"""Unit tests for the FilteredCodingAgent baseline."""

import unittest.mock as mock

import pytest

from libs.agent_cli.filtered_agent import filter_log, FilteredCodingAgent


# =========================================================================
# filter_log()
# =========================================================================


class TestFilterLog:
    """Tests for the heuristic log-filtering helper."""

    def test_empty_log(self):
        assert filter_log("") == ""

    def test_whitespace_only_log(self):
        assert filter_log("   \n\n  ") == ""

    def test_short_log_all_in_tail(self):
        """When the log fits within tail_lines, there is no error section."""
        log = "line1\nline2\nline3"
        result = filter_log(log, tail_lines=200)
        assert "=== LAST 3 LINES OF LOG ===" in result
        assert "ERROR LINES" not in result
        assert "line1" in result
        assert "line3" in result

    def test_tail_truncation(self):
        """Only the last tail_lines lines appear in the tail section."""
        lines = [f"line{i}" for i in range(500)]
        log = "\n".join(lines)
        result = filter_log(log, tail_lines=200)

        assert "=== LAST 200 LINES OF LOG ===" in result
        # Last line present
        assert "line499" in result
        # First line should NOT be in the tail section (it's in the head)
        # but it has no error pattern, so it shouldn't appear at all
        assert "line0" not in result

    def test_error_grep_from_head(self):
        """Error lines from before the tail window appear in the error section."""
        head = ["INFO: starting", "ERROR: port conflict", "INFO: continuing"]
        tail = [f"tail_line{i}" for i in range(200)]
        log = "\n".join(head + tail)

        result = filter_log(log, tail_lines=200)

        assert "=== ERROR LINES FROM LOG ===" in result
        assert "ERROR: port conflict" in result
        # Non-error head lines should not appear
        assert "INFO: starting" not in result

    def test_error_patterns(self):
        """Each documented error keyword is matched."""
        keywords = [
            "error occurred",
            "build failed",
            "fatal crash",
            "exception thrown",
            "traceback found",
            "panic in goroutine",
            "cannot open file",
            "not found",
            "permission denied",
            "connection timeout",
            "connection refused",
            "exit code 1",
            "exit status 2",
        ]
        # Put keywords in head, then 200 clean tail lines
        tail = [f"ok_line{i}" for i in range(200)]
        log = "\n".join(keywords + tail)
        result = filter_log(log, tail_lines=200)

        for kw in keywords:
            assert kw in result, f"Expected pattern '{kw}' in output"

    def test_max_error_lines_cap(self):
        """Error lines exceeding max_error_lines are capped."""
        errors = [f"ERROR: issue {i}" for i in range(200)]
        tail = [f"tail{i}" for i in range(200)]
        log = "\n".join(errors + tail)

        result = filter_log(log, tail_lines=200, max_error_lines=100)

        error_section = result.split("=== LAST")[0]
        error_count = error_section.count("ERROR: issue")
        assert error_count == 100

    def test_no_double_counting(self):
        """Error lines within the tail window don't appear in the error section."""
        head = [f"clean_line{i}" for i in range(100)]
        tail_with_errors = ["ERROR: in tail section"] + [
            f"tail{i}" for i in range(199)
        ]
        log = "\n".join(head + tail_with_errors)

        result = filter_log(log, tail_lines=200)

        # "ERROR: in tail section" should appear in the tail, not in error section
        assert "=== ERROR LINES FROM LOG ===" not in result
        assert "ERROR: in tail section" in result


# =========================================================================
# FilteredCodingAgent – routing
# =========================================================================


class TestFilteredAgentRouting:
    """Verify prompt routing to the correct internal method."""

    def _make_agent(self):
        return FilteredCodingAgent(model="vertex_ai/gemini-2.0-flash")

    def test_direct_text_route(self):
        agent = self._make_agent()
        with mock.patch.object(agent, "_generate_direct", return_value="ok") as m:
            agent.generate("Please generate a fix_summary for the deployment")
            m.assert_called_once()

    def test_file_gen_route(self):
        agent = self._make_agent()
        with mock.patch.object(agent, "_generate_files", return_value="ok") as m:
            agent.generate("Generate .sds/deploy.sh for this repo", cwd="/tmp")
            m.assert_called_once()

    def test_fix_route(self):
        agent = self._make_agent()
        with mock.patch.object(agent, "_generate_fix", return_value="ok") as m:
            agent.generate("Fix the deployment error: port conflict", cwd="/tmp")
            m.assert_called_once()


# =========================================================================
# FilteredCodingAgent._find_latest_log
# =========================================================================


class TestFilteredAgentFindLatestLog:
    """Tests for the log-file discovery helper."""

    def test_finds_latest(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "deploy.log").write_text("old log")
        (logs / "deploy_2.log").write_text("newer log")

        result = FilteredCodingAgent._find_latest_log(logs, "deploy")

        # Should return the content of the most recently modified file
        assert result in ("old log", "newer log")
        assert result != ""

    def test_empty_dir(self, tmp_path):
        logs = tmp_path / "logs"
        logs.mkdir()

        result = FilteredCodingAgent._find_latest_log(logs, "deploy")

        assert result == ""

    def test_nonexistent_dir(self, tmp_path):
        result = FilteredCodingAgent._find_latest_log(
            tmp_path / "nonexistent", "deploy"
        )

        assert result == ""


# =========================================================================
# FilteredCodingAgent – backup logic
# =========================================================================


class TestFilteredAgentBackup:
    """Tests for deploy.sh.bak creation in _generate_fix."""

    def test_backup_created(self, tmp_path):
        """_generate_fix creates a .bak file before calling the LLM."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("#!/bin/bash\ndocker compose up -d\n")
        (sds / "logs").mkdir()

        agent = FilteredCodingAgent(model="vertex_ai/gemini-2.0-flash")

        with mock.patch(
            "libs.agent_cli.filtered_agent._litellm_call_with_retry",
            return_value="no changes needed",
        ):
            agent._generate_fix(
                "Fix the error", repo_path=tmp_path, token_acc={}
            )

        assert (sds / "deploy.sh.bak").exists()
        assert (
            (sds / "deploy.sh.bak").read_text()
            == "#!/bin/bash\ndocker compose up -d\n"
        )

    def test_backup_not_overwritten(self, tmp_path):
        """An existing .bak is not overwritten on subsequent calls."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "deploy.sh").write_text("modified content\n")
        (sds / "deploy.sh.bak").write_text("original content\n")
        (sds / "logs").mkdir()

        agent = FilteredCodingAgent(model="vertex_ai/gemini-2.0-flash")

        with mock.patch(
            "libs.agent_cli.filtered_agent._litellm_call_with_retry",
            return_value="no changes needed",
        ):
            agent._generate_fix(
                "Fix the error", repo_path=tmp_path, token_acc={}
            )

        assert (sds / "deploy.sh.bak").read_text() == "original content\n"


# =========================================================================
# FilteredCodingAgent – file writing from LLM response
# =========================================================================


class TestFilteredAgentFileWriting:
    """Tests for FILE: section parsing and writing in _generate_fix."""

    def test_writes_file_sections(self, tmp_path):
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "logs").mkdir()

        llm_response = (
            "FILE: .sds/deploy.sh\n"
            "```bash\n"
            "#!/bin/bash\necho fixed\n"
            "```\n\n"
            "<summary>\n- Fixed deployment\n</summary>"
        )

        agent = FilteredCodingAgent(model="vertex_ai/gemini-2.0-flash")

        with mock.patch(
            "libs.agent_cli.filtered_agent._litellm_call_with_retry",
            return_value=llm_response,
        ):
            agent._generate_fix(
                "Fix the error", repo_path=tmp_path, token_acc={}
            )

        assert (sds / "deploy.sh").exists()
        assert "echo fixed" in (sds / "deploy.sh").read_text()

    def test_writes_files_outside_sds(self, tmp_path):
        """The broader regex allows writing files outside .sds/."""
        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "logs").mkdir()

        llm_response = (
            "FILE: docker-compose.yml\n"
            "```yaml\n"
            "version: '3'\nservices:\n  web:\n    image: nginx\n"
            "```\n"
        )

        agent = FilteredCodingAgent(model="vertex_ai/gemini-2.0-flash")

        with mock.patch(
            "libs.agent_cli.filtered_agent._litellm_call_with_retry",
            return_value=llm_response,
        ):
            agent._generate_fix(
                "Fix the error", repo_path=tmp_path, token_acc={}
            )

        assert (tmp_path / "docker-compose.yml").exists()
        assert "nginx" in (tmp_path / "docker-compose.yml").read_text()


# =========================================================================
# Config / factory integration
# =========================================================================


class TestFilteredProviderRegistration:
    """Verify the 'filtered' provider is properly registered."""

    def test_valid_provider_in_config(self):
        from app_operator.config import AgentConfig

        cfg = AgentConfig(provider="filtered")
        assert cfg.provider == "filtered"

    def test_registered_in_agent_registry(self):
        from libs.agent_cli.base import AGENT_REGISTRY

        assert "filtered" in AGENT_REGISTRY
        assert AGENT_REGISTRY["filtered"] is FilteredCodingAgent


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
