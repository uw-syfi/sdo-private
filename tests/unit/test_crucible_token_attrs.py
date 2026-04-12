"""Static check: ensure deprecated pydantic-ai token attribute names are not used."""

import re
from pathlib import Path

# Match attribute access on RunUsage-like objects: ``.request_tokens`` or
# ``.response_tokens``. A bare substring match would fire on unrelated local
# names like ``last_request_tokens`` (a context-window byte counter).
DEPRECATED = [r"\.request_tokens\b", r"\.response_tokens\b"]
FILES = [
    Path("sregym_agents/crucible/agents/drivers/pydantic_ai_driver.py"),
    Path("sregym_agents/crucible/agents/sre_agent.py"),
    Path("sregym_agents/crucible/agents/judge_agent.py"),
    Path("sregym_agents/crucible/agents/recovery_agent.py"),
]


def test_no_deprecated_token_attrs():
    for path in FILES:
        source = path.read_text()
        for pattern in DEPRECATED:
            matches = [m.start() for m in re.finditer(pattern, source)]
            assert not matches, (
                f"{path} still uses deprecated attribute pattern '{pattern}' "
                f"(found at character offsets {matches}). "
                f"Use '.input_tokens'/'.output_tokens' instead."
            )
