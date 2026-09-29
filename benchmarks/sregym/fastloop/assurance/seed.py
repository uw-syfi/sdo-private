"""Rebuild the lifecycle seed the no-LLM assurance suite runs against.

The seed is SREGym's hotel-reservation source snapshot plus the operational
memory the health judge authored in lifecycle commit ``30e023d`` (generators,
workloads, and the three health detectors), checked in under
``benchmarks/sregym/experiments/assurance/seeds``. Rebuilding it from those two
inputs needs no LLM and no earlier run's scratch directory.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SEEDS_DIR = REPO_ROOT / "benchmarks" / "sregym" / "experiments" / "assurance" / "seeds"
DEFAULT_SEED = "hotel_reservation_30e023d"
#: Optional trees copied over the seed's ``.sdo`` (``seeds/overlays/<name>/``), for example ``links``:
#: the ``link-probe`` workload the health judge would author, its detector and the manifest that installs it.
OVERLAYS_DIRNAME = "overlays"
#: Where the checked-in seed keeps its ``.sdo`` directory (a dot directory would be discovered as an app root).
MEMORY_DIRNAME = "operational_memory"
HOTEL_SOURCE_SUBDIR = Path("SREGym-applications") / "hotelReservation"
_SNAPSHOT_MESSAGE = "Initial application workspace snapshot"
_LIFECYCLE_MESSAGE = "sdo: capture goal, architecture, and independent health judge"


class SeedError(RuntimeError):
    """Raised when the assurance seed cannot be rebuilt."""


def _git(repository: Path, *args: str, author: tuple[str, str] | None = None) -> str:
    command = ["git", "-C", str(repository)]
    if author is not None:
        command += ["-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}"]
    completed = subprocess.run([*command, *args], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SeedError(f"git {' '.join(args)} failed in {repository}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def build_seed_repository(
    target: Path,
    *,
    sregym_dir: Path,
    seed: str = DEFAULT_SEED,
    seeds_dir: Path = SEEDS_DIR,
    overlays: tuple[str, ...] = (),
) -> str:
    """Create ``target`` as a two-commit git repository (source snapshot, then lifecycle memory).

    Returns the lifecycle commit. ``target`` must not exist yet.
    """

    source = sregym_dir / HOTEL_SOURCE_SUBDIR
    memory = seeds_dir / seed / MEMORY_DIRNAME
    if not source.is_dir():
        raise SeedError(f"{source} is missing; run `git -C {sregym_dir} submodule update --init SREGym-applications`")
    if not (memory / "diagnostics" / "manifest.yaml").is_file():
        raise SeedError(f"{memory} is not a seed's operational memory")
    overlay_dirs = [seeds_dir / OVERLAYS_DIRNAME / name for name in overlays]
    for name, overlay in zip(overlays, overlay_dirs, strict=True):
        if not overlay.is_dir():
            raise SeedError(f"seed overlay {name!r} is missing: {overlay}")
    if target.exists():
        raise SeedError(f"{target} already exists")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", ".gitmodules"))
    _git(target, "init", "-q")
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", _SNAPSHOT_MESSAGE, author=("SREGym", "sregym@localhost"))
    shutil.copytree(memory, target / ".sdo")
    for overlay in overlay_dirs:
        shutil.copytree(overlay, target / ".sdo", dirs_exist_ok=True)
    _git(target, "add", "-A", ".sdo")
    _git(target, "commit", "-q", "-m", _LIFECYCLE_MESSAGE, author=("SDO Lifecycle", "sdo-lifecycle@localhost"))
    return _git(target, "rev-parse", "HEAD")
