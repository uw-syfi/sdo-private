"""Property-based tests for ComposeManipulator using hypothesis.

These tests encode invariants of the static utility methods on ComposeManipulator
which are pure functions with clear input/output contracts.
"""

import pytest

# Try to import hypothesis, skip tests if not available
try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

    def given(*args, **kwargs):
        return pytest.mark.skip(reason="hypothesis not installed")

    def assume(*args, **kwargs):
        pass

    class DummySettings:
        def __call__(self, *args, **kwargs):
            return pytest.mark.skip(reason="hypothesis not installed")

    class DummyStrategies:
        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    settings = DummySettings()
    st = DummyStrategies()

from app_operator.fault_injection.base import ComposeManipulator

pytestmark = pytest.mark.skipif(
    not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed - install with: uv add --dev hypothesis"
)

VALID_CATEGORIES = {"database", "cache", "frontend", "backend", "proxy", "monitoring"}

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
if HYPOTHESIS_AVAILABLE:

    @st.composite
    def service_config_strategy(draw):
        """Generate a service config dict with optional image key."""
        image = draw(st.one_of(st.none(), st.text(max_size=50)))
        config = {}
        if image is not None:
            config["image"] = image
        return config

    @st.composite
    def compose_data_strategy(draw):
        """Generate a docker-compose data dict with a services section."""
        service_names = draw(
            st.lists(
                st.text(min_size=1, max_size=30).filter(str.isidentifier),
                min_size=0,
                max_size=5,
                unique=True,
            )
        )
        services = {}
        for name in service_names:
            config = draw(service_config_strategy())
            # Optionally add ports and environment
            ports = draw(
                st.lists(
                    st.text(min_size=1, max_size=20),
                    max_size=3,
                )
            )
            env = draw(
                st.one_of(
                    st.just([]),
                    st.lists(st.text(min_size=1, max_size=40), max_size=5),
                    st.dictionaries(
                        st.text(min_size=1, max_size=20).filter(lambda s: "=" not in s),
                        st.text(max_size=20),
                        max_size=5,
                    ),
                )
            )
            if ports:
                config["ports"] = ports
            if env:
                config["environment"] = env
            services[name] = config
        return {"services": services}
else:

    def service_config_strategy():
        pass

    def compose_data_strategy():
        pass


# ---------------------------------------------------------------------------
# classify_service
# ---------------------------------------------------------------------------


class TestClassifyServiceProperties:
    """Property-based tests for ComposeManipulator.classify_service."""

    @given(
        name=st.text(max_size=50),
        config=service_config_strategy(),
    )
    @settings(max_examples=50, deadline=1000)
    def test_always_returns_valid_category(self, name, config):
        """classify_service always returns one of the known category strings."""
        result = ComposeManipulator.classify_service(name, config)
        assert result in VALID_CATEGORIES

    @given(config=service_config_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_redis_name_classified_as_cache(self, config):
        """A service named 'redis' is always classified as cache."""
        result = ComposeManipulator.classify_service("redis", config)
        assert result == "cache"

    @given(config=service_config_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_postgres_name_classified_as_database(self, config):
        """A service named 'postgres' is always classified as database."""
        result = ComposeManipulator.classify_service("postgres", config)
        assert result == "database"

    @given(config=service_config_strategy())
    @settings(max_examples=50, deadline=1000)
    def test_nginx_name_classified_as_proxy(self, config):
        """A service named 'nginx' is always classified as proxy."""
        result = ComposeManipulator.classify_service("nginx", config)
        assert result == "proxy"


# ---------------------------------------------------------------------------
# parse_port_mapping
# ---------------------------------------------------------------------------


class TestParsePortMappingProperties:
    """Property-based tests for ComposeManipulator.parse_port_mapping."""

    @given(
        host=st.integers(min_value=0, max_value=65535),
        container=st.integers(min_value=0, max_value=65535),
    )
    @settings(max_examples=50, deadline=1000)
    def test_host_colon_container_format(self, host, container):
        """'HOST:CONTAINER' format returns correct host and container port integers."""
        result = ComposeManipulator.parse_port_mapping(f"{host}:{container}")
        assert result == {"host": host, "container": container}

    @given(port=st.integers(min_value=0, max_value=65535))
    @settings(max_examples=50, deadline=1000)
    def test_single_port_maps_to_both(self, port):
        """'PORT' format returns equal host and container ports."""
        result = ComposeManipulator.parse_port_mapping(str(port))
        assert result == {"host": port, "container": port}

    @given(
        text=st.text(
            alphabet=st.characters(blacklist_characters="0123456789:"),
            min_size=1,
            max_size=30,
        )
    )
    @settings(max_examples=50, deadline=1000)
    def test_non_matching_returns_none(self, text):
        """Strings containing non-digit, non-colon characters return None."""
        import re

        # These strings can't match \d+:\d+ or ^\d+$ since they contain non-digit chars
        assume(not re.match(r"^(\d+):(\d+)$", text))
        assume(not re.match(r"^\d+$", text))
        result = ComposeManipulator.parse_port_mapping(text)
        assert result is None


# ---------------------------------------------------------------------------
# get_service_ports
# ---------------------------------------------------------------------------


class TestGetServicePortsProperties:
    """Property-based tests for ComposeManipulator.get_service_ports."""

    @given(compose_data=compose_data_strategy(), service=st.text(max_size=30))
    @settings(max_examples=50, deadline=1000)
    def test_always_returns_list(self, compose_data, service):
        """get_service_ports always returns a list, even for missing services."""
        result = ComposeManipulator.get_service_ports(compose_data, service)
        assert isinstance(result, list)

    @given(service=st.text(min_size=1, max_size=30))
    @settings(max_examples=50, deadline=1000)
    def test_missing_services_key_returns_empty_list(self, service):
        """Missing 'services' key returns an empty list."""
        result = ComposeManipulator.get_service_ports({}, service)
        assert result == []


# ---------------------------------------------------------------------------
# get_service_environment normalization
# ---------------------------------------------------------------------------


class TestGetServiceEnvironmentProperties:
    """Property-based tests for ComposeManipulator.get_service_environment."""

    @given(
        keys=st.lists(
            st.text(min_size=1, max_size=20).filter(lambda s: "=" not in s and s.strip()),
            min_size=1,
            max_size=5,
            unique=True,
        ),
        values=st.lists(st.text(max_size=20), min_size=1, max_size=5),
    )
    @settings(max_examples=50, deadline=1000)
    def test_dict_and_list_formats_normalize_to_same_result(self, keys, values):
        """Dict and list environment formats normalize to the same KEY=VALUE pairs."""
        # Build a consistent key-value mapping
        pairs = list(zip(keys, values, strict=False))[: min(len(keys), len(values))]
        assume(pairs)

        env_dict = dict(pairs)
        env_list = [f"{k}={v}" for k, v in pairs]

        service_name = "svc"
        compose_dict = {"services": {service_name: {"environment": env_dict}}}
        compose_list = {"services": {service_name: {"environment": env_list}}}

        result_dict = sorted(ComposeManipulator.get_service_environment(compose_dict, service_name))
        result_list = sorted(ComposeManipulator.get_service_environment(compose_list, service_name))

        assert result_dict == result_list

    @given(compose_data=compose_data_strategy(), service=st.text(max_size=30))
    @settings(max_examples=50, deadline=1000)
    def test_always_returns_list(self, compose_data, service):
        """get_service_environment always returns a list."""
        result = ComposeManipulator.get_service_environment(compose_data, service)
        assert isinstance(result, list)
