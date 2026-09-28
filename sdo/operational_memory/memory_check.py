"""``sdo-memory-check``: run the broker's memory rules against uncommitted ``.sdo/`` edits.

An agent authoring operational memory runs this before returning, so a
proposal the broker would reject is fixed in the same turn::

    python3 -m sdo.operational_memory.memory_check --app . --actor responder

It applies :class:`MemoryValidator` without diagnostics (ownership, front
matter, the playbook index, role placeholders, script syntax, detector
registration and provenance rules) to every ``.sdo/`` path that differs from
the baseline commit (``HEAD`` by default) or is untracked. Detector Go tests
are not run; use ``controller.builder.check_cli draft-test`` for those.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sdo.operational_memory.models import ArtifactOwner
from sdo.operational_memory.validation import (
    PLAYBOOK_INDEX_PATH,
    MemoryValidationError,
    MemoryValidator,
)


class MemoryCheckError(RuntimeError):
    """Raised when the memory check cannot determine what to validate."""


#: Fix instructions for the validator errors agents hit most often, keyed by message prefix.
_HINTS: tuple[tuple[str, str], ...] = (
    (
        "playbook is missing from index",
        f"add a relative Markdown link such as `- [Title](<fault-class>/README.md)` to {PLAYBOOK_INDEX_PATH}",
    ),
    (
        "playbook index link does not exist",
        f"point every link in {PLAYBOOK_INDEX_PATH} at an existing `<fault-class>/README.md`",
    ),
    (
        "playbook must use a role placeholder",
        "use at least one uppercase role placeholder such as `<NAMESPACE>` or `<DEPLOYMENT>` in the playbook body; "
        "lowercase `<namespace>` does not count",
    ),
    (
        "playbook scripts must use the",
        "rename files under `.sdo/playbooks/<fault-class>/scripts/` to end in `.sh`",
    ),
    ("invalid shell syntax", "fix the script until `bash -n <script>` succeeds"),
    (
        "responder may not rewrite provenance",
        "restore the existing detector's originatingIncident/originatingCommit in the manifest and its Spec()",
    ),
    (
        "incident detector",
        "set `persistence.firing: 1` in the manifest and `Firing: 1` in Spec() for a new or changed incident detector",
    ),
)


def _hint(error: str) -> str | None:
    if "does not own" in error:
        return "revert that file: this actor may not edit it (`git checkout -- <path>`, or delete it if new)"
    for prefix, hint in _HINTS:
        if error.startswith(prefix):
            return hint
    return None


def _git(app: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", "-C", str(app), *args], check=False, capture_output=True)
    if completed.returncode != 0:
        details = completed.stderr.decode(errors="replace").strip()
        raise MemoryCheckError(f"git {' '.join(args)} failed: {details}")
    return completed.stdout


def changed_memory_paths(app: Path, baseline: str) -> list[str]:
    """``.sdo/`` paths, relative to *app*, that differ from *baseline* or are untracked."""

    changed = _git(app, "diff", "--relative", "--name-only", "-z", baseline, "--", ".sdo")
    untracked = _git(app, "ls-files", "--others", "--exclude-standard", "-z", "--", ".sdo")
    paths = {path for path in (changed + b"\0" + untracked).decode().split("\0") if path}
    return sorted(paths)


def _extract_baseline(app: Path, baseline: str, destination: Path) -> Path | None:
    """Materialize *baseline*'s ``.sdo/`` under *destination*; return that baseline app root.

    A throwaway index keeps the repository's own index and worktree untouched.
    """

    prefix = _git(app, "rev-parse", "--show-prefix").decode().strip()
    tree = f"{baseline}:{prefix}.sdo"
    probe = subprocess.run(["git", "-C", str(app), "cat-file", "-e", tree], check=False, capture_output=True)
    if probe.returncode != 0:
        return None
    root = destination / "baseline"
    env = {**os.environ, "GIT_INDEX_FILE": str(destination / "index")}
    for args in (("read-tree", "--prefix=.sdo/", tree), ("checkout-index", "--all", f"--prefix={root}/")):
        completed = subprocess.run(["git", "-C", str(app), *args], check=False, capture_output=True, env=env)
        if completed.returncode != 0:
            details = completed.stderr.decode(errors="replace").strip()
            raise MemoryCheckError(f"cannot materialize baseline {baseline}: {details}")
    return root


def check(app: Path, *, actor: ArtifactOwner, baseline: str = "HEAD") -> list[str]:
    """Validate uncommitted memory edits; return the checked paths.

    Raises:
        MemoryValidationError: The proposal violates a broker memory rule.
        MemoryCheckError: Git could not describe the proposal or its baseline.
    """

    app = app.resolve()
    paths = changed_memory_paths(app, baseline)
    if not paths:
        return []
    with tempfile.TemporaryDirectory(prefix="sdo-memory-check-") as scratch:
        baseline_root = _extract_baseline(app, baseline, Path(scratch))
        MemoryValidator(run_diagnostics=False).validate(
            app,
            actor=actor,
            changed_paths=paths,
            baseline_root=baseline_root,
        )
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sdo-memory-check",
        description="Check uncommitted .sdo/ edits against the commit broker's memory rules (no detector tests).",
    )
    parser.add_argument("--app", type=Path, default=Path("."), help="application root holding .sdo/ (default: .)")
    parser.add_argument(
        "--actor",
        choices=[owner.value for owner in ArtifactOwner],
        default=ArtifactOwner.RESPONDER.value,
        help="artifact owner proposing the edits (default: responder)",
    )
    parser.add_argument(
        "--baseline",
        default="HEAD",
        help="commit the proposal is compared with; the reflection worktree's HEAD is the outcome commit",
    )
    args = parser.parse_args(argv)
    try:
        paths = check(args.app, actor=ArtifactOwner(args.actor), baseline=args.baseline)
    except MemoryValidationError as exc:
        error = str(exc)
        print(f"sdo-memory-check: FAILED: {error}", file=sys.stderr)
        hint = _hint(error)
        if hint:
            print(f"fix: {hint}", file=sys.stderr)
        print("Fix the error and rerun until it prints OK.", file=sys.stderr)
        return 1
    except MemoryCheckError as exc:
        print(f"sdo-memory-check: ERROR: {exc}", file=sys.stderr)
        return 2
    if not paths:
        print("sdo-memory-check: OK: no uncommitted .sdo changes to check")
        return 0
    print(f"sdo-memory-check: OK: {len(paths)} changed .sdo path(s) pass the broker's memory rules")
    for path in paths:
        print(f"  {path}")
    print("Detector Go tests are not part of this check; run check_cli draft-test for a changed incident detector.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
