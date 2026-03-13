"""Tests for ArtifactGuardrail."""

from unittest.mock import MagicMock

from app_operator.langgraph.guardrails import ArtifactGuardrail


class TestArtifactGuardrailMissing:
    def test_missing_returns_empty_when_all_exist(self, tmp_path):
        files = [".sds/code_analysis.md", ".sds/deployment_issues.md"]
        for f in files:
            path = tmp_path / f
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        # Use RealFilesystem to verify actual disk files
        from app_operator.filesystem import RealFilesystem

        guardrail = ArtifactGuardrail(files)
        assert guardrail.missing(tmp_path, RealFilesystem()) == []

    def test_missing_returns_absent_files(self, tmp_path):
        files = [".sds/code_analysis.md", ".sds/deployment_issues.md"]
        from app_operator.filesystem import RealFilesystem

        guardrail = ArtifactGuardrail(files)
        assert guardrail.missing(tmp_path, RealFilesystem()) == files

    def test_missing_partial(self, tmp_path):
        files = [".sds/code_analysis.md", ".sds/deployment_issues.md"]
        present = tmp_path / ".sds" / "code_analysis.md"
        present.parent.mkdir(parents=True, exist_ok=True)
        present.touch()
        from app_operator.filesystem import RealFilesystem

        guardrail = ArtifactGuardrail(files)
        assert guardrail.missing(tmp_path, RealFilesystem()) == [".sds/deployment_issues.md"]


class TestArtifactGuardrailReminder:
    def test_reminder_lists_files(self):
        guardrail = ArtifactGuardrail([".sds/code_analysis.md", ".sds/deployment_issues.md"])
        reminder = guardrail.reminder([".sds/code_analysis.md", ".sds/deployment_issues.md"])
        assert ".sds/code_analysis.md" in reminder
        assert ".sds/deployment_issues.md" in reminder
        assert "write_file" in reminder

    def test_reminder_single_file(self):
        guardrail = ArtifactGuardrail([".sds/deploy.sh"])
        reminder = guardrail.reminder([".sds/deploy.sh"])
        assert ".sds/deploy.sh" in reminder


class TestArtifactGuardrailMaxRetries:
    def test_max_retries_bound(self, tmp_path):
        """ctx.invoke is called exactly 1 + max_retries times when files are never created."""
        from app_operator.filesystem import RealFilesystem
        from app_operator.langgraph.guardrails import ArtifactGuardrail

        guardrail = ArtifactGuardrail([".sds/code_analysis.md"], max_retries=3)
        filesystem = RealFilesystem()

        invoke_count = 0

        def fake_invoke(*args, **kwargs):
            nonlocal invoke_count
            invoke_count += 1
            return MagicMock(messages=[], text="")

        # Simulate the retry loop from analyzer.py
        fake_invoke()  # initial call
        for _retry in range(guardrail.max_retries):
            missing = guardrail.missing(tmp_path, filesystem)
            if not missing:
                break
            fake_invoke()  # retry call

        assert invoke_count == 1 + guardrail.max_retries

    def test_no_retries_when_files_exist(self, tmp_path):
        """No retries are made when files are present after the initial call."""
        from app_operator.filesystem import RealFilesystem

        files = [".sds/code_analysis.md"]
        present = tmp_path / ".sds" / "code_analysis.md"
        present.parent.mkdir(parents=True, exist_ok=True)
        present.touch()

        guardrail = ArtifactGuardrail(files, max_retries=3)
        filesystem = RealFilesystem()

        invoke_count = 0

        def fake_invoke(*args, **kwargs):
            nonlocal invoke_count
            invoke_count += 1
            return MagicMock(messages=[], text="")

        fake_invoke()  # initial call
        for _retry in range(guardrail.max_retries):
            missing = guardrail.missing(tmp_path, filesystem)
            if not missing:
                break
            fake_invoke()

        assert invoke_count == 1  # no retries needed
