"""A fake host for the launch preflight and manifest tests: healthy by default."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from benchmarks.sregym.runner.launch_contract import CONTROLLER_HELP_COMMAND, IMAGE_TRAFFIC_SDK
from benchmarks.sregym.runner.preflight import GB, ImageInfo, ImageVersions
from sdo.controller_install import controller_launch_flags

from pathlib import Path

if TYPE_CHECKING:
    from collections.abc import Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
CODEX_PIN = "0.157.1"
AGENTSHIM_PIN = "0.7.0"


@dataclass
class FakeHost:
    """A healthy host by default; tests break one thing at a time."""

    home: Path
    free: dict[str, int] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    default_free: int = 500 * GB
    docker_root_dir: Path | None = None
    images: dict[str, ImageInfo] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    versions: dict[str, ImageVersions] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    missing_images: set[str] = field(default_factory=set)  # pyright: ignore[reportUnknownVariableType]
    npm_missing: set[str] = field(default_factory=set)  # pyright: ignore[reportUnknownVariableType]
    npm_offline: bool = False
    codex_version: str | None = CODEX_PIN
    packages: dict[str, str] = field(default_factory=lambda: {"agentshim": AGENTSHIM_PIN})
    clusters: list[str] | None = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    nodes: dict[str, list[str]] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    ports: dict[str, int] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    locks: dict[str, str] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    git_answers: dict[tuple[str, ...], str] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    load: tuple[float, float, float] | None = (1.0, 2.0, 3.0)
    probed: list[str] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]
    #: Per-image overrides of what the image prints; an image not listed is built from this checkout.
    controller_help: dict[str, str | None] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]
    traffic_sdk: dict[str, str | None] = field(default_factory=dict)  # pyright: ignore[reportUnknownVariableType]

    def free_bytes(self, path: Path) -> int | None:
        for prefix, value in self.free.items():
            if str(path).startswith(prefix):
                return value
        return self.default_free

    def docker_root(self) -> Path | None:
        return self.docker_root_dir

    def image(self, ref: str) -> ImageInfo | None:
        if ref in self.missing_images:
            return None
        return self.images.get(ref) or ImageInfo(ref=ref, id=f"sha256:{abs(hash(ref)):064x}"[:71])

    def image_versions(self, ref: str, modules: Sequence[str]) -> ImageVersions | None:
        self.probed.append(ref)
        return self.versions.get(ref) or ImageVersions(codex=CODEX_PIN, agentshim=AGENTSHIM_PIN)

    def npm_resolves(self, spec: str) -> bool | None:
        if self.npm_offline:
            return None
        return spec not in self.npm_missing

    def host_codex_version(self) -> str | None:
        return self.codex_version

    def python_package_version(self, name: str) -> str | None:
        return self.packages.get(name)

    def kind_clusters(self) -> list[str] | None:
        return self.clusters

    def kind_nodes(self, cluster: str) -> list[str] | None:
        return self.nodes.get(cluster)

    def control_plane_port(self, cluster: str) -> int | None:
        return self.ports.get(cluster)

    def cluster_lock_owner(self, cluster: str) -> str | None:
        return self.locks.get(cluster)

    def git(self, repository: Path, *args: str) -> str | None:
        return self.git_answers.get(args)

    def load_average(self) -> tuple[float, float, float] | None:
        return self.load

    def image_output(self, ref: str, argv: list[str]) -> str | None:
        if tuple(argv) == CONTROLLER_HELP_COMMAND:
            if ref in self.controller_help:
                return self.controller_help[ref]
            flags = controller_launch_flags(
                controller_namespace="fake-control",
                closeout_state_gate=True,
                max_follow_ups=1,
                late_findings="pull",
                healthy_baseline=True,
            )
            return "\n".join(sorted(flags))
        if argv[:2] == ["sh", "-c"] and IMAGE_TRAFFIC_SDK in argv[2]:
            if ref in self.traffic_sdk:
                return self.traffic_sdk[ref]
            traffic = REPOSITORY_ROOT / "controller" / "sdk" / "traffic"
            return "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(traffic.glob("*.go"))
                if not path.name.endswith("_test.go")
            )
        return None
