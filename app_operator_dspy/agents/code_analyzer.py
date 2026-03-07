"""DSPy-native code analyzer agent."""

import os

import dspy

from app_operator_dspy.signatures import AnalyzeCodebase
from app_operator_dspy.tools.filesystem import list_files, read_file, write_file

_KEY_FILENAMES = [
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "README.md",
    "requirements.txt",
    "package.json",
    "go.mod",
    "Makefile",
]


class CodeAnalyzerAgent(dspy.Module):
    """Analyzes a repository and identifies deployment requirements.

    Uses ChainOfThought reasoning over the file tree and key file contents
    to produce a deployment-oriented analysis and list of potential issues.
    """

    def __init__(self):
        super().__init__()
        self.analyze = dspy.ChainOfThought(AnalyzeCodebase)

    def forward(self, repo_path: str) -> dspy.Prediction:
        file_tree = list_files(repo_path, "**/*")

        # Read key deployment-related files for richer context
        key_contents = []
        for name in _KEY_FILENAMES:
            fpath = os.path.join(repo_path, name)
            if os.path.isfile(fpath):
                content = read_file(fpath)
                if not content.startswith("Error"):
                    key_contents.append(f"=== {name} ===\n{content}")

        context = file_tree
        if key_contents:
            context += "\n\n--- Key File Contents ---\n" + "\n\n".join(key_contents)

        result = self.analyze(repo_path=repo_path, file_tree=context)

        # Persist analysis outputs
        sds_dir = os.path.join(repo_path, ".sds")
        write_file(os.path.join(sds_dir, "code_analysis.md"), result.analysis)
        write_file(os.path.join(sds_dir, "deployment_issues.md"), result.issues)

        return result
