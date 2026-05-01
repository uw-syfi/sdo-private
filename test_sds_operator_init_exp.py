import subprocess


def test_init_exp():
    """
    Verifies that the `./sds_operator init-exp` command creates an isolated experiment copy.
    """
    app_name = "my-app"
    experiment_name = "my-experiment"

    # Execute the command
    process = subprocess.run(["./sds_operator", "init-exp", app_name, experiment_name], capture_output=True, text=True)

    # Verify that the command executed successfully
    assert process.returncode == 0, f"Expected exit code 0, but got {process.returncode}. Stderr: {process.stderr}"

    # Verify that the output indicates success
    assert "successfully created" in process.stdout.lower()

    # A more advanced test could verify isolation (for example kubectl namespace
    # or filesystem layout) depending on deployment mode.
