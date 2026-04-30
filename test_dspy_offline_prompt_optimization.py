
import unittest
import subprocess
import os
import json

class TestOptimizePrompts(unittest.TestCase):

    def setUp(self):
        """Set up a dummy prompt file for testing."""
        self.input_filename = "prompts.json"
        self.output_filename = "optimized_prompts.json"
        
        dummy_prompts = {
            "task": "classification",
            "prompts": [
                {"role": "user", "content": "Classify this text: {text}"}
            ]
        }
        with open(self.input_filename, "w") as f:
            json.dump(dummy_prompts, f)

    def tearDown(self):
        """Clean up the created files."""
        for filename in [self.input_filename, self.output_filename]:
            if os.path.exists(filename):
                os.remove(filename)

    def test_run_dspy_offline_optimization(self):
        """
        Tests that the `./sds_operator optimize-prompts` command runs successfully
        and produces an optimized prompts file.
        """
        command = [
            "./sds_operator",
            "optimize-prompts",
            f"--input-file={self.input_filename}",
            f"--output-file={self.output_filename}"
        ]
        
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True
            )
            
            # Check that the command output indicates success
            self.assertIn("Optimization complete", result.stdout)
            
            # Verify that the output file was created and is not empty
            self.assertTrue(os.path.exists(self.output_filename))
            self.assertGreater(os.path.getsize(self.output_filename), 0)
            
            # Verify that the content of the output file is valid JSON
            with open(self.output_filename, 'r') as f:
                try:
                    json.load(f)
                except json.JSONDecodeError:
                    self.fail("Output file is not valid JSON.")

        except FileNotFoundError:
            self.fail("sds_operator not found. Make sure it's in the current directory and executable.")
        except subprocess.CalledProcessError as e:
            self.fail(f"Command failed with exit code {e.returncode}.\nStderr: {e.stderr}")

if __name__ == '__main__':
    unittest.main()
