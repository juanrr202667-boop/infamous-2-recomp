"""Regression checks for the portable synthetic audio analyzer CLI."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AudioAnalyzerTests(unittest.TestCase):
    def test_self_test_runs_from_an_empty_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/analyze_audio_full_scale.py"),
                 "--self-test"],
                cwd=directory, text=True, capture_output=True, check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["self_test"], "PASS")


if __name__ == "__main__":
    unittest.main()
