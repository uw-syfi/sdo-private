"""Operator-specific routing utilities for RLM-based agents.

Contains regex patterns used by RLMCodingAgent, HybridCodingAgent, and
SubagentCodingAgent to dispatch prompts to the correct execution path.
"""

import re

# Patterns that indicate a fix-error task (deployment failure diagnosis).
# These prompts mention .sds/deploy.sh etc. as *context* but should be routed
# to the fix path (RLM loop / subagent fan-out / hybrid), NOT to file
# generation.  Must be checked BEFORE FILE_GEN_RE to avoid false matches.
_FIX_ERROR_PATTERNS = [
    r"deployment has failed",
    r"fix the .* deployment",
    r"analyze deployment errors",
    r"<summary>",
    r"deployment failure",
]
FIX_ERROR_RE = re.compile("|".join(_FIX_ERROR_PATTERNS), re.IGNORECASE)

# Patterns that indicate a file-generation task (write specific output files).
# These tasks use a direct single LLM call instead of the RLM loop.
_FILE_GEN_PATTERNS = [
    r"\.sds/code_analysis\.md",
    r"\.sds/deployment_issues\.md",
    r"\.sds/deploy\.sh",
    r"\.sds/health_check\.sh",
]
FILE_GEN_RE = re.compile("|".join(_FILE_GEN_PATTERNS))

# Patterns that indicate a pure text-generation task (no file writes needed).
# These tasks use a direct single LLM call with the prompt as-is.
_DIRECT_TEXT_PATTERNS = [
    r"fix_summary",
]
DIRECT_TEXT_RE = re.compile("|".join(_DIRECT_TEXT_PATTERNS))
