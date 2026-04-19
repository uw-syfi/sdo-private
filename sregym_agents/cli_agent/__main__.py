"""CLI entry: `uv run python -m sregym_agents.cli_agent ...`."""

from __future__ import annotations

import sys

from .driver import main

if __name__ == "__main__":
    sys.exit(main())
