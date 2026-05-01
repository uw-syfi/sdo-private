import subprocess
import unittest


class TestAgentWorkflow(unittest.TestCase):
    def test_generate_and_run_agent_workflow_cli(self):
        """
        Tests that the sds_lego_agent can generate and run a workflow from a CLI prompt.
        """
        # The command to be tested
        command = ["./sds_lego_agent", "--prompt", "Create a python script to print '''hello world''' and run it."]

        try:
            # Execute the command
            # We expect the agent to generate and execute a python script that prints "hello world"
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,  # Raise an exception for non-zero exit codes
                timeout=300,  # 5-minute timeout for the agent to complete
            )

            # Verify that the command executed successfully
            self.assertEqual(result.returncode, 0)

            # Verify that the output contains the expected string "hello world"
            self.assertIn("hello world", result.stdout.lower())

        except FileNotFoundError:
            self.fail("./sds_lego_agent not found. Please ensure the agent executable is in the path.")
        except subprocess.CalledProcessError as e:
            self.fail(
                f"Agent execution failed with return code {e.returncode}.\nStdout: {e.stdout}\nStderr: {e.stderr}"
            )
        except subprocess.TimeoutExpired:
            self.fail("Agent execution timed out.")


if __name__ == "__main__":
    unittest.main()
