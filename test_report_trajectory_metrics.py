import os
import subprocess
import tempfile


def test_trajectory_metrics_report():
    """
    Verifies that `./sds_operator analyze-prompts` reports trajectory metrics.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a dummy prompt file.
        with open(os.path.join(temp_dir, "prompt.txt"), "w") as f:
            f.write("This is a test prompt.")

        # Run the command and capture the output.
        result = subprocess.run(["./sds_operator", "analyze-prompts", temp_dir], capture_output=True, text=True)

        # Check that the command executed successfully.
        assert result.returncode == 0, f"Command failed with error: {result.stderr}"

        # Check that the output contains the expected header for trajectory metrics.
        assert "Trajectory Metrics" in result.stdout
