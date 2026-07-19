from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from controller.builder.errors import ToolRootError

MEMORY_DIR_NAME = ".sdo"


@dataclass(frozen=True)
class ControllerToolPaths:
    tool_root: Path
    sdk_dir: Path
    core_dir: Path
    runtime_dir: Path


def find_app_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / MEMORY_DIR_NAME / "diagnostics" / "manifest.yaml").is_file():
            return candidate
    raise ToolRootError("could not find .sdo/diagnostics/manifest.yaml")


def find_diagnostics_dir(app_root: Path) -> Path:
    resolved = app_root.resolve()
    diagnostics_dir = resolved / MEMORY_DIR_NAME / "diagnostics"
    if (diagnostics_dir / "manifest.yaml").is_file():
        return diagnostics_dir
    raise ToolRootError(f"application has no .sdo/diagnostics/manifest.yaml: {resolved}")


def find_tool_paths() -> ControllerToolPaths:
    env_root = os.environ.get("SDO_CONTROLLER_TOOL_ROOT")
    if env_root:
        return _tool_paths_from_root(Path(env_root))

    package_path = Path(__file__).resolve()
    for candidate in package_path.parents:
        if _has_controller_dirs(candidate):
            return _tool_paths_from_root(candidate)
    raise ToolRootError("could not locate controller/sdk and controller/core; set SDO_CONTROLLER_TOOL_ROOT")


def _tool_paths_from_root(tool_root: Path) -> ControllerToolPaths:
    resolved = tool_root.resolve()
    controller_root = resolved / "controller"
    if (controller_root / "sdk").is_dir() or (controller_root / "core").is_dir():
        sdk_dir = controller_root / "sdk"
        core_dir = controller_root / "core"
        runtime_dir = controller_root / "runtime"
    else:
        sdk_dir = resolved / "sdk"
        core_dir = resolved / "core"
        runtime_dir = resolved / "runtime"
    paths = ControllerToolPaths(
        tool_root=resolved,
        sdk_dir=sdk_dir,
        core_dir=core_dir,
        runtime_dir=runtime_dir,
    )
    missing = [str(path.name) for path in [paths.sdk_dir, paths.core_dir, paths.runtime_dir] if not path.is_dir()]
    if missing:
        raise ToolRootError(f"controller tool root is missing: {', '.join(missing)}")
    return paths


def _has_controller_dirs(candidate: Path) -> bool:
    return all((candidate / "controller" / name).is_dir() for name in ["sdk", "core", "runtime"]) or all(
        (candidate / name).is_dir() for name in ["sdk", "core", "runtime"]
    )
