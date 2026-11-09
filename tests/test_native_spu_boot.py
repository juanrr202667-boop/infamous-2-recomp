"""Opt-in regression against the locally recompiled game, without a window."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("I2_NATIVE_TEST") == "1",
                     "set I2_NATIVE_TEST=1 to run the local native boot")
class NativeSpuBootTests(unittest.TestCase):
    def test_policy_returns_without_malformed_dma(self) -> None:
        env = os.environ.copy()
        env["SPURS_PM_TRACE"] = "1"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/probe_boot.py"), "--seconds", "5"],
            cwd=ROOT, env=env, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((ROOT / "logs/boot-status.json").read_text(encoding="utf-8"))
        self.assertTrue(report["stages"]["spu_initialized"])
        log = (ROOT / "logs/boot-probe.log").read_text(encoding="utf-8", errors="replace")
        self.assertIn("PM resolved (fp=0xB3523C4B17650EF8 image=1)", log)
        self.assertRegex(log, r"\[spurs-pm\] RUN#\d+ wid=0\b")
        self.assertTrue(re.search(
            r"\[spurs-pm\] END wid=0 status=0x[0-9A-F]+ pc=0x009C0 polls=\d+ exited=1",
            log), "WWS policy did not return to exitToKernel")
        self.assertEqual(report["missing_workloads"], [])
        self.assertEqual(report["issues"].get("spu_malformed_dma", 0), 0,
                         report["first_malformed_dma"])
        self.assertEqual(report["issues"].get("spu_repeated_dma_halt", 0), 0)


if __name__ == "__main__":
    unittest.main()
