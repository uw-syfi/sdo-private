import pytest
from lego_agent.prompts import get_loader, reset_loader


@pytest.fixture
def loader():
    """Fixture that provides a loader and ensures cleanup after test."""
    loader = get_loader()
    yield loader
    reset_loader()


def test_loader_initialization(loader):
    # Verify that the loader is correctly pointing to the templates directory
    assert loader.templates_dir.exists()
    assert (loader.templates_dir / "lego_agent").exists()


def test_lego_agent_system_prompt(loader):
    rendered = loader.render("lego_agent/system.jinja2", qa_pairs=[])
    assert rendered is not None
    # Add assertions based on expected content if known,
    # but at least check it renders.
    assert "Workflow Design Checklist" in rendered


def test_lego_agent_user_prompt(loader):
    rendered = loader.render("lego_agent/user.jinja2", user_prompt="TEST_PROMPT")
    assert "TEST_PROMPT" in rendered
    assert "yaml_config" not in rendered  # No longer in user prompt


def test_lego_agent_repair_prompt(loader):
    rendered = loader.render(
        "lego_agent/repair.jinja2", error="TEST_ERROR", raw_response="RAW_RESPONSE"
    )
    assert "TEST_ERROR" in rendered
    assert "RAW_RESPONSE" in rendered
    assert "yaml_config" in rendered
