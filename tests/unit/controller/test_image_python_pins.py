"""The controller images pin the Python packages the checked-in code was locked against.

The images install their Python dependencies with pip, outside ``uv``. A pin
that falls behind ``uv.lock`` builds an image whose code fails to import (for
example agentshim 0.6.8 lacks the 0.7 usage API that ``libs/agent_cli`` uses).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILES = ("controller/Dockerfile.runtime", "controller/Dockerfile.validator")
_PIN = re.compile(r"^\s+([A-Za-z0-9_.-]+)==([0-9][^\s\\]*)", re.MULTILINE)


def _locked_versions() -> dict[str, set[str]]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for package in lock["package"]:
        versions.setdefault(package["name"].lower().replace("_", "-"), set()).add(package["version"])
    return versions


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_image_pip_pins_match_the_lockfile(dockerfile: str) -> None:
    pins = _PIN.findall((REPO_ROOT / dockerfile).read_text(encoding="utf-8"))
    assert pins, f"{dockerfile} pins no Python packages"
    locked = _locked_versions()
    drift = {
        name: (version, sorted(locked.get(name.lower().replace("_", "-"), set())))
        for name, version in pins
        if version not in locked.get(name.lower().replace("_", "-"), set())
    }
    assert drift == {}, f"{dockerfile} pins differ from uv.lock: {drift}"
