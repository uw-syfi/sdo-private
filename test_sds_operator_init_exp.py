import subprocess

def test_init_exp():
    """
    Verifies that the `./sds_operator init-exp` command creates an isolated experiment copy.
    """
    app_name = "my-app"
    experiment_name = "my-experiment"

    # Execute the command
    process = subprocess.run(
        ["./sds_operator", "init-exp", app_name, experiment_name],
        capture_output=True,
        text=True
    )

    # Verify that the command executed successfully
    assert process.returncode == 0, f"Expected exit code 0, but got {process.returncode}. Stderr: {process.stderr}"

    # Verify that the output indicates success
    assert "successfully created" in process.stdout.lower()

    # A more advanced test would verify the isolation, for example,
    # by checking for the creation of a new namespace or a copy of the application
    # with the experiment name. For example:
    #
    # verify_process = subprocess.run(["kubectl", "get", "namespace", experiment_name], capture_output=True, text=True)
    # assert verify_process.returncode == 0, f"Namespace '{experiment_name}' should have been created."

