"""Command validation for shell=True subprocess calls.

Rejects known-dangerous command patterns before passing to subprocess.run()
to provide a safety net against destructive LLM-generated commands.
"""

import re

# Each pattern is a tuple of (compiled_regex, human-readable description).
DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(r"\brm\s+.*-[^\s]*r[^\s]*f[^\s]*\s+/\s*($|[;&|])"),
        "rm -rf / (delete root filesystem)",
    ),
    (
        re.compile(r"\brm\s+.*-[^\s]*f[^\s]*r[^\s]*\s+/\s*($|[;&|])"),
        "rm -fr / (delete root filesystem)",
    ),
    (
        re.compile(r"\bmkfs\b"),
        "mkfs (format filesystem)",
    ),
    (
        re.compile(r"\bdd\s+.*\bif="),
        "dd if= (raw disk write)",
    ),
    (
        re.compile(
            r">\s*/(?:etc|usr|boot)/",
        ),
        "write redirect to system directory",
    ),
    (
        re.compile(
            r"\btee\s+.*/(?:etc|usr|boot)/",
        ),
        "tee to system directory",
    ),
    (
        re.compile(
            r"\bcp\s+.*\s/(?:etc|usr|boot)/",
        ),
        "cp to system directory",
    ),
    (
        re.compile(
            r"\bmv\s+.*\s/(?:etc|usr|boot)/",
        ),
        "mv to system directory",
    ),
]


class DangerousCommandError(ValueError):
    """Raised when a command matches a known-dangerous pattern."""

    def __init__(self, command: str, reason: str):
        super().__init__(f"Dangerous command rejected: {reason}. Command: {command}")
        self.command = command
        self.reason = reason


def validate_command(command: str) -> None:
    """Validate a command string against known-dangerous patterns.

    Args:
        command: The shell command string to validate.

    Raises:
        DangerousCommandError: If the command matches a dangerous pattern.
    """
    for pattern, description in DANGEROUS_PATTERNS:
        if pattern.search(command):
            raise DangerousCommandError(command, description)
