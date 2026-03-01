from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """Discover the repository root by searching upward for .git or sds.toml.

    Args:
        start: Starting directory. Defaults to cwd.

    Returns:
        The discovered root directory, or *start* if no marker is found.
    """
    root = (start or Path.cwd()).resolve()
    for parent in [root, *root.parents]:
        if (parent / ".git").exists() or (parent / "sds.toml").exists():
            return parent
    return root
