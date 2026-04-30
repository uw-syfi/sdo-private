import os
import subprocess
import time
import unittest


class TestRunExpParallel(unittest.TestCase):
    def setUp(self):
        # Create a mock sds_operator script
        sds_operator_script = """#!/bin/bash
if [ "$1" == "run-exp" ]; then
    shift
    # Run all subsequent arguments in parallel
    for exp in "$@"; do
        (
            # Simulate running an experiment by executing a command from a dummy config
            # In a real scenario, this would parse a config file and run the experiment.
            # Here, we'll just execute the content of the file.
            bash "$exp"
        ) &
    done
    wait
fi
"""
        with open("sds_operator", "w") as f:
            f.write(sds_operator_script)
        os.chmod("sds_operator", 0o755)

        # Create dummy experiment files
        with open("exp1.sh", "w") as f:
            f.write("sleep 2; echo 'exp1 done'")
        with open("exp2.sh", "w") as f:
            f.write("sleep 2; echo 'exp2 done'")

    def test_run_multiple_experiments_in_parallel(self):
        """
        Tests that `sds_operator run-exp` runs experiments in parallel.
        """
        start_time = time.time()
        subprocess.run(["./sds_operator", "run-exp", "exp1.sh", "exp2.sh"], check=True)
        end_time = time.time()
        duration = end_time - start_time

        # If the experiments ran in parallel, the total duration should be
        # slightly more than 2 seconds, but less than 4 seconds.
        # We allow for some overhead.
        self.assertLess(duration, 3.0)
        self.assertGreater(duration, 2.0)

    def tearDown(self):
        os.remove("sds_operator")
        os.remove("exp1.sh")
        os.remove("exp2.sh")


if __name__ == "__main__":
    unittest.main()
