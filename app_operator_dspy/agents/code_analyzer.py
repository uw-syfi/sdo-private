"""DSPy-native code analyzer agent."""

import os

import dspy

from app_operator_dspy.logger import get_logger
from app_operator_dspy.signatures import AnalyzeCodebase
from app_operator_dspy.tools import CODE_ANALYZER_TOOLS
from app_operator_dspy.tools.filesystem import write_file

log = get_logger("code_analyzer")


class CodeAnalyzerAgent(dspy.Module):
    """Analyzes a repository and identifies deployment requirements."""

    def __init__(self):
        super().__init__()
        self.analyze = dspy.ReAct(AnalyzeCodebase, tools=CODE_ANALYZER_TOOLS, max_iters=12)

    def forward(self, repo_path: str) -> dspy.Prediction:
        log.info("scanning {}...", repo_path)
        log.info("calling LLM for analysis...")
        result = self.analyze(repo_path=repo_path)
        log.info("analysis complete")

        # Persist analysis outputs
        sds_dir = os.path.join(repo_path, ".sds")
        write_file(os.path.join(sds_dir, "code_analysis.md"), result.analysis)
        write_file(os.path.join(sds_dir, "deployment_issues.md"), result.issues)

        return result
