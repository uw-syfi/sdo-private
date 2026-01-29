import pytest
from agentflow.prompts import get_loader, reset_loader

@pytest.fixture
def loader():
    """Fixture that provides a loader and ensures cleanup after test."""
    loader = get_loader()
    yield loader
    reset_loader()

def test_loader_initialization(loader):
    # Verify that the loader is correctly pointing to the templates directory
    assert loader.templates_dir.exists()
    assert (loader.templates_dir / "agentflow").exists()

def test_agentflow_system_prompt(loader):
    rendered = loader.render("agentflow/system.jinja2")
    assert rendered is not None
    # Add assertions based on expected content if known, 
    # but at least check it renders.

def test_agentflow_user_prompt(loader):
    rendered = loader.render(
        "agentflow/user.jinja2", 
        user_prompt="TEST_PROMPT",
        qa_pairs=[],
        loop_bound=10
    )
    assert "TEST_PROMPT" in rendered
    assert "MAX_ITERATIONS = 10" in rendered

def test_agentflow_repair_prompt(loader):
    rendered = loader.render(
        "agentflow/repair.jinja2",
        error="TEST_ERROR",
        raw_response="RAW_RESPONSE"
    )
    assert "TEST_ERROR" in rendered
    assert "RAW_RESPONSE" in rendered
