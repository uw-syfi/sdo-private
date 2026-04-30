
import unittest
import os
import tempfile
import shutil

class TestOutputLocation(unittest.TestCase):

    def setUp(self):
        self.app_dir = tempfile.mkdtemp()
        self.sds_dir = os.path.join(self.app_dir, '.sds')
        os.makedirs(self.sds_dir)

    def tearDown(self):
        shutil.rmtree(self.app_dir)

    def test_all_output_lands_in_sds_directory(self):
        # In a real test, you would run your application here and it would generate some output.
        # For this example, we'll simulate the creation of an output file.
        simulated_output_file = os.path.join(self.sds_dir, 'output.txt')
        with open(simulated_output_file, 'w') as f:
            f.write('This is a test output file.')

        # Get the directory of the simulated output file
        output_dir = os.path.dirname(simulated_output_file)

        # Assert that the output directory is the .sds directory
        self.assertEqual(output_dir, self.sds_dir)

        # We can also check that the output file is within the app directory.
        self.assertTrue(simulated_output_file.startswith(self.app_dir))

if __name__ == '__main__':
    unittest.main()
