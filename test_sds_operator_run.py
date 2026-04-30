import unittest
import subprocess
import time
import requests

class TestSdsOperatorRun(unittest.TestCase):
    """
    Test case for the './sds_operator run <app>' promise.
    """

    APP_NAME = "nginx"  # Using a simple web server for the test
    APP_PORT = 8080

    @classmethod
    def setUpClass(cls):
        """
        Deploy the application before running tests.
        """
        cls.process = subprocess.Popen(
            ["./sds_operator", "run", cls.APP_NAME],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # Allow time for the application to deploy and start
        time.sleep(10)

    @classmethod
    def tearDownClass(cls):
        """
        Clean up and stop the application after all tests are done.
        """
        # The promise does not specify a stop command, so we kill the process
        cls.process.terminate()
        cls.process.wait()

    def test_deployment_successful(self):
        """
        Check if the deployment command was initiated successfully.
        """
        # A running process indicates the operator has started the app
        self.assertIsNone(self.process.poll(), "sds_operator process terminated unexpectedly.")

    def test_application_is_running_and_accessible(self):
        """
        Verify the application is running and responding to requests.
        """
        try:
            response = requests.get(f"http://localhost:{self.APP_PORT}", timeout=5)
            self.assertEqual(response.status_code, 200, "Application did not return a 200 OK status.")
        except requests.exceptions.RequestException as e:
            self.fail(f"Failed to connect to the application: {e}")

    def test_monitoring_logs(self):
        """
        Verify that the operator is producing monitoring output (logs).
        This is a basic check to see if any output is generated.
        """
        # This test assumes the operator logs to stdout
        output = self.process.stdout.readline()
        self.assertTrue(len(output) > 0, "No monitoring output was captured from the operator.")

if __name__ == '__main__':
    unittest.main()
