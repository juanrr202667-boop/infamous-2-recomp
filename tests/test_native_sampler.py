"""Opt-in check that the port runner starts its unattended guest profiler."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("I2_NATIVE_TEST") == "1",
                     "set I2_NATIVE_TEST=1 to run the local native boot")
class NativeSamplerTests(unittest.TestCase):
    def test_enabled_sampler_collects_guest_reports(self) -> None:
        env = os.environ.copy()
        env.update({"PS3_SAMPLE": "10", "PS3_SAMPLE_MAIN": "1"})
        env.pop("PPU_POKE_ADDR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/probe_boot.py"), "--seconds", "3"],
            cwd=ROOT, env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        log = (ROOT / "logs/boot-probe.log").read_text(encoding="utf-8", errors="replace")
        self.assertTrue("[samp] sampling every 10 ms" in log,
                        "PS3_SAMPLE did not start the profiler in the port runner")
        self.assertTrue("[samp] pdata self-check:" in log,
                        "Profiler did not check the validity of function attribution")
        self.assertTrue("[samp] " in log and "samples" in log,
                        "Profiler did not publish a sample report")


if __name__ == "__main__":
    unittest.main()
