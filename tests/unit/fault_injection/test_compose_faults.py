"""Tests for Docker Compose fault implementations."""

import random

import pytest

from app_operator.fault_injection.compose_faults import (
    COMPOSE_FAULTS,
    ComposeFaultInjector,
)
from app_operator.fault_injection.models import FaultCategory, FaultResult, FaultSeverity

from hypothesis import given, settings
from hypothesis import strategies as st


@pytest.fixture
def hotel_compose():
    """Simplified hotelReservation docker-compose structure."""
    return {
        "version": "3",
        "services": {
            "frontend": {
                "image": "hotel_reserv_frontend:latest",
                "ports": ["5000:5000"],
                "environment": [
                    "TLS=false",
                    "GC=off",
                    "JAEGER_SAMPLE_RATIO=0.01",
                ],
                "depends_on": ["consul"],
                "networks": ["hotel-net"],
            },
            "geo": {
                "image": "hotel_reserv_geo:latest",
                "ports": ["8083:8083"],
                "environment": {
                    "DB_HOST": "mongodb-geo",
                    "DB_PASSWORD": "geopassword",
                },
                "depends_on": ["mongodb-geo", "consul"],
                "networks": ["hotel-net"],
            },
            "rate": {
                "image": "hotel_reserv_rate:latest",
                "ports": ["8084:8084"],
                "environment": [
                    "MEMCACHED_HOST=memcached-rate",
                    "MONGO_HOST=mongodb-rate",
                ],
                "depends_on": ["mongodb-rate", "memcached-rate"],
                "networks": ["hotel-net"],
            },
            "consul": {
                "image": "consul:1.12",
                "ports": ["8500:8500"],
                "networks": ["hotel-net"],
            },
            "mongodb-geo": {
                "image": "mongo:4.4",
                "volumes": ["geo-data:/data/db"],
                "networks": ["hotel-net"],
            },
            "mongodb-rate": {
                "image": "mongo:4.4",
                "volumes": ["rate-data:/data/db"],
                "networks": ["hotel-net"],
            },
            "memcached-rate": {
                "image": "memcached:1.6",
                "ports": ["11211:11211"],
                "networks": ["hotel-net"],
            },
            "jaeger": {
                "image": "jaegertracing/all-in-one:1.29",
                "ports": ["16686:16686", "6831:6831/udp"],
                "networks": ["hotel-net"],
            },
        },
        "networks": {
            "hotel-net": {"driver": "bridge"},
        },
        "volumes": {
            "geo-data": None,
            "rate-data": None,
        },
    }


@pytest.fixture
def injector():
    """ComposeFaultInjector with fixed seed."""
    return ComposeFaultInjector(rng=random.Random(42))


def _get_fault(fault_id: str):
    """Get a fault by ID from the catalog."""
    for f in COMPOSE_FAULTS:
        if f.fault_id == fault_id:
            return f
    raise KeyError(f"Fault {fault_id} not found")


class TestComposeFaultsCatalog:
    def test_catalog_has_22_faults(self):
        assert len(COMPOSE_FAULTS) == 22

    def test_unique_ids(self):
        ids = [f.fault_id for f in COMPOSE_FAULTS]
        assert len(ids) == len(set(ids))

    def test_all_categories_represented(self):
        categories = {f.category for f in COMPOSE_FAULTS}
        assert FaultCategory.MISCONFIGURATION in categories
        assert FaultCategory.SECURITY in categories
        assert FaultCategory.METASTABLE in categories
        assert FaultCategory.CORRELATED in categories
        assert FaultCategory.INFRASTRUCTURE in categories

    def test_all_have_descriptions(self):
        for f in COMPOSE_FAULTS:
            assert f.description, f"{f.fault_id} has no description"


class TestMisconfigurationFaults:
    def test_wrong_port_mapping(self, hotel_compose, injector):
        fault = _get_fault("MISC-001")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert result.target_service == "frontend"
        # Port should have changed
        port = hotel_compose["services"]["frontend"]["ports"][0]
        assert port != "5000:5000"
        assert ":5000" in port  # Container port preserved

    def test_missing_env_var_list(self, hotel_compose, injector):
        fault = _get_fault("MISC-002")
        orig_count = len(hotel_compose["services"]["frontend"]["environment"])
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert len(hotel_compose["services"]["frontend"]["environment"]) == orig_count - 1

    def test_missing_env_var_dict(self, hotel_compose, injector):
        fault = _get_fault("MISC-002")
        orig_count = len(hotel_compose["services"]["geo"]["environment"])
        result = injector.inject(fault, hotel_compose, "geo")
        assert result.success
        assert len(hotel_compose["services"]["geo"]["environment"]) == orig_count - 1

    def test_wrong_image_tag(self, hotel_compose, injector):
        fault = _get_fault("MISC-003")
        result = injector.inject(fault, hotel_compose, "consul")
        assert result.success
        assert "nonexistent" in hotel_compose["services"]["consul"]["image"]

    def test_wrong_entrypoint(self, hotel_compose, injector):
        fault = _get_fault("MISC-004")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert hotel_compose["services"]["frontend"]["entrypoint"] == "/bin/nonexistent-cmd"

    def test_bad_volume_mount(self, hotel_compose, injector):
        fault = _get_fault("MISC-005")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        volumes = hotel_compose["services"]["frontend"]["volumes"]
        assert any("nonexistent" in str(v) for v in volumes)

    def test_duplicate_port_conflict(self, hotel_compose, injector):
        fault = _get_fault("MISC-006")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        # Some other service should now have a conflicting port
        assert "conflicts_with" in result.modified_fields


class TestSecurityFaults:
    def test_removed_auth_config(self, hotel_compose, injector):
        fault = _get_fault("SEC-001")
        result = injector.inject(fault, hotel_compose, "geo")
        assert result.success
        # DB_PASSWORD should be removed
        env = hotel_compose["services"]["geo"]["environment"]
        assert "DB_PASSWORD" not in env

    def test_removed_auth_no_auth_vars(self, hotel_compose, injector):
        fault = _get_fault("SEC-001")
        result = injector.inject(fault, hotel_compose, "consul")
        assert not result.success

    def test_exposed_debug_port(self, hotel_compose, injector):
        fault = _get_fault("SEC-002")
        orig_ports = len(hotel_compose["services"]["frontend"].get("ports", []))
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert len(hotel_compose["services"]["frontend"]["ports"]) == orig_ports + 1

    def test_privileged_container(self, hotel_compose, injector):
        fault = _get_fault("SEC-003")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert hotel_compose["services"]["frontend"]["privileged"] is True


class TestMetastableFaults:
    def test_resource_limit_cpu(self, hotel_compose, injector):
        fault = _get_fault("META-001")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        limits = hotel_compose["services"]["frontend"]["deploy"]["resources"]["limits"]
        assert limits["cpus"] == "0.01"

    def test_resource_limit_memory(self, hotel_compose, injector):
        fault = _get_fault("META-002")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        limits = hotel_compose["services"]["frontend"]["deploy"]["resources"]["limits"]
        assert limits["memory"] == "4m"

    def test_restart_loop_trigger(self, hotel_compose, injector):
        fault = _get_fault("META-003")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        svc = hotel_compose["services"]["frontend"]
        assert svc["restart"] == "always"
        assert "exit 1" in svc["command"]

    def test_slow_healthcheck(self, hotel_compose, injector):
        fault = _get_fault("META-004")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        hc = hotel_compose["services"]["frontend"]["healthcheck"]
        assert hc["test"] == ["CMD", "false"]

    def test_tmpfs_too_small(self, hotel_compose, injector):
        fault = _get_fault("META-005")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert "size=1k" in hotel_compose["services"]["frontend"]["tmpfs"]


class TestCorrelatedFaults:
    def test_remove_dependency_list(self, hotel_compose, injector):
        fault = _get_fault("CORR-001")
        orig_deps = len(hotel_compose["services"]["rate"]["depends_on"])
        result = injector.inject(fault, hotel_compose, "rate")
        assert result.success
        assert len(hotel_compose["services"]["rate"]["depends_on"]) == orig_deps - 1

    def test_remove_dependency_dict(self, hotel_compose, injector):
        fault = _get_fault("CORR-001")
        # Convert depends_on to dict format
        hotel_compose["services"]["rate"]["depends_on"] = {
            "mongodb-rate": {"condition": "service_started"},
            "memcached-rate": {"condition": "service_started"},
        }
        result = injector.inject(fault, hotel_compose, "rate")
        assert result.success
        assert len(hotel_compose["services"]["rate"]["depends_on"]) == 1

    def test_break_shared_database_dict(self, hotel_compose, injector):
        fault = _get_fault("CORR-002")
        result = injector.inject(fault, hotel_compose, "geo")
        assert result.success
        env = hotel_compose["services"]["geo"]["environment"]
        assert env["DB_HOST"] == "broken-host-sds-fault:65535"

    def test_cascading_port_change(self, hotel_compose, injector):
        fault = _get_fault("CORR-003")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        port = hotel_compose["services"]["frontend"]["ports"][0]
        assert "6000" in port  # 5000 + 1000

    def test_remove_shared_network(self, hotel_compose, injector):
        fault = _get_fault("CORR-004")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert "hotel-net" not in hotel_compose.get("networks", {})

    def test_remove_shared_volume(self, hotel_compose, injector):
        fault = _get_fault("CORR-005")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        # At least one volume should be removed
        assert len(hotel_compose.get("volumes", {})) < 2


class TestInfrastructureFaults:
    def test_dns_override(self, hotel_compose, injector):
        fault = _get_fault("INFRA-001")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        extra_hosts = hotel_compose["services"]["frontend"]["extra_hosts"]
        assert any("127.0.0.1" in h for h in extra_hosts)

    def test_init_failure(self, hotel_compose, injector):
        fault = _get_fault("INFRA-002")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        svc = hotel_compose["services"]["frontend"]
        assert svc["entrypoint"] == "sh"
        assert "exit 1" in svc["command"]

    def test_read_only_rootfs(self, hotel_compose, injector):
        fault = _get_fault("INFRA-003")
        result = injector.inject(fault, hotel_compose, "frontend")
        assert result.success
        assert hotel_compose["services"]["frontend"]["read_only"] is True


class TestAutoServiceSelection:
    def test_auto_selects_service_with_ports(self, hotel_compose, injector):
        fault = _get_fault("MISC-001")
        result = injector.inject(fault, hotel_compose)
        assert result.success
        # Should have targeted a service with ports
        assert result.target_service in ["frontend", "geo", "rate", "consul", "memcached-rate", "jaeger"]

    def test_auto_selects_service_with_env(self, hotel_compose, injector):
        fault = _get_fault("MISC-002")
        result = injector.inject(fault, hotel_compose)
        assert result.success
        assert result.target_service in ["frontend", "geo", "rate"]

    def test_no_applicable_service(self, injector):
        fault = _get_fault("MISC-001")
        compose = {"services": {"worker": {"image": "worker:1"}}}
        result = injector.inject(fault, compose)
        assert not result.success
        assert "No applicable services" in result.error_message


class TestComposeFaultInjectorDispatch:
    def test_unknown_fault_id(self, hotel_compose, injector):
        from app_operator.fault_injection.models import Fault

        fault = Fault(
            fault_id="UNKNOWN-999",
            name="unknown",
            category=FaultCategory.MISCONFIGURATION,
            severity=FaultSeverity.LOW,
            description="Unknown fault",
        )
        result = injector.inject(fault, hotel_compose, "frontend")
        assert not result.success
        assert "No handler" in result.error_message


# ---------------------------------------------------------------------------
# Strategy for property-based tests
# ---------------------------------------------------------------------------
@st.composite
def compose_data_strategy(draw):
    """Generate a minimal docker-compose dict with varying services."""
    num_services = draw(st.integers(min_value=0, max_value=8))
    services = {}
    for i in range(num_services):
        name = f"svc{i}"
        config = {}
        # Randomly add ports
        has_ports = draw(st.booleans())
        if has_ports:
            host = draw(st.integers(min_value=1024, max_value=60000))
            config["ports"] = [f"{host}:{host}"]
        # Randomly add environment
        has_env = draw(st.booleans())
        if has_env:
            config["environment"] = [f"KEY{i}=val{i}"]
        # Randomly add an image
        has_image = draw(st.booleans())
        if has_image:
            config["image"] = draw(st.sampled_from(["nginx:latest", "postgres:14", "redis:7", "myapp:1.0"]))
        services[name] = config
    return {"services": services}


class TestComposeFaultInjectionProperty:
    """Property-based tests for ComposeFaultInjector over generated compose topologies."""

    @given(compose_data=compose_data_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_inject_result_always_has_fault_id(self, compose_data):
        """Injecting any fault always returns a result whose fault_id matches the fault."""
        injector = ComposeFaultInjector(rng=random.Random(0))
        fault = COMPOSE_FAULTS[0]
        result = injector.inject(fault, compose_data)
        assert isinstance(result, FaultResult)
        assert result.fault.fault_id == fault.fault_id

    @given(compose_data=compose_data_strategy())
    @settings(max_examples=50, deadline=2000)
    def test_failed_injection_has_error_message(self, compose_data):
        """When injection fails, the result must carry a non-empty error message."""
        injector = ComposeFaultInjector(rng=random.Random(0))
        fault = COMPOSE_FAULTS[0]
        result = injector.inject(fault, compose_data)
        if not result.success:
            assert result.error_message is not None
            assert len(result.error_message) > 0

    @given(
        compose_data=compose_data_strategy(),
        fault_index=st.integers(min_value=0, max_value=len(COMPOSE_FAULTS) - 1),
    )
    @settings(max_examples=50, deadline=2000)
    def test_inject_never_raises_exception(self, compose_data, fault_index):
        """inject() must never raise an uncaught exception for any compose topology."""
        injector = ComposeFaultInjector(rng=random.Random(0))
        fault = COMPOSE_FAULTS[fault_index]
        try:
            result = injector.inject(fault, compose_data)
            assert isinstance(result, FaultResult)
        except Exception as exc:
            pytest.fail(f"inject() raised {type(exc).__name__} for fault {fault.fault_id}: {exc}")
