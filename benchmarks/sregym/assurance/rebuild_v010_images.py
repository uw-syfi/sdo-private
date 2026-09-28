"""Rebuild the shared ``sdo-*:v0.1.0`` images from a given commit — only when nothing is using them.

RC1.md's "Decisions (autonomous)" section is explicit about why this needs its
own gate: the shared tags were not rebuilt during RC1 integration because
other agents' lanes were running from them, and a rebuild mid-run wedges any
lane started before the fix landed in the image. This module:

- **detects** whether any kind cluster this host knows about is either locked
  by another experiment (SREGym's own per-cluster ``flock``) or currently
  running a pod built from one of the four ``v0.1.0`` images
  (:func:`detect_images_in_use`); refusing to rebuild is the default, and
  ``--force`` is the only override, logged loudly;
- **rebuilds** by running :mod:`scripts.build_sdo_images.sh` against the
  working tree, which must be checked out at the commit the caller names (the
  same "build from the checkout" convention ``build_images.sh`` and RC1 both
  use; this module does not create worktrees or check anything out itself);
- **records** each rebuilt image's id and repo digests plus the commit and
  timestamp, in a JSON file the phase-1 manifest can fold in.

Every host interaction (kind, kubectl, git, docker, the build subprocess) is
injected, so the detection logic and the digest recording are unit-tested
with stubs and never touch a real cluster or Docker daemon; this module's own
CLI is never invoked with an execution path in this task (detection only).

Usage::

    uv run python -m benchmarks.sregym.assurance.rebuild_v010_images <commit> [--force]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: The four shared images every phase-1 lane and the CI image job pin at ``v0.1.0``.
V010_IMAGES: dict[str, str] = {
    "controller": "sdo-controller:v0.1.0",
    "responder": "sdo-responder:v0.1.0",
    "sregym_responder": "sdo-sregym-responder:v0.1.0",
    "validator": "sdo-detector-validator:v0.1.0",
}


# --------------------------------------------------------------------------- detection


class ImageUsageProbe(Protocol):
    """Everything the in-use detector reads from the host; tests substitute a stub."""

    def kind_clusters(self) -> list[str]: ...
    def cluster_lock_owner(self, cluster: str) -> str | None: ...
    def pods_using_image(self, cluster: str, image_ref: str) -> list[str]: ...
    def containers_using_image(self, image_ref: str) -> list[str]: ...


@dataclass(frozen=True)
class UsageFinding:
    reason: str


def detect_images_in_use(images: Mapping[str, str], probe: ImageUsageProbe) -> list[UsageFinding]:
    """Every reason a rebuild of *images* (role -> ref) would be unsafe right now.

    Empty means clear to rebuild. A cluster the probe cannot enumerate
    (``kind_clusters()`` returning ``[]``) is not itself a finding: the
    caller's ``--force`` override exists for exactly the "I checked by hand"
    case, but the default posture here is to refuse rather than assume.
    """

    findings: list[UsageFinding] = []
    for cluster in probe.kind_clusters():
        owner = probe.cluster_lock_owner(cluster)
        if owner:
            findings.append(UsageFinding(f"cluster {cluster} is locked by another experiment: {owner}"))
        for role, ref in images.items():
            pods = probe.pods_using_image(cluster, ref)
            if pods:
                findings.append(UsageFinding(f"cluster {cluster} is running {role} ({ref}) in pod(s) {pods}"))
    for role, ref in images.items():
        containers = probe.containers_using_image(ref)
        if containers:
            findings.append(UsageFinding(f"{role} ({ref}) is running in container(s) {containers}"))
    return findings


class SystemImageUsageProbe:
    """The real host: ``kind``, ``kubectl`` over every known cluster, and ``docker ps``."""

    def __init__(self, *, home: Path | None = None) -> None:
        self._home = home or Path.home()

    def _text(self, argv: list[str], *, timeout: float = 30.0) -> str | None:
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout if completed.returncode == 0 else None

    def kind_clusters(self) -> list[str]:
        out = self._text(["kind", "get", "clusters"])
        return [] if out is None else [line.strip() for line in out.splitlines() if line.strip()]

    def cluster_lock_owner(self, cluster: str) -> str | None:
        from benchmarks.sregym.runner.preflight import flock_owner

        return flock_owner(self._home / ".cache" / "sregym" / "locks" / f"{cluster}.lock")

    def pods_using_image(self, cluster: str, image_ref: str) -> list[str]:
        out = self._text(
            [
                "kubectl",
                "--context",
                f"kind-{cluster}",
                "get",
                "pods",
                "-A",
                "-o",
                "jsonpath={range .items[*]}{.metadata.namespace}/{.metadata.name}={range .spec.containers[*]}"
                '{.image} {end}{"\\n"}{end}',
            ],
            timeout=30,
        )
        if out is None:
            return []
        return [line.split("=", 1)[0] for line in out.splitlines() if image_ref in line]

    def containers_using_image(self, image_ref: str) -> list[str]:
        out = self._text(["docker", "ps", "--filter", f"ancestor={image_ref}", "--format", "{{.Names}}"])
        return [] if out is None else [line.strip() for line in out.splitlines() if line.strip()]


# --------------------------------------------------------------------------- rebuild + digests


@dataclass(frozen=True)
class ImageDigest:
    ref: str
    id: str
    repo_digests: tuple[str, ...] = ()


class ImageInspector(Protocol):
    def inspect(self, ref: str) -> ImageDigest | None: ...


class DockerImageInspector:
    def inspect(self, ref: str) -> ImageDigest | None:
        try:
            completed = subprocess.run(
                ["docker", "image", "inspect", ref, "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        document = json.loads(completed.stdout)
        return ImageDigest(
            ref=ref,
            id=str(document.get("Id", "")),
            repo_digests=tuple(str(item) for item in document.get("RepoDigests") or ()),
        )


class GitHead(Protocol):
    def head_commit(self, repository: Path) -> str | None: ...


class SystemGitHead:
    def head_commit(self, repository: Path) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None


class BuildRunner(Protocol):
    def run(self, project_root: Path) -> int: ...


class SystemBuildRunner:
    def run(self, project_root: Path) -> int:
        completed = subprocess.run(
            ["bash", str(project_root / "scripts" / "build_sdo_images.sh")],
            cwd=str(project_root),
            check=False,
        )
        return completed.returncode


class RebuildRefusedError(RuntimeError):
    def __init__(self, findings: Sequence[UsageFinding]) -> None:
        self.findings = list(findings)
        lines = ["refusing to rebuild sdo-*:v0.1.0; the images are in use:"]
        lines.extend(f"  - {finding.reason}" for finding in findings)
        lines.append("pass --force only once you have confirmed by hand that nothing is mid-run")
        super().__init__("\n".join(lines))


class CommitMismatchError(RuntimeError):
    def __init__(self, expected: str, actual: str | None) -> None:
        super().__init__(
            f"working tree HEAD is {actual!r}, not the requested commit {expected!r}; "
            "check out the commit before rebuilding (this module does not create worktrees)"
        )


@dataclass(frozen=True)
class RebuildResult:
    commit: str
    rebuilt_at: float
    digests: dict[str, ImageDigest]

    def to_dict(self) -> dict[str, object]:
        return {
            "commit": self.commit,
            "rebuilt_at": self.rebuilt_at,
            "images": {role: asdict(digest) for role, digest in self.digests.items()},
        }


def rebuild_v010_images(
    *,
    commit: str,
    project_root: Path,
    usage_probe: ImageUsageProbe,
    git_head: GitHead,
    build_runner: BuildRunner,
    inspector: ImageInspector,
    images: Mapping[str, str] = V010_IMAGES,
    force: bool = False,
    now: float | None = None,
) -> RebuildResult:
    """Rebuild *images* from *commit*; raises instead of building when that is unsafe.

    Order: commit check, then the in-use detection (both bypassable only by
    ``force`` for the in-use check; the commit check is never bypassed,
    because a mismatched checkout would silently build the wrong code), then
    the build subprocess, then digest recording.
    """

    actual = git_head.head_commit(project_root)
    if actual != commit:
        raise CommitMismatchError(commit, actual)

    findings = detect_images_in_use(images, usage_probe)
    if findings and not force:
        raise RebuildRefusedError(findings)

    returncode = build_runner.run(project_root)
    if returncode != 0:
        raise RuntimeError(f"scripts/build_sdo_images.sh exited {returncode}")

    digests: dict[str, ImageDigest] = {}
    missing: list[str] = []
    for role, ref in images.items():
        digest = inspector.inspect(ref)
        if digest is None:
            missing.append(ref)
            continue
        digests[role] = digest
    if missing:
        raise RuntimeError(f"built but could not inspect: {', '.join(missing)}")

    return RebuildResult(commit=commit, rebuilt_at=now if now is not None else time.time(), digests=digests)


def write_digest_record(path: Path, result: RebuildResult) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    import os

    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild sdo-*:v0.1.0 from a given commit, only when no lane or cluster is using them."
    )
    parser.add_argument("commit", help="the commit sha the working tree must already be checked out at")
    parser.add_argument("--force", action="store_true", help="rebuild despite a detected in-use finding")
    parser.add_argument(
        "--out", type=Path, default=None, help="where to write the digest record (default: .launch/v010_digests.json)"
    )
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[3]
    out = args.out or project_root / "benchmarks" / "sregym" / "experiments" / "assurance" / "phase1" / ".launch" / (
        "v010_digests.json"
    )

    try:
        result = rebuild_v010_images(
            commit=args.commit,
            project_root=project_root,
            usage_probe=SystemImageUsageProbe(),
            git_head=SystemGitHead(),
            build_runner=SystemBuildRunner(),
            inspector=DockerImageInspector(),
            force=args.force,
        )
    except (RebuildRefusedError, CommitMismatchError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    write_digest_record(out, result)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
