from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from benchmarks.sregym.assurance.rebuild_v010_images import (
    CommitMismatchError,
    ImageDigest,
    RebuildRefusedError,
    detect_images_in_use,
    rebuild_v010_images,
    write_digest_record,
)

IMAGES = {"controller": "sdo-controller:v0.1.0", "validator": "sdo-detector-validator:v0.1.0"}


class StubUsageProbe:
    def __init__(
        self,
        *,
        clusters: list[str] | None = None,
        locks: dict[str, str] | None = None,
        pods: dict[tuple[str, str], list[str]] | None = None,
        containers: dict[str, list[str]] | None = None,
    ) -> None:
        self._clusters = clusters or []
        self._locks = locks or {}
        self._pods = pods or {}
        self._containers = containers or {}

    def kind_clusters(self) -> list[str]:
        return self._clusters

    def cluster_lock_owner(self, cluster: str) -> str | None:
        return self._locks.get(cluster)

    def pods_using_image(self, cluster: str, image_ref: str) -> list[str]:
        return self._pods.get((cluster, image_ref), [])

    def containers_using_image(self, image_ref: str) -> list[str]:
        return self._containers.get(image_ref, [])


def test_detect_images_in_use_is_clear_when_nothing_is_running() -> None:
    probe = StubUsageProbe(clusters=["assure-w0", "assure-w1"])
    assert detect_images_in_use(IMAGES, probe) == []


def test_detect_images_in_use_flags_a_locked_cluster() -> None:
    probe = StubUsageProbe(clusters=["assure-w0"], locks={"assure-w0": "pid 1234"})
    findings = detect_images_in_use(IMAGES, probe)
    assert len(findings) == 1
    assert "locked by another experiment" in findings[0].reason


def test_detect_images_in_use_flags_a_pod_running_a_v010_image() -> None:
    probe = StubUsageProbe(
        clusters=["luna-w2"],
        pods={("luna-w2", "sdo-controller:v0.1.0"): ["sdo/controller-abc"]},
    )
    findings = detect_images_in_use(IMAGES, probe)
    assert len(findings) == 1
    assert "controller" in findings[0].reason
    assert "luna-w2" in findings[0].reason


def test_detect_images_in_use_flags_a_bare_container() -> None:
    probe = StubUsageProbe(containers={"sdo-detector-validator:v0.1.0": ["cranky_euler"]})
    findings = detect_images_in_use(IMAGES, probe)
    assert len(findings) == 1
    assert "validator" in findings[0].reason


def test_detect_images_in_use_collects_every_finding_across_clusters() -> None:
    probe = StubUsageProbe(
        clusters=["assure-w0", "assure-w1"],
        locks={"assure-w1": "pid 999"},
        pods={("assure-w0", "sdo-controller:v0.1.0"): ["sdo/controller-1"]},
    )
    findings = detect_images_in_use(IMAGES, probe)
    assert len(findings) == 2


class StubGitHead:
    def __init__(self, commit: str | None) -> None:
        self._commit = commit

    def head_commit(self, repository: Path) -> str | None:
        return self._commit


class StubBuildRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.ran = False

    def run(self, project_root: Path) -> int:
        self.ran = True
        return self.returncode


class StubInspector:
    def __init__(self, digests: dict[str, ImageDigest] | None = None) -> None:
        self._digests = digests or {}

    def inspect(self, ref: str) -> ImageDigest | None:
        return self._digests.get(ref)


def _digests_for(images: dict[str, str]) -> dict[str, ImageDigest]:
    return {
        ref: ImageDigest(ref=ref, id=f"sha256:{ref}", repo_digests=(f"{ref}@sha256:deadbeef",))
        for ref in images.values()
    }


def test_rebuild_refuses_when_the_working_tree_is_at_a_different_commit(tmp_path: Path) -> None:
    with pytest.raises(CommitMismatchError, match="deadbeef"):
        rebuild_v010_images(
            commit="deadbeef",
            project_root=tmp_path,
            usage_probe=StubUsageProbe(),
            git_head=StubGitHead("othercommit"),
            build_runner=StubBuildRunner(),
            inspector=StubInspector(),
            images=IMAGES,
        )


def test_rebuild_refuses_when_any_lane_is_using_the_images(tmp_path: Path) -> None:
    build_runner = StubBuildRunner()
    with pytest.raises(RebuildRefusedError):
        rebuild_v010_images(
            commit="abc123",
            project_root=tmp_path,
            usage_probe=StubUsageProbe(clusters=["assure-w0"], locks={"assure-w0": "pid 1"}),
            git_head=StubGitHead("abc123"),
            build_runner=build_runner,
            inspector=StubInspector(),
            images=IMAGES,
        )
    assert not build_runner.ran  # detection runs before the build


def test_rebuild_force_bypasses_a_detected_finding_but_not_the_commit_check(tmp_path: Path) -> None:
    build_runner = StubBuildRunner()
    result = rebuild_v010_images(
        commit="abc123",
        project_root=tmp_path,
        usage_probe=StubUsageProbe(clusters=["assure-w0"], locks={"assure-w0": "pid 1"}),
        git_head=StubGitHead("abc123"),
        build_runner=build_runner,
        inspector=StubInspector(_digests_for(IMAGES)),
        images=IMAGES,
        force=True,
        now=1_700_000_000.0,
    )
    assert build_runner.ran
    assert result.commit == "abc123"
    assert set(result.digests) == set(IMAGES)


def test_rebuild_records_every_images_digest(tmp_path: Path) -> None:
    result = rebuild_v010_images(
        commit="abc123",
        project_root=tmp_path,
        usage_probe=StubUsageProbe(),
        git_head=StubGitHead("abc123"),
        build_runner=StubBuildRunner(),
        inspector=StubInspector(_digests_for(IMAGES)),
        images=IMAGES,
        now=1_700_000_000.0,
    )
    assert result.digests["controller"].id == "sha256:sdo-controller:v0.1.0"
    assert result.digests["validator"].repo_digests == ("sdo-detector-validator:v0.1.0@sha256:deadbeef",)

    out = write_digest_record(tmp_path / "digests.json", result)
    import json

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["commit"] == "abc123"
    assert data["images"]["controller"]["id"] == "sha256:sdo-controller:v0.1.0"


def test_rebuild_raises_when_the_build_subprocess_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="exited 1"):
        rebuild_v010_images(
            commit="abc123",
            project_root=tmp_path,
            usage_probe=StubUsageProbe(),
            git_head=StubGitHead("abc123"),
            build_runner=StubBuildRunner(returncode=1),
            inspector=StubInspector(),
            images=IMAGES,
        )


def test_rebuild_raises_when_a_built_image_cannot_be_inspected(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="could not inspect"):
        rebuild_v010_images(
            commit="abc123",
            project_root=tmp_path,
            usage_probe=StubUsageProbe(),
            git_head=StubGitHead("abc123"),
            build_runner=StubBuildRunner(),
            inspector=StubInspector({}),  # nothing inspects successfully
            images=IMAGES,
        )
