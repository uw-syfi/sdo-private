from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "bench" / "sregym"
APP_REGISTRY_PATH = BENCH_ROOT / "sregym" / "service" / "apps" / "app_registry.py"

if not APP_REGISTRY_PATH.exists():
    pytest.skip(
        "bench/sregym submodule not checked out — skipping app_registry tests",
        allow_module_level=True,
    )


def _module(name: str) -> Any:
    return cast("Any", sys.modules.setdefault(name, types.ModuleType(name)))


def _import_app_registry() -> Any:
    _module("sregym")
    _module("sregym.service")
    _module("sregym.service.apps")

    paths_mod = _module("sregym.paths")
    paths_mod.ASTRONOMY_SHOP_METADATA = Path("/tmp/astronomy-shop.json")
    paths_mod.HOTEL_RES_METADATA = Path("/tmp/hotel-reservation.json")
    paths_mod.SOCIAL_NETWORK_METADATA = Path("/tmp/social-network.json")
    paths_mod.TRAIN_TICKET_METADATA = Path("/tmp/train-ticket.json")
    paths_mod.FLEET_CAST_METADATA = Path("/tmp/fleet-cast.json")
    paths_mod.BLUEPRINT_HOTEL_RES_METADATA = Path("/tmp/blueprint-hotel-reservation.json")

    app_names_mod = _module("sregym.service.apps.app_names")

    class _AppName:
        ASTRONOMY_SHOP = types.SimpleNamespace(value="Astronomy Shop")
        HOTEL_RESERVATION = types.SimpleNamespace(value="Hotel Reservation")
        SOCIAL_NETWORK = types.SimpleNamespace(value="Social Network")
        TRAIN_TICKET = types.SimpleNamespace(value="Train Ticket")
        FLEET_CAST = types.SimpleNamespace(value="Fleet Cast")
        BLUEPRINT_HOTEL_RESERVATION = types.SimpleNamespace(value="Blueprint Hotel Reservation")

    app_names_mod.AppName = _AppName

    for module_name, class_name in (
        ("sregym.service.apps.astronomy_shop", "AstronomyShop"),
        ("sregym.service.apps.fleet_cast", "FleetCast"),
        ("sregym.service.apps.flight_ticket", "FlightTicket"),
        ("sregym.service.apps.hotel_reservation", "HotelReservation"),
        ("sregym.service.apps.social_network", "SocialNetwork"),
        ("sregym.service.apps.blueprint_hotel_reservation", "BlueprintHotelReservation"),
        ("sregym.service.apps.train_ticket", "TrainTicket"),
    ):
        mod = _module(module_name)
        setattr(mod, class_name, type(class_name, (), {}))

    helm_mod = _module("sregym.service.helm")
    helm_mod.Helm = type("Helm", (), {})

    spec = importlib.util.spec_from_file_location("bench_sregym_app_registry", APP_REGISTRY_PATH)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_app_registry_supports_train_ticket():
    mod = _import_app_registry()
    registry = mod.AppRegistry()

    app = registry.get_app_instance("Train Ticket")

    assert app.__class__.__name__ == "TrainTicket"
    assert registry.get_app_config_file("Train Ticket").name == "train-ticket.json"
