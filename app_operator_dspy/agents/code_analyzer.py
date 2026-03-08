"""DSPy-native code analyzer agent."""

import os
from pathlib import Path

import dspy

from app_operator_dspy.signatures import AnalyzeCodebase
from app_operator_dspy.tools.filesystem import list_files, read_file, write_file

# Skip binary/generated directories when reading file contents
_SKIP_DIRS = {".git", ".sds", "node_modules", "__pycache__", ".venv", "venv", "vendor"}

DEFAULT_MAX_FILE_SIZE = 100_000


class CodeAnalyzerAgent(dspy.Module):
    """Analyzes a repository and identifies deployment requirements.

    Reads all small text files in the repo and passes them to the LLM,
    letting it decide which are relevant for deployment analysis.
    """

    def __init__(self, max_file_size: int = DEFAULT_MAX_FILE_SIZE):
        super().__init__()
        self.analyze = dspy.ChainOfThought(AnalyzeCodebase)
        self.max_file_size = max_file_size

    def forward(self, repo_path: str) -> dspy.Prediction:
        file_tree = list_files(repo_path, "**/*")

        # Read all small text files — let the LLM decide what matters
        repo = Path(repo_path)
        file_contents = []
        for fpath in sorted(repo.rglob("*")):
            if not fpath.is_file():
                continue
            # Skip files in generated/binary directories
            rel = fpath.relative_to(repo)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            try:
                if fpath.stat().st_size > self.max_file_size:
                    continue
            except OSError:
                continue
            content = read_file(str(fpath))
            if not content.startswith("Error"):
                file_contents.append(f"=== {rel} ===\n{content}")

        context = file_tree
        raw_context = ""
        if file_contents:
            raw_context = "\n\n".join(file_contents)
            context += "\n\n--- File Contents ---\n" + raw_context

        result = self.analyze(repo_path=repo_path, file_tree=context)

        # Persist analysis outputs
        sds_dir = os.path.join(repo_path, ".sds")
        write_file(os.path.join(sds_dir, "code_analysis.md"), result.analysis)
        write_file(os.path.join(sds_dir, "deployment_issues.md"), result.issues)

        # Attach raw file contents for downstream agents (e.g., deployer
        # needs exact port mappings from compose files, not LLM summaries)
        result.raw_context = raw_context

        return result
