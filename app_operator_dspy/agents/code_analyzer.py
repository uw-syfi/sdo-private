"""DSPy-native code analyzer agent."""

import os
from pathlib import Path

import dspy

from app_operator_dspy.signatures import AnalyzeCodebase
from app_operator_dspy.tools.filesystem import list_files, read_file, write_file

# Skip binary/generated directories when reading file contents
_SKIP_DIRS = {".git", ".sds", "node_modules", "__pycache__", ".venv", "venv", "vendor"}

DEFAULT_MAX_FILE_SIZE = 100_000
DEFAULT_MAX_CONTEXT_CHARS = 500_000  # ~125K tokens, well within Gemini's 1M limit


def _file_depth(rel_path: Path) -> int:
    """Return directory depth of a file path.

    Shallower files (root-level) are read first — they tend to be the
    most important (compose files, Dockerfiles, READMEs, top-level configs).
    """
    return len(rel_path.parts) - 1


class CodeAnalyzerAgent(dspy.Module):
    """Analyzes a repository and identifies deployment requirements.

    Reads files prioritized by deployment relevance (compose, Dockerfiles,
    configs first) up to a context budget, then passes them to the LLM.
    """

    def __init__(
        self,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ):
        super().__init__()
        self.analyze = dspy.ChainOfThought(AnalyzeCodebase)
        self.max_file_size = max_file_size
        self.max_context_chars = max_context_chars

    def forward(self, repo_path: str) -> dspy.Prediction:
        print(f"[code_analyzer] scanning {repo_path}...")
        file_tree = list_files(repo_path, "**/*")

        # Collect candidate files with deployment-relevant ordering
        repo = Path(repo_path)
        candidates = []
        for fpath in repo.rglob("*"):
            if not fpath.is_file():
                continue
            rel = fpath.relative_to(repo)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            try:
                size = fpath.stat().st_size
                if size > self.max_file_size:
                    continue
            except OSError:
                continue
            candidates.append((rel, fpath))

        # Sort by depth (shallower first), then alphabetically
        candidates.sort(key=lambda x: (_file_depth(x[0]), x[0]))

        # Read files up to context budget
        file_contents = []
        total_chars = 0
        files_skipped = 0
        for rel, fpath in candidates:
            content = read_file(str(fpath))
            if content.startswith("Error"):
                continue
            entry = f"=== {rel} ===\n{content}"
            if total_chars + len(entry) > self.max_context_chars:
                files_skipped += 1
                continue
            file_contents.append(entry)
            total_chars += len(entry)

        context = file_tree
        raw_context = ""
        if file_contents:
            raw_context = "\n\n".join(file_contents)
            context += "\n\n--- File Contents ---\n" + raw_context
        if files_skipped:
            context += (
                f"\n\n[Note: {files_skipped} files skipped due to context budget. File tree above lists all files.]"
            )

        print(
            f"[code_analyzer] read {len(file_contents)} files "
            f"({total_chars:,} chars), {files_skipped} skipped"
        )
        print("[code_analyzer] calling LLM for analysis...")
        result = self.analyze(repo_path=repo_path, file_tree=context)
        print("[code_analyzer] analysis complete")

        # Persist analysis outputs
        sds_dir = os.path.join(repo_path, ".sds")
        write_file(os.path.join(sds_dir, "code_analysis.md"), result.analysis)
        write_file(os.path.join(sds_dir, "deployment_issues.md"), result.issues)

        # Attach raw file contents for downstream agents (e.g., deployer
        # needs exact port mappings from compose files, not LLM summaries)
        result.raw_context = raw_context

        return result
