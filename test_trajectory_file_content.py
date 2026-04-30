import unittest
import json

class TestTrajectoryFileContent(unittest.TestCase):

    def setUp(self):
        """Set up a sample trajectory data representing the content of a trajectory file."""
        self.trajectory_data = [
            {
                "phase": "Phase 1",
                "prompt": "This is the first prompt.",
                "response": "This is the first response.",
                "token_counts": {
                    "prompt_tokens": 5,
                    "response_tokens": 5,
                    "total_tokens": 10
                },
                "success": True
            },
            {
                "phase": "Phase 2",
                "prompt": "This is the second prompt.",
                "response": "This is the second response.",
                "token_counts": {
                    "prompt_tokens": 5,
                    "response_tokens": 5,
                    "total_tokens": 10
                },
                "success": False,
                "error": "An error occurred."
            }
        ]

    def test_trajectory_entries_have_required_fields(self):
        """
        Verify that each entry in the trajectory data contains the required fields.
        """
        for entry in self.trajectory_data:
            self.assertIn("phase", entry)
            self.assertIn("prompt", entry)
            self.assertIn("response", entry)
            self.assertIn("token_counts", entry)
            self.assertIn("success", entry)

    def test_token_counts_structure(self):
        """
        Verify that the token_counts dictionary has the correct structure.
        """
        for entry in self.trajectory_data:
            token_counts = entry.get("token_counts", {})
            self.assertIn("prompt_tokens", token_counts)
            self.assertIn("response_tokens", token_counts)
            self.assertIn("total_tokens", token_counts)

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)