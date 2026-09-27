from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def seed_go_cache_from_environment() -> bool:
    """Copy the trusted image cache into this run's private writable cache."""

    seed_value = os.environ.get("SDO_GO_CACHE_SEED", "").strip()
    target_value = os.environ.get("GOCACHE", "").strip()
    if not seed_value or not target_value:
        return False
    seed = Path(seed_value)
    target = Path(target_value)
    if not seed.is_dir():
        raise ValueError(f"SDO_GO_CACHE_SEED is not a directory: {seed}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(seed, target, dirs_exist_ok=True)
    return True


@dataclass(frozen=True)
class GoRunner:
    executable: str = "go"

    @classmethod
    def from_environment(cls) -> GoRunner:
        seed_go_cache_from_environment()
        configured = os.environ.get("SDO_CONTROLLER_GO")
        if configured:
            return cls(executable=configured)
        discovered = shutil.which("go")
        if discovered:
            return cls(executable=discovered)
        local_install = Path("/usr/local/go/bin/go")
        if local_install.is_file():
            return cls(executable=str(local_install))
        return cls()

    def run(self, args: list[str], *, cwd: Path) -> int:
        try:
            completed = subprocess.run(
                [self.executable, *args],
                cwd=cwd,
                check=False,
            )
        except FileNotFoundError:
            print(f"could not find Go executable: {self.executable}")
            return 127
        return completed.returncode
