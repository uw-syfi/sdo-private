from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from observer.updater.errors import ToolRootError


@dataclass(frozen=True)
class ObserverToolPaths:
    tool_root: Path
    sdk_dir: Path
    core_dir: Path


def find_app_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".sds" / "diagnostics" / "manifest.yaml").is_file():
            return candidate
    raise ToolRootError("could not find .sds/diagnostics/manifest.yaml from the current directory")


def find_tool_paths() -> ObserverToolPaths:
    env_root = os.environ.get("SDS_OBSERVER_TOOL_ROOT")
    if env_root:
        return _tool_paths_from_root(Path(env_root))

    package_path = Path(__file__).resolve()
    for candidate in package_path.parents:
        if _has_observer_dirs(candidate):
            return _tool_paths_from_root(candidate)
    raise ToolRootError("could not locate observer/sdk and observer/core; set SDS_OBSERVER_TOOL_ROOT")


def _tool_paths_from_root(tool_root: Path) -> ObserverToolPaths:
    resolved = tool_root.resolve()
    observer_dir = resolved / "observer"
    if (observer_dir / "sdk").is_dir() or (observer_dir / "core").is_dir():
        sdk_dir = observer_dir / "sdk"
        core_dir = observer_dir / "core"
    else:
        sdk_dir = resolved / "sdk"
        core_dir = resolved / "core"
    paths = ObserverToolPaths(
        tool_root=resolved,
        sdk_dir=sdk_dir,
        core_dir=core_dir,
    )
    missing = [str(path.name) for path in [paths.sdk_dir, paths.core_dir] if not path.is_dir()]
    if missing:
        raise ToolRootError(f"observer tool root is missing: {', '.join(missing)}")
    return paths


def _has_observer_dirs(candidate: Path) -> bool:
    return ((candidate / "observer" / "sdk").is_dir() and (candidate / "observer" / "core").is_dir()) or (
        (candidate / "sdk").is_dir() and (candidate / "core").is_dir()
    )
