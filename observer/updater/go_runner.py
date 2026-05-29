from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class GoRunner:
    executable: str = "go"

    @classmethod
    def from_environment(cls) -> GoRunner:
        return cls(executable=os.environ.get("SDS_OBSERVER_GO", "go"))

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
