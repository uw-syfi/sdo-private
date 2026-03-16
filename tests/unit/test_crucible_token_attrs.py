"""Static check: ensure deprecated pydantic-ai token attribute names are not used."""

import re
from pathlib import Path

DEPRECATED = ["request_tokens", "response_tokens"]
FILES = [
    Path("sregym_agents/crucible/sre_agent.py"),
    Path("sregym_agents/crucible/judge_agent.py"),
]


def test_no_deprecated_token_attrs():
    for path in FILES:
        source = path.read_text()
        for attr in DEPRECATED:
            matches = [m.start() for m in re.finditer(re.escape(attr), source)]
            assert not matches, (
                f"{path} still uses deprecated attribute '{attr}' "
                f"(found at character offsets {matches}). "
                f"Use 'input_tokens'/'output_tokens' instead."
            )
