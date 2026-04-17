"""Shared artifact guardrails."""

from dataclasses import dataclass
from pathlib import Path

from app_operator.core import FileSystemInterface


@dataclass
class ArtifactGuardrail:
    required_files: list[str]  # paths relative to repo_path
    max_retries: int = 3

    def missing(self, repo_path: Path, filesystem: FileSystemInterface) -> list[str]:
        return [f for f in self.required_files if not filesystem.exists(repo_path / f)]

    def reminder(self, missing_files: list[str]) -> str:
        files_list = "\n".join(f"  - {f}" for f in missing_files)
        return (
            f"The following required output files were not created:\n{files_list}\n\n"
            "Please write these files now using the write_file tool. "
            "Use the information you have already gathered — do not re-analyze."
        )
