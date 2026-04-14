"""Tests for validate_compose_tool."""

from app_operator_dspy.tools.agent_tools import validate_compose_tool


class TestValidateComposeTool:
    def test_valid_compose_with_image(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  web:\n    image: nginx\n")

        result = validate_compose_tool(str(compose))

        assert "valid" in result
        assert "1 services" in result

    def test_missing_file(self):
        result = validate_compose_tool("/nonexistent/docker-compose.yml")
        assert "Error reading" in result

    def test_invalid_yaml(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  web:\n    - invalid: [broken")

        result = validate_compose_tool(str(compose))

        assert "Invalid YAML" in result

    def test_missing_build_context(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  web:\n    build: ./nonexistent\n")

        result = validate_compose_tool(str(compose))

        assert "does not exist" in result

    def test_valid_build_context(self, tmp_path):
        ctx = tmp_path / "web"
        ctx.mkdir()
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  web:\n    build: ./web\n")

        result = validate_compose_tool(str(compose))

        assert "valid" in result

    def test_missing_dockerfile_in_build_dict(self, tmp_path):
        ctx = tmp_path / "web"
        ctx.mkdir()
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("services:\n  web:\n    build:\n      context: ./web\n      dockerfile: Custom.Dockerfile\n")

        result = validate_compose_tool(str(compose))

        assert "not found" in result

    def test_no_services_key(self, tmp_path):
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("version: '3'\n")

        result = validate_compose_tool(str(compose))

        assert "No 'services' key" in result

    def test_has_validate_compose_name(self):
        assert validate_compose_tool.__name__ == "validate_compose"
