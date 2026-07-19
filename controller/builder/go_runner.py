from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoRunner:
    executable: str = "go"

    @classmethod
    def from_environment(cls) -> GoRunner:
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
