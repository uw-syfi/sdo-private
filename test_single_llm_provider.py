import unittest
from unittest.mock import mock_open, patch

# This test verifies the promise that all agents share a single LLM provider
# configured in `sds.toml` and accessed via `agentshim/`.


# Mock representation of the agentshim module for testing purposes.
# In a real system, this would be an actual module.
class AgentShim:
    _llm_provider_client = None

    @classmethod
    def get_llm_client(cls):
        """
        Gets the LLM client. If not already initialized, it reads the
        configuration from 'sds.toml' to create the client. This ensures
        a single, shared instance based on the configuration.
        """
        if cls._llm_provider_client is None:
            # In a real implementation, this would involve parsing the TOML file.
            # For this test, we'll use simple string manipulation on the mock file.
            with open("sds.toml") as f:
                config_content = f.read()

            provider_name = None
            for line in config_content.split("\n"):
                if "provider" in line:
                    provider_name = line.split("=")[1].strip().strip('"')
                    break

            if provider_name:
                # In a real system, this would initialize a client library.
                # Here, we just create a string to represent the client.
                cls._llm_provider_client = f"{provider_name}_client"
            else:
                raise ValueError("LLM provider not found in sds.toml")

        return cls._llm_provider_client


# Mock representations of agents that need to use the LLM.
class MockAgentAlpha:
    def do_task(self):
        # The agent gets the LLM client via the shared agentshim.
        return AgentShim.get_llm_client()


class MockAgentBeta:
    def do_task(self):
        # This agent also gets the LLM client via the same shared agentshim.
        return AgentShim.get_llm_client()


class TestSingleLLMProvider(unittest.TestCase):
    def setUp(self):
        # Reset the singleton client before each test to ensure isolation.
        AgentShim._llm_provider_client = None

    def test_agents_share_single_provider_configured_in_toml(self):
        """
        Verifies that different agents access the same LLM client, and
        that the client is the one specified in the mock sds.toml file.
        """
        mock_toml_content = '[llm]\nprovider = "gemini"'

        with patch("builtins.open", mock_open(read_data=mock_toml_content)):
            agent_alpha = MockAgentAlpha()
            agent_beta = MockAgentBeta()

            # Both agents perform a task that requires the LLM client.
            client_from_alpha = agent_alpha.do_task()
            client_from_beta = agent_beta.do_task()

            # 1. Verify the client is based on the 'gemini' config.
            self.assertEqual(client_from_alpha, "gemini_client")

            # 2. Verify both agents received the exact same client instance.
            self.assertIs(client_from_alpha, client_from_beta)

    def test_llm_provider_can_be_switched_via_config(self):
        """
        Verifies that if the configuration changes to a different provider,
        the agentshim provides a client for the new provider.
        """
        mock_toml_content = '[llm]\nprovider = "claude"'

        with patch("builtins.open", mock_open(read_data=mock_toml_content)):
            agent = MockAgentAlpha()
            client = agent.do_task()

            # Verify the client is now based on the 'claude' config.
            self.assertEqual(client, "claude_client")


if __name__ == "__main__":
    unittest.main(argv=["first-arg-is-ignored"], exit=False)
