from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"

if not BENCH_ROOT.exists():
    pytest.skip(
        "bench/sregym submodule not checked out — skipping source_deploy tests",
        allow_module_level=True,
    )

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

sregym_paths = importlib.import_module("sregym.paths")
source_deploy = importlib.import_module("sregym.service.source_deploy")

TARGET_MICROSERVICES = sregym_paths.TARGET_MICROSERVICES
ASTRONOMY_SHOP_METADATA = sregym_paths.ASTRONOMY_SHOP_METADATA
BLUEPRINT_HOTEL_RES_METADATA = sregym_paths.BLUEPRINT_HOTEL_RES_METADATA
FLEET_CAST_METADATA = sregym_paths.FLEET_CAST_METADATA
HOTEL_RES_METADATA = sregym_paths.HOTEL_RES_METADATA
SOCIAL_NETWORK_METADATA = sregym_paths.SOCIAL_NETWORK_METADATA

_BUILT_IMAGES = source_deploy._BUILT_IMAGES
_SOCIAL_MICROSERVICE_CHARTS = source_deploy._SOCIAL_MICROSERVICE_CHARTS
all_declared_apps = source_deploy.all_declared_apps
plan_for_app = source_deploy.plan_for_app


class _FakeApp:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture(autouse=True)
def _clear_source_deploy_cache():
    _BUILT_IMAGES.clear()
    yield
    _BUILT_IMAGES.clear()


def test_source_deploy_registry_covers_all_registered_apps():
    declared_apps = {
        yaml.safe_load(path.read_text(encoding="utf-8"))["Name"]
        for path in (
            ASTRONOMY_SHOP_METADATA,
            HOTEL_RES_METADATA,
            SOCIAL_NETWORK_METADATA,
            FLEET_CAST_METADATA,
            BLUEPRINT_HOTEL_RES_METADATA,
        )
    }

    assert all_declared_apps() == declared_apps


def test_plan_for_app_requires_kind_cluster_name(monkeypatch):
    monkeypatch.delenv("SREGYM_KIND_CLUSTER_NAME", raising=False)

    with pytest.raises(RuntimeError, match="SREGYM_KIND_CLUSTER_NAME"):
        with plan_for_app(_FakeApp("Hotel Reservation")):
            pass


def test_hotel_source_plan_generates_overlay_outside_source_tree(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setenv("SREGYM_KIND_CLUSTER_NAME", "kind-src-test")
    monkeypatch.setattr("sregym.service.source_deploy._run_command", lambda command: commands.append(command))

    with plan_for_app(_FakeApp("Hotel Reservation")) as plan:
        assert plan.manifest_path is not None
        assert plan.manifest_path.name == "overlay"
        assert TARGET_MICROSERVICES not in plan.manifest_path.parents
        assert (plan.manifest_path.parent / "base").is_dir()
        base_kustomization_path = plan.manifest_path.parent / "base" / "kustomization.yaml"
        base_kustomization = yaml.safe_load(base_kustomization_path.read_text(encoding="utf-8"))

        kustomization_path = plan.manifest_path / "kustomization.yaml"
        kustomization = yaml.safe_load(kustomization_path.read_text(encoding="utf-8"))

        assert kustomization["resources"] == ["../base"]
        assert kustomization["images"] == [
            {
                "name": "yinfangchen/hotelreservation",
                "newName": "yinfangchen/hotelreservation",
                "newTag": "sregym-src-hotel-reservation-kind-src-test",
            }
        ]
        assert "frontend/frontend-deployment.yaml" in base_kustomization["resources"]
        assert "rate/mongodb-rate-deployment.yaml" in base_kustomization["resources"]

    assert commands == [
        [
            "docker",
            "build",
            "-t",
            "yinfangchen/hotelreservation:sregym-src-hotel-reservation-kind-src-test",
            "-f",
            str(TARGET_MICROSERVICES / "hotelReservation" / "Dockerfile"),
            str(TARGET_MICROSERVICES / "hotelReservation"),
        ],
        [
            "kind",
            "load",
            "docker-image",
            "yinfangchen/hotelreservation:sregym-src-hotel-reservation-kind-src-test",
            "--name",
            "kind-src-test",
        ],
    ]


def test_social_source_plan_generates_expected_helm_overrides(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setenv("SREGYM_KIND_CLUSTER_NAME", "kind-social")
    monkeypatch.setattr("sregym.service.source_deploy._run_command", lambda command: commands.append(command))

    with plan_for_app(_FakeApp("Social Network"), node_architectures={"amd64"}) as plan:
        assert plan.manifest_path is None
        assert plan.helm_extra_args
        overrides = list(plan.helm_extra_args)

    tag = "sregym-src-social-network-kind-social"
    for chart in _SOCIAL_MICROSERVICE_CHARTS:
        assert f"{chart}.container.image=deathstarbench/social-network-microservices" in overrides
        assert f"{chart}.container.imageVersion={tag}" in overrides
    assert "nginx-thrift.container.image=yg397/openresty-thrift" in overrides
    assert f"nginx-thrift.container.imageVersion={tag}" in overrides
    assert "media-frontend.container.image=yg397/media-frontend" in overrides
    assert f"media-frontend.container.imageVersion={tag}" in overrides

    assert commands == [
        [
            "docker",
            "build",
            "-t",
            f"deathstarbench/social-network-microservices:{tag}",
            str(TARGET_MICROSERVICES / "socialNetwork"),
        ],
        [
            "kind",
            "load",
            "docker-image",
            f"deathstarbench/social-network-microservices:{tag}",
            "--name",
            "kind-social",
        ],
        [
            "docker",
            "build",
            "-t",
            f"yg397/openresty-thrift:{tag}",
            "-f",
            str(TARGET_MICROSERVICES / "socialNetwork" / "docker" / "openresty-thrift" / "xenial" / "Dockerfile"),
            str(TARGET_MICROSERVICES / "socialNetwork" / "docker" / "openresty-thrift"),
        ],
        [
            "kind",
            "load",
            "docker-image",
            f"yg397/openresty-thrift:{tag}",
            "--name",
            "kind-social",
        ],
        [
            "docker",
            "build",
            "-t",
            f"yg397/media-frontend:{tag}",
            "-f",
            str(TARGET_MICROSERVICES / "socialNetwork" / "docker" / "media-frontend" / "xenial" / "Dockerfile"),
            str(TARGET_MICROSERVICES / "socialNetwork" / "docker" / "media-frontend"),
        ],
        [
            "kind",
            "load",
            "docker-image",
            f"yg397/media-frontend:{tag}",
            "--name",
            "kind-social",
        ],
    ]


def test_social_source_plan_uses_arm_media_dockerfile(monkeypatch):
    commands: list[list[str]] = []
    monkeypatch.setenv("SREGYM_KIND_CLUSTER_NAME", "kind-social-arm")
    monkeypatch.setattr("sregym.service.source_deploy._run_command", lambda command: commands.append(command))

    with plan_for_app(_FakeApp("Social Network"), node_architectures={"arm64"}):
        pass

    media_build_command = commands[4]
    assert media_build_command[5].endswith("docker/media-frontend/xenial/Dockerfile.arm64")
