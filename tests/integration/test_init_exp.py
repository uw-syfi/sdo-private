import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

try:
    from app_operator.commands import init_exp
except ImportError:
    init_exp = None


@pytest.fixture
def source_app(tmp_path):
    """Create a dummy app structure with git and .sds folders."""
    app_name = "test-app"
    # Create source in a distinct subdirectory
    source_root = tmp_path / "source"
    source_root.mkdir()
    app_path = source_root / app_name
    app_path.mkdir()

    # Add source files
    (app_path / "src").mkdir()
    (app_path / "src" / "main.py").write_text("print('hello')")
    (app_path / "README.md").write_text("# Test App")

    # Initialize git to simulate existing git history
    subprocess.run(["git", "init"], cwd=app_path, check=True)
    subprocess.run(["git", "config", "user.email", "you@example.com"], cwd=app_path, check=True)
    subprocess.run(["git", "config", "user.name", "Your Name"], cwd=app_path, check=True)
    (app_path / ".git" / "old_git_file").write_text("should be deleted")

    # Add .sds directory to be deleted
    (app_path / ".sds").mkdir()
    (app_path / ".sds" / "config.toml").write_text("agent_provider = 'gemini'")

    return app_path


@pytest.fixture
def execute_command(tmp_path):
    """Fixture that returns a function to execute the init-exp command."""

    def _execute(app_path_str, exp_name):
        if init_exp is None:
            pytest.fail("init_exp module not implemented")

        # Create a working directory for execution
        work_dir = tmp_path / "work"
        work_dir.mkdir(exist_ok=True)

        args = Namespace(app_path=app_path_str, exp_name=exp_name)

        orig_cwd = os.getcwd()
        os.chdir(work_dir)
        try:
            ret = init_exp.run_command(args)
        finally:
            os.chdir(orig_cwd)

        # Calculate expected target path based on logic: exp/<app-name>/<exp-name>
        # app-name is derived from the resolved app_path name
        resolved_app_path = Path(app_path_str).resolve()
        app_name = resolved_app_path.name
        target_path = work_dir / "exp" / app_name / exp_name

        return ret, target_path

    return _execute


def test_init_exp_copies_files(source_app, execute_command):
    """Verify application files are correctly copied."""
    ret, target_path = execute_command(str(source_app), "exp-files")

    assert ret == 0
    assert target_path.exists()
    assert (target_path / "src" / "main.py").read_text() == "print('hello')"
    assert (target_path / "README.md").read_text() == "# Test App"


def test_init_exp_resets_git_history(source_app, execute_command):
    """Verify old git history is removed and a new repo is initialized."""
    ret, target_path = execute_command(str(source_app), "exp-git")

    assert ret == 0
    # Old git file should be gone
    assert not (target_path / ".git" / "old_git_file").exists()

    # New git repo should be initialized
    assert (target_path / ".git").exists()
    assert (target_path / ".git" / "HEAD").exists()

    # Check it's a valid repo and on branch 'main'
    subprocess.run(["git", "status"], cwd=target_path, check=True, stdout=subprocess.DEVNULL)

    # Verify current branch is main
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=target_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "main"

    # Verify initial commit was created
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=target_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "1"


def test_init_exp_removes_sds_config(source_app, execute_command):
    """Verify .sds directory is removed from the target."""
    ret, target_path = execute_command(str(source_app), "exp-sds")

    assert ret == 0
    assert not (target_path / ".sds").exists()


def test_init_exp_handles_trailing_slash(source_app, execute_command):
    """Verify app path trailing slash is handled correctly for naming."""
    # Append trailing slash
    app_path_slash = str(source_app) + os.sep
    ret, target_path = execute_command(app_path_slash, "exp-slash")

    assert ret == 0
    assert target_path.exists()
    # The target path construction in execute_command relies on the same logic
    # as the implementation (resolve().name), so we verify the name is correct explicitly
    assert target_path.parent.name == "test-app"


def test_init_exp_fails_if_exists(source_app, execute_command):
    """Verify command fails if experiment directory already exists."""
    # First run to create it
    ret1, _ = execute_command(str(source_app), "exp-dup")
    assert ret1 == 0

    # Second run should fail
    ret2, _ = execute_command(str(source_app), "exp-dup")
    assert ret2 != 0


def test_init_exp_handles_git_submodule_structure(tmp_path, execute_command):
    """Verify init-exp works when .git is a file (like in submodules)."""
    app_name = "submodule-app"
    source_root = tmp_path / "submodule_source"
    source_root.mkdir()
    app_path = source_root / app_name
    app_path.mkdir()

    # Add some files
    (app_path / "src").mkdir()
    (app_path / "src" / "code.py").write_text("print('submodule')")

    # simulate git submodule: .git is a file pointing to the gitdir
    (app_path / ".git").write_text("gitdir: ../.git/modules/submodule-app")

    ret, target_path = execute_command(str(app_path), "exp-submod")

    assert ret == 0
    assert target_path.exists()

    # The .git file should be replaced by a .git directory (new repo)
    assert (target_path / ".git").is_dir()
    assert (target_path / ".git" / "HEAD").exists()

    # Check it's a valid repo
    subprocess.run(["git", "status"], cwd=target_path, check=True, stdout=subprocess.DEVNULL)
