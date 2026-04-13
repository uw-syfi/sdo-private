"""RLM-based code analyzer agent.

Uses dspy.RLM to programmatically explore a repository through a
sandboxed REPL instead of feeding the entire codebase into the
context window.
"""

import os

import dspy
from dspy.predict.rlm import RLM

from app_operator_dspy.logger import get_logger
from app_operator_dspy.signatures import AnalyzeCodebaseRLM
from app_operator_dspy.tools.agent_tools import (
    list_files_tool,
    read_file_tool,
    run_shell_tool,
)
from app_operator_dspy.tools.filesystem import write_file

log = get_logger("rlm_code_analyzer")


class RLMCodeAnalyzerAgent(dspy.Module):
    """Analyzes a repository using RLM to programmatically explore the codebase.

    Instead of feeding the entire repo into the LLM context, RLM lets the
    model write Python code to read files, list directories, and run shell
    commands inside a sandboxed REPL.
    """

    def __init__(self, sub_lm: dspy.LM | None = None):
        super().__init__()
        self.analyze = RLM(
            AnalyzeCodebaseRLM,
            tools=[read_file_tool, list_files_tool, run_shell_tool],
            max_iterations=20,
            max_llm_calls=50,
            max_output_chars=100_000,
            verbose=True,
            sub_lm=sub_lm,
        )

    def forward(self, repo_path: str) -> dspy.Prediction:
        log.info("scanning {} with RLM...", repo_path)
        result = self.analyze(repo_path=repo_path)
        log.info("RLM analysis complete")

        # Persist analysis outputs
        sds_dir = os.path.join(repo_path, ".sds")
        write_file(os.path.join(sds_dir, "code_analysis.md"), result.analysis)
        write_file(os.path.join(sds_dir, "deployment_issues.md"), result.issues)

        return result
