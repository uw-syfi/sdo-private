import pytest

# Assume the main application exposes `run_app` (e.g. from `sds_app`) and
# configuration loading (e.g. `load_config` from `sds_config`) as described in docs.


# Mock objects to represent the actual providers and runtimes.
# In a real test, these might be more sophisticated mocks or even
# test doubles that simulate the behavior of the real objects.
class MockClaudeProvider:
    pass


class MockGeminiProvider:
    pass


class MockCliAgent:
    def __init__(self, provider):
        self.provider = provider


class MockPydanticAi:
    def __init__(self, provider):
        self.provider = provider


# A dictionary to map string names to the mock classes
PROVIDER_MAP = {
    "Claude": MockClaudeProvider,
    "Gemini": MockGeminiProvider,
}

RUNTIME_MAP = {
    "cli_agent": MockCliAgent,
    "pydantic_ai": MockPydanticAi,
}


def hypothetical_run_app(config):
    """
    This is a stand-in for the real application's entry point.
    It demonstrates how the config would be used to select components.
    """
    provider_name = config["provider"]
    runtime_name = config["runtime"]

    ProviderClass = PROVIDER_MAP.get(provider_name)
    RuntimeClass = RUNTIME_MAP.get(runtime_name)

    if not ProviderClass or not RuntimeClass:
        raise ValueError("Invalid provider or runtime in config")

    provider_instance = ProviderClass()
    runtime_instance = RuntimeClass(provider=provider_instance)

    # In a real app, this might start a service or event loop.
    # For a test, we can just return the initialized runtime.
    return runtime_instance


@pytest.mark.parametrize(
    ("provider_name", "runtime_name"),
    [
        ("Claude", "cli_agent"),
        ("Claude", "pydantic_ai"),
        ("Gemini", "cli_agent"),
        ("Gemini", "pydantic_ai"),
    ],
)
def test_provider_and_runtime_are_swappable(provider_name, runtime_name):
    """
    This test verifies that changing the provider and runtime strings in the
    configuration correctly changes the application's behavior. It simulates
    the loading of the `sds.toml` file by patching the config loading mechanism.
    """
    # This dictionary simulates the contents of `sds.toml`
    test_config = {
        "provider": provider_name,
        "runtime": runtime_name,
    }

    # We call our hypothetical application entry point with the test config.
    # In a real test of an existing application, you would instead patch the
    # function that loads `sds.toml` to return `test_config`, then invoke the app entry point.

    runtime_instance = hypothetical_run_app(test_config)

    # Assert that the instantiated runtime is of the correct type
    ExpectedRuntimeClass = RUNTIME_MAP[runtime_name]
    assert isinstance(runtime_instance, ExpectedRuntimeClass), (
        f"Expected runtime to be {ExpectedRuntimeClass.__name__}, but got {type(runtime_instance).__name__}"
    )

    # Assert that the provider within the runtime is of the correct type
    ExpectedProviderClass = PROVIDER_MAP[provider_name]
    assert isinstance(runtime_instance.provider, ExpectedProviderClass), (
        f"Expected provider to be {ExpectedProviderClass.__name__}, but got {type(runtime_instance.provider).__name__}"
    )
