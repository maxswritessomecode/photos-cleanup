import unittest
import sys
import os
import subprocess

# Ensure workspace is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

class TestCLI(unittest.TestCase):
    def test_cli_help(self):
        # Call the CLI script with --help to verify parser compiles
        cli_path = os.path.join(os.path.dirname(__file__), '..', 'cli.py')
        res = subprocess.run(['python3', cli_path, '--help'], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Photos Deduplication and Centralization Pipeline", res.stdout)
        self.assertIn("scan", res.stdout)
        self.assertIn("dedup", res.stdout)
        self.assertIn("execute", res.stdout)

if __name__ == '__main__':
    unittest.main()
