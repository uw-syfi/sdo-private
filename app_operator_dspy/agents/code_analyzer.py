"""DSPy-native code analyzer agent."""

import os

import dspy

from app_operator_dspy.signatures import AnalyzeCodebase
from app_operator_dspy.tools.filesystem import write_file


class CodeAnalyzerAgent(dspy.Module):
    """Analyzes a repository and identifies deployment requirements."""

    def __init__(self):
        super().__init__()
        self.analyze = dspy.ChainOfThought(AnalyzeCodebase)

    def forward(self, repo_path: str) -> dspy.Prediction:
        print(f"[code_analyzer] scanning {repo_path}...")
        print("[code_analyzer] calling LLM for analysis...")
        result = self.analyze(repo_path=repo_path)
        print("[code_analyzer] analysis complete")

        # Persist analysis outputs
        sds_dir = os.path.join(repo_path, ".sds")
        write_file(os.path.join(sds_dir, "code_analysis.md"), result.analysis)
        write_file(os.path.join(sds_dir, "deployment_issues.md"), result.issues)

        return result
