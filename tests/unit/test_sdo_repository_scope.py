from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LEGACY_PATTERNS = (
    re.compile(r"\bSDS\b"),
    re.compile(r"Self-Defining Systems"),
    re.compile(r"\.sds(?:/|\b)"),
    re.compile(r"\bsds[-_.]", re.IGNORECASE),
    re.compile(r"\bSDS_"),
)


def _repository_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    return [
        REPOSITORY_ROOT / raw.decode()
        for raw in completed.stdout.split(b"\0")
        if raw and not raw.startswith((b"bench/sregym/", b"sdo_paper/"))
    ]


def test_tracked_repository_has_no_legacy_sds_names() -> None:
    violations: list[str] = []
    for path in _repository_files():
        if not path.is_file() or path == Path(__file__).resolve():
            continue
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        for pattern in LEGACY_PATTERNS:
            if pattern.search(relative):
                violations.append(f"{relative}: legacy name in path")
                break
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in LEGACY_PATTERNS):
                violations.append(f"{relative}:{line_number}: {line.strip()}")
    assert not violations, "legacy SDS names remain:\n" + "\n".join(violations)


def test_ci_references_only_existing_repository_scripts() -> None:
    ci = (REPOSITORY_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    referenced = sorted(set(re.findall(r"\./(scripts/[A-Za-z0-9_./-]+\.sh)", ci)))
    missing = [relative for relative in referenced if not (REPOSITORY_ROOT / relative).is_file()]
    assert not missing, f"CI references missing scripts: {missing}"
