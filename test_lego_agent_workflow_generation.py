import unittest

# Assuming lego_agent is the library containing the agent workflow generator
import lego_agent


class TestLegoAgent(unittest.TestCase):
    """
    Tests for the lego_agent workflow generator.
    """

    def test_workflow_generation(self):
        """
        Tests that the lego_agent can generate a workflow.
        """
        # Define a simple prompt for the workflow generation
        prompt = "Create a workflow to greet a user."

        # Generate the workflow
        workflow = lego_agent.generate_workflow(prompt)

        # Assert that the workflow is not None
        self.assertIsNotNone(workflow, "The generated workflow should not be None.")

        # Add more specific assertions based on the expected workflow structure
        # For example, check if it's a list of steps
        self.assertIsInstance(workflow, list, "The workflow should be a list of steps.")
        self.assertGreater(len(workflow), 0, "The workflow should have at least one step.")


if __name__ == "__main__":
    unittest.main()
