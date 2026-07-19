from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyright_config() -> dict[str, object]:
    decoded: object = json.loads((REPO_ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return cast("dict[str, object]", decoded)


def _tracked_python_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", "*.py"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [path for path in result.stdout.splitlines() if path]


def test_pyright_strict_mode_covers_every_tracked_python_file() -> None:
    config = _pyright_config()

    assert config["typeCheckingMode"] == "strict"
    assert "strict" not in config, "path-specific strict mode would leave other Python in standard mode"
    assert "reportMissingTypeStubs" not in config, "strict diagnostics must not be disabled globally"

    raw_includes = config["include"]
    assert isinstance(raw_includes, list)
    include_items = cast("list[object]", raw_includes)
    includes = [path for path in include_items if isinstance(path, str)]
    assert len(includes) == len(include_items)

    uncovered = [
        path
        for path in _tracked_python_files()
        if not any(path == include or path.startswith(f"{include.rstrip('/')}/") for include in includes)
    ]
    assert uncovered == [], f"tracked Python files outside strict Pyright scope: {uncovered}"
