import pytest
from app_operator.config import (
    load_config,
    Config,
    UnrecognizedSectionError,
    UnrecognizedFieldError,
)
from app_operator.exceptions import ConfigurationError


def test_load_config_valid_config(tmp_path):
    """Test loading a valid configuration file."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "gemini"
model = "gemini-1.5-pro"

[operator]
interval = 60
monitoring_max_iters = 10
deployment_max_iters = 3
""")

    config = load_config(str(tmp_path))
    assert config.agent.provider == "gemini"
    assert config.agent.model == "gemini-1.5-pro"
    assert config.operator.interval == 60
    assert config.operator.monitoring_max_iters == 10
    assert config.operator.deployment_max_iters == 3


def test_load_config_defaults(tmp_path):
    """Test loading config with defaults when no file exists."""
    # Use explicit non-existent config path to avoid picking up root sds.toml
    config = load_config(str(tmp_path), config_path=str(tmp_path / "nonexistent.toml"))
    assert config.agent.provider == "codex"
    assert config.agent.model is None
    assert config.operator.interval == 30
    assert config.operator.monitoring_max_iters == 5
    assert config.operator.deployment_max_iters == 20


def test_load_config_partial_config(tmp_path):
    """Test loading config with only some fields specified."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "claude"

[operator]
interval = 45
""")

    config = load_config(str(tmp_path))
    assert config.agent.provider == "claude"
    assert config.agent.model == "gemini-2.5-flash"  # inherited from root sds.toml
    assert config.operator.interval == 45
    assert config.operator.monitoring_max_iters == 5  # default
    assert config.operator.deployment_max_iters == 20  # default


def test_config_unrecognized_section(tmp_path):
    """Test that unrecognized top-level sections raise an error."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "gemini"

[operator]
interval = 30

[unknown_section]
some_field = "value"
""")

    with pytest.raises(UnrecognizedSectionError) as exc_info:
        load_config(str(tmp_path))

    assert "unknown_section" in str(exc_info.value)
    assert "Unrecognized section(s)" in str(exc_info.value)


def test_config_multiple_unrecognized_sections(tmp_path):
    """Test that multiple unrecognized sections are all reported."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "gemini"

[operator]
interval = 30

[section1]
field = "value"

[section2]
field = "value"
""")

    with pytest.raises(UnrecognizedSectionError) as exc_info:
        load_config(str(tmp_path))

    error_msg = str(exc_info.value)
    assert "section1" in error_msg
    assert "section2" in error_msg
    assert "Unrecognized section(s)" in error_msg


def test_config_unrecognized_field_in_agent(tmp_path):
    """Test that unrecognized fields in [agent] section raise an error."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "gemini"
unknown_field = "value"
""")

    with pytest.raises(UnrecognizedFieldError) as exc_info:
        load_config(str(tmp_path))

    assert "unknown_field" in str(exc_info.value)
    assert "[agent] section" in str(exc_info.value)
    assert "Unrecognized field(s)" in str(exc_info.value)


def test_config_unrecognized_field_in_operator(tmp_path):
    """Test that unrecognized fields in [operator] section raise an error."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[operator]
interval = 30
unknown_field = "value"
""")

    with pytest.raises(UnrecognizedFieldError) as exc_info:
        load_config(str(tmp_path))

    assert "unknown_field" in str(exc_info.value)
    assert "[operator] section" in str(exc_info.value)
    assert "Unrecognized field(s)" in str(exc_info.value)


def test_config_multiple_unrecognized_fields(tmp_path):
    """Test that multiple unrecognized fields are all reported."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "gemini"
unknown_field1 = "value1"
unknown_field2 = "value2"
""")

    with pytest.raises(UnrecognizedFieldError) as exc_info:
        load_config(str(tmp_path))

    error_msg = str(exc_info.value)
    assert "unknown_field1" in error_msg
    assert "unknown_field2" in error_msg
    assert "Unrecognized field(s)" in error_msg


def test_config_unrecognized_section_and_field(tmp_path):
    """Test that both unrecognized section and field errors can occur."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "gemini"
unknown_field = "value"

[unknown_section]
field = "value"
""")

    # Should raise UnrecognizedSectionError first (checked before field validation)
    with pytest.raises(UnrecognizedSectionError) as exc_info:
        load_config(str(tmp_path))

    assert "unknown_section" in str(exc_info.value)


def test_config_from_dict_valid():
    """Test Config.from_dict with valid data."""
    data = {
        "agent": {"provider": "gemini", "model": "gemini-1.5-pro"},
        "operator": {"interval": 60, "monitoring_max_iters": 10}
    }
    config = Config.from_dict(data)
    assert config.agent.provider == "gemini"
    assert config.agent.model == "gemini-1.5-pro"
    assert config.operator.interval == 60
    assert config.operator.monitoring_max_iters == 10


def test_config_from_dict_unrecognized_section():
    """Test Config.from_dict raises error for unrecognized section."""
    data = {
        "agent": {"provider": "gemini"},
        "operator": {"interval": 30},
        "unknown": {"field": "value"}
    }
    with pytest.raises(UnrecognizedSectionError) as exc_info:
        Config.from_dict(data)
    assert "unknown" in str(exc_info.value)


def test_config_from_dict_unrecognized_field_agent():
    """Test Config.from_dict raises error for unrecognized field in agent."""
    data = {
        "agent": {"provider": "gemini", "unknown_field": "value"},
        "operator": {"interval": 30}
    }
    with pytest.raises(UnrecognizedFieldError) as exc_info:
        Config.from_dict(data)
    assert "unknown_field" in str(exc_info.value)
    assert "[agent]" in str(exc_info.value)


def test_config_from_dict_unrecognized_field_operator():
    """Test Config.from_dict raises error for unrecognized field in operator."""
    data = {
        "agent": {"provider": "gemini"},
        "operator": {"interval": 30, "unknown_field": "value"}
    }
    with pytest.raises(UnrecognizedFieldError) as exc_info:
        Config.from_dict(data)
    assert "unknown_field" in str(exc_info.value)
    assert "[operator]" in str(exc_info.value)


def test_config_empty_sections(tmp_path):
    """Test that empty sections are handled correctly."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]

[operator]
""")

    config = load_config(str(tmp_path))
    assert config.agent.provider == "gemini"  # inherited from root sds.toml
    assert config.operator.interval == 30  # default


def test_config_only_agent_section(tmp_path):
    """Test config with only agent section."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
provider = "claude"
""")

    config = load_config(str(tmp_path))
    assert config.agent.provider == "claude"
    assert config.operator.interval == 30  # default


def test_config_only_operator_section(tmp_path):
    """Test config with only operator section."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[operator]
interval = 90
""")

    config = load_config(str(tmp_path))
    assert config.agent.provider == "gemini"  # inherited from root sds.toml
    assert config.operator.interval == 90


def test_exception_hierarchy():
    """Test that config exceptions properly extend ConfigurationError."""
    # Verify the inheritance hierarchy
    assert issubclass(UnrecognizedSectionError, ConfigurationError)
    assert issubclass(UnrecognizedFieldError, ConfigurationError)


def test_unrecognized_section_caught_as_configuration_error(tmp_path):
    """Test that UnrecognizedSectionError can be caught as ConfigurationError."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[unknown_section]
field = "value"
""")

    with pytest.raises(ConfigurationError):
        load_config(str(tmp_path))


def test_unrecognized_field_caught_as_configuration_error(tmp_path):
    """Test that UnrecognizedFieldError can be caught as ConfigurationError."""
    config_file = tmp_path / "sds.toml"
    config_file.write_text("""[agent]
unknown_field = "value"
""")

    with pytest.raises(ConfigurationError):
        load_config(str(tmp_path))
