"""Operator-specific routing utilities for RLM-based agents.

Contains regex patterns used by RLMCodingAgent, HybridCodingAgent, and
SubagentCodingAgent to dispatch prompts to the correct execution path.
"""
import re

# Patterns that indicate a file-generation task (write specific output files).
# These tasks use a direct single LLM call instead of the RLM loop.
_FILE_GEN_PATTERNS = [
    r"\.sds/code_analysis\.md",
    r"\.sds/deployment_issues\.md",
    r"\.sds/deploy\.sh",
    r"\.sds/health_check\.sh",
]
_FILE_GEN_RE = re.compile("|".join(_FILE_GEN_PATTERNS))

# Patterns that indicate a pure text-generation task (no file writes needed).
# These tasks use a direct single LLM call with the prompt as-is.
_DIRECT_TEXT_PATTERNS = [
    r"fix_summary",
]
_DIRECT_TEXT_RE = re.compile("|".join(_DIRECT_TEXT_PATTERNS))
