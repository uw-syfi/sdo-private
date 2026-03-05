"""Tests for SDSPromptAdapter."""

import pytest

from app_operator.gepa.adapter import SDSPromptAdapter


@pytest.fixture
def adapter_with_template(tmp_path):
    """Create an SDSPromptAdapter with a deployer/system.jinja2 template."""
    (tmp_path / "deployer").mkdir()
    (tmp_path / "deployer" / "system.jinja2").write_text("Deploy on {{ platform }}.")
    return SDSPromptAdapter(tmp_path)


class TestSDSPromptAdapterValidation:
    """Test validate_template()."""

    def test_accepts_interpolation_variable(self, adapter_with_template):
        content = "Deploy on {{ platform }} please."
        assert adapter_with_template.validate_template("deployer/system.jinja2", content)

    def test_rejects_missing_variable(self, adapter_with_template):
        content = "Deploy stuff without any variable reference."
        assert not adapter_with_template.validate_template("deployer/system.jinja2", content)

    def test_rejects_invalid_jinja2(self, adapter_with_template):
        content = "{{ platform }} {% if unclosed"
        assert not adapter_with_template.validate_template("deployer/system.jinja2", content)

    def test_rejects_unknown_template(self, tmp_path):
        adapter = SDSPromptAdapter(tmp_path)
        assert not adapter.validate_template("unknown.jinja2", "content")
