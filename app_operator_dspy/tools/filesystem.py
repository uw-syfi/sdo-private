"""Filesystem tools for DSPy agents."""

from pathlib import Path


def read_file(path: str) -> str:
    """Read and return the contents of a file.

    Args:
        path: Absolute or relative path to the file.

    Returns:
        The file contents, or an error message if the file cannot be read.
    """
    try:
        return Path(path).read_text()
    except (OSError, UnicodeDecodeError) as exc:
        return f"Error reading {path}: {exc}"


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating parent directories if needed.

    Args:
        path: Absolute or relative path to the file.
        content: The text content to write.

    Returns:
        A confirmation message with the number of bytes written.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except OSError:
        raise


def list_files(path: str, pattern: str = "*") -> str:
    """List files matching a glob pattern in the given directory.

    Args:
        path: Directory to search in.
        pattern: Glob pattern to match (e.g. ``"*.py"``, ``"**/*.sh"``).

    Returns:
        Newline-separated list of matching file paths, or an error message.
    """
    try:
        p = Path(path)
        matches = sorted(str(m.relative_to(p)) for m in p.glob(pattern) if m.is_file())
        if not matches:
            return f"No files matching '{pattern}' in {path}"
        return "\n".join(matches)
    except OSError as exc:
        return f"Error listing {path}: {exc}"
