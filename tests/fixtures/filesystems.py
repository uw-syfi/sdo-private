"""Test filesystem utilities for isolated testing.

This module provides pre-configured filesystem instances for common
test scenarios.
"""

from pathlib import Path
from app_operator.filesystem import InMemoryFilesystem


def create_test_filesystem_with_scripts(repo_path: Path) -> InMemoryFilesystem:
    """Create a test filesystem with pre-generated scripts.

    Args:
        repo_path: The repository path to use.

    Returns:
        InMemoryFilesystem: A configured test filesystem.
    """
    fs = InMemoryFilesystem()

    # Create directory structure
    fs.directories.add(str(repo_path))
    fs.directories.add(str(repo_path / ".sds"))
    fs.directories.add(str(repo_path / ".sds" / "logs"))

    # Create deploy script
    deploy_script = repo_path / ".sds" / "deploy.sh"
    fs.write_text(
        deploy_script,
        "#!/bin/bash\nset -e\necho 'Deploying...'\necho 'Deployment successful'\nexit 0\n",
    )
    fs.chmod(deploy_script, 0o755)

    # Create health check script
    health_script = repo_path / ".sds" / "health_check.sh"
    fs.write_text(
        health_script,
        "#!/bin/bash\nset -e\necho 'Running health checks...'\necho 'All checks passed'\nexit 0\n",
    )
    fs.chmod(health_script, 0o755)

    return fs


def create_readonly_filesystem(repo_path: Path) -> InMemoryFilesystem:
    """Create a test filesystem that simulates read-only filesystem.

    Args:
        repo_path: The repository path to use.

    Returns:
        InMemoryFilesystem: A configured test filesystem with read-only errors.
    """
    fs = InMemoryFilesystem()

    # Create directory structure
    fs.directories.add(str(repo_path))
    fs.directories.add(str(repo_path / ".sds"))

    # Mark .sds as read-only
    fs.simulate_readonly(repo_path / ".sds" / "deploy.sh")
    fs.simulate_readonly(repo_path / ".sds" / "health_check.sh")
    fs.simulate_readonly(repo_path / ".sds" / "logs")

    return fs


def create_permission_denied_filesystem(repo_path: Path) -> InMemoryFilesystem:
    """Create a test filesystem that simulates permission errors.

    Args:
        repo_path: The repository path to use.

    Returns:
        InMemoryFilesystem: A configured test filesystem with permission errors.
    """
    fs = InMemoryFilesystem()

    # Create directory structure
    fs.directories.add(str(repo_path))

    # Simulate permission denied on .sds directory
    fs.simulate_permission_error(repo_path / ".sds")

    return fs


def create_disk_full_filesystem(repo_path: Path) -> InMemoryFilesystem:
    """Create a test filesystem that simulates disk full.

    Args:
        repo_path: The repository path to use.

    Returns:
        InMemoryFilesystem: A configured test filesystem with disk full errors.
    """
    fs = InMemoryFilesystem()

    # Create directory structure
    fs.directories.add(str(repo_path))
    fs.directories.add(str(repo_path / ".sds"))
    fs.directories.add(str(repo_path / ".sds" / "logs"))

    # Simulate disk full for log files
    fs.simulate_disk_full(repo_path / ".sds" / "logs" / "deploy_attempt_1.log")

    return fs
