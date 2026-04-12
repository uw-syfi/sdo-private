"""Tests for filesystem abstraction completeness.

Verifies that glob, rglob, is_file methods work correctly on
InMemoryFilesystem, and that callsites that previously bypassed
the abstraction now use it properly.
"""

from pathlib import Path

import pytest

from app_operator.filesystem import InMemoryFilesystem


@pytest.fixture
def fs():
    return InMemoryFilesystem()


class TestNormalizePath:
    """Test that _normalize_path avoids OS syscalls."""

    def test_normalize_absolute_path(self, fs):
        """Absolute paths should be normalized without resolve()."""
        result = fs._normalize_path(Path("/foo/bar/../baz"))
        assert result == "/foo/baz"

    def test_normalize_dot_components(self, fs):
        """Dot components should be collapsed."""
        result = fs._normalize_path(Path("/foo/./bar"))
        assert result == "/foo/bar"

    def test_normalize_relative_path_anchored_to_cwd(self, fs):
        """Relative paths should be anchored to cwd."""
        result = fs._normalize_path(Path("relative/path"))
        # Should be an absolute path
        assert result.startswith("/")
        assert result.endswith("relative/path")


class TestGlob:
    """Test InMemoryFilesystem.glob()."""

    def test_glob_finds_matching_files(self, fs):
        base = Path("/repo/.sds/logs")
        fs.mkdir(base, parents=True)
        fs.write_text(base / "deploy_attempt_1.log", "log1")
        fs.write_text(base / "deploy_attempt_2.log", "log2")
        fs.write_text(base / "health_check.log", "health")

        results = fs.glob(base, "deploy_attempt_*.log")

        names = sorted(p.name for p in results)
        assert names == ["deploy_attempt_1.log", "deploy_attempt_2.log"]

    def test_glob_returns_empty_for_no_matches(self, fs):
        base = Path("/repo/.sds/logs")
        fs.mkdir(base, parents=True)
        fs.write_text(base / "other.txt", "content")

        results = fs.glob(base, "deploy_attempt_*.log")
        assert results == []

    def test_glob_does_not_recurse(self, fs):
        base = Path("/repo")
        sub = base / "subdir"
        fs.mkdir(sub, parents=True)
        fs.write_text(base / "file.txt", "top")
        fs.write_text(sub / "file.txt", "nested")

        results = fs.glob(base, "file.txt")
        assert len(results) == 1
        assert results[0].name == "file.txt"
        assert "subdir" not in str(results[0])


class TestRglob:
    """Test InMemoryFilesystem.rglob()."""

    def test_rglob_finds_files_recursively(self, fs):
        base = Path("/repo")
        fs.mkdir(base / "src" / "pkg", parents=True)
        fs.write_text(base / "README.md", "readme")
        fs.write_text(base / "src" / "main.py", "main")
        fs.write_text(base / "src" / "pkg" / "util.py", "util")

        results = fs.rglob(base, "*")
        paths = sorted(str(p) for p in results)
        # Should find files and directories recursively
        assert any("main.py" in p for p in paths)
        assert any("util.py" in p for p in paths)
        assert any("README.md" in p for p in paths)

    def test_rglob_pattern_matching(self, fs):
        base = Path("/repo")
        fs.mkdir(base / "src", parents=True)
        fs.write_text(base / "readme.txt", "txt")
        fs.write_text(base / "src" / "main.py", "py")
        fs.write_text(base / "src" / "test.py", "py")

        results = fs.rglob(base, "*.py")
        names = sorted(p.name for p in results)
        assert names == ["main.py", "test.py"]


class TestIsFile:
    """Test InMemoryFilesystem.is_file()."""

    def test_is_file_returns_true_for_files(self, fs):
        path = Path("/repo/file.txt")
        fs.mkdir(Path("/repo"), parents=True)
        fs.write_text(path, "content")
        assert fs.is_file(path) is True

    def test_is_file_returns_false_for_directories(self, fs):
        path = Path("/repo/dir")
        fs.mkdir(path, parents=True)
        assert fs.is_file(path) is False

    def test_is_file_returns_false_for_nonexistent(self, fs):
        assert fs.is_file(Path("/nonexistent")) is False


class TestDeployerGlobIntegration:
    """Test that DeploymentAgent._get_next_attempt_number uses filesystem.glob."""

    def test_get_next_attempt_number_with_inmemory_fs(self):
        from app_operator.cli_agent.agents.deployer import DeploymentAgent
        from tests.fixtures.agents import StubAgent

        repo = Path("/test/repo")
        fs = InMemoryFilesystem()
        fs.mkdir(repo / ".sds" / "logs", parents=True)
        fs.write_text(repo / ".sds" / "deploy.sh", "#!/bin/bash\n")
        fs.write_text(repo / ".sds" / "health_check.sh", "#!/bin/bash\n")
        fs.write_text(repo / ".sds" / "logs" / "deploy_attempt_1.log", "log1")
        fs.write_text(repo / ".sds" / "logs" / "deploy_attempt_3.log", "log3")

        agent = DeploymentAgent(repo, StubAgent(), filesystem=fs)
        assert agent._get_next_attempt_number() == 4

    def test_get_next_attempt_number_no_logs_with_inmemory_fs(self):
        from app_operator.cli_agent.agents.deployer import DeploymentAgent
        from tests.fixtures.agents import StubAgent

        repo = Path("/test/repo")
        fs = InMemoryFilesystem()
        fs.mkdir(repo / ".sds", parents=True)
        fs.write_text(repo / ".sds" / "deploy.sh", "#!/bin/bash\n")
        fs.write_text(repo / ".sds" / "health_check.sh", "#!/bin/bash\n")

        agent = DeploymentAgent(repo, StubAgent(), filesystem=fs)
        assert agent._get_next_attempt_number() == 1


class TestCodeAnalyzerFileTreeIntegration:
    """Test that CodeAnalyzerAgent._get_file_tree uses filesystem abstraction."""

    def test_get_file_tree_with_inmemory_fs(self):
        from app_operator.cli_agent.agents.code_analyzer import CodeAnalyzerAgent
        from tests.fixtures.agents import StubAgent

        repo = Path("/test/repo")
        fs = InMemoryFilesystem()
        fs.mkdir(repo / "src", parents=True)
        fs.write_text(repo / "src" / "main.py", "print('hello')")
        fs.write_text(repo / "README.md", "# Readme")

        analyzer = CodeAnalyzerAgent(repo, StubAgent(), filesystem=fs)
        tree = analyzer._get_file_tree()

        assert "src/main.py" in tree
        assert "README.md" in tree

    def test_get_file_tree_excludes_hidden_dirs(self):
        from app_operator.cli_agent.agents.code_analyzer import CodeAnalyzerAgent
        from tests.fixtures.agents import StubAgent

        repo = Path("/test/repo")
        fs = InMemoryFilesystem()
        fs.mkdir(repo / ".hidden", parents=True)
        fs.mkdir(repo / "src", parents=True)
        fs.write_text(repo / ".hidden" / "secret.txt", "secret")
        fs.write_text(repo / "src" / "app.py", "app")

        analyzer = CodeAnalyzerAgent(repo, StubAgent(), filesystem=fs)
        tree = analyzer._get_file_tree()

        assert "app.py" in tree
        assert "secret.txt" not in tree


class TestAnalyzeRepositoryIntegration:
    """Test that analyze_repository uses filesystem abstraction."""

    def test_analyze_repository_with_inmemory_fs(self):
        from app_operator.prompts.deployment_context import analyze_repository

        repo = Path("/test/repo")
        fs = InMemoryFilesystem()
        fs.mkdir(repo, parents=True)
        fs.write_text(repo / "docker-compose.yml", "version: '3'")
        fs.write_text(repo / "go.mod", "module example")

        context = analyze_repository(repo, filesystem=fs)

        assert "docker-compose.yml" in context
        assert "go.mod" in context
        assert "Repository: repo" in context

    def test_analyze_repository_detects_code_analysis(self):
        from app_operator.prompts.deployment_context import analyze_repository

        repo = Path("/test/repo")
        fs = InMemoryFilesystem()
        fs.mkdir(repo / ".sds", parents=True)
        fs.write_text(repo / ".sds" / "code_analysis.md", "analysis")

        context = analyze_repository(repo, filesystem=fs)
        assert "code_analysis.md" in context

    def test_analyze_repository_detects_readme(self):
        from app_operator.prompts.deployment_context import analyze_repository

        repo = Path("/test/repo")
        fs = InMemoryFilesystem()
        fs.mkdir(repo, parents=True)
        fs.write_text(repo / "README.md", "# Project")

        context = analyze_repository(repo, filesystem=fs)
        assert "README" in context


class TestCreateGenerateScriptPromptIntegration:
    """Test that create_generate_script_prompt checks file existence."""

    def test_with_analysis_files(self, tmp_path):
        """Verify prompt includes reconciliation when analysis files exist."""
        from app_operator.prompts.deployer import create_generate_script_prompt

        sds = tmp_path / ".sds"
        sds.mkdir()
        (sds / "code_analysis.md").write_text("Analysis content here")
        (sds / "deployment_issues.md").write_text("Issues content here")

        prompt = create_generate_script_prompt(
            script_name="deploy.sh",
            repo_context="Context",
            target_dir=str(tmp_path),
            platform="docker",
        )

        assert isinstance(prompt, str)
        assert "deploy.sh" in prompt
        assert "Architecture Reconciliation" in prompt
        assert "Deployment Issues" in prompt

    def test_works_without_analysis_files(self, tmp_path):
        from app_operator.prompts.deployer import create_generate_script_prompt

        sds = tmp_path / ".sds"
        sds.mkdir()

        prompt = create_generate_script_prompt(
            script_name="deploy.sh",
            repo_context="Context",
            target_dir=str(tmp_path),
            platform="docker",
        )

        assert "deploy.sh" in prompt
        assert "Architecture Reconciliation" not in prompt
        assert "Deployment Issues" not in prompt
