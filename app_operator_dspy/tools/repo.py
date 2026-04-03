"""Repository utilities for experiment and optimization harnesses."""

import os
import shutil
import subprocess


def init_submodules(source_path: str, dest_path: str) -> None:
    """Initialize git submodules in a copied app directory.

    If the destination has a .gitmodules file, attempts to initialize
    submodules so agents have access to all source code.
    """
    gitmodules = os.path.join(dest_path, ".gitmodules")
    if not os.path.exists(gitmodules):
        return
    # Copy .git reference from source if it's a submodule pointer file
    source_git = os.path.join(source_path, ".git")
    dest_git = os.path.join(dest_path, ".git")
    if os.path.isfile(source_git) and not os.path.exists(dest_git):
        shutil.copy2(source_git, dest_git)
    try:
        subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=dest_path,
            capture_output=True,
            timeout=120,
        )
    except Exception:
        pass
