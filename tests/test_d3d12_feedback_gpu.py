"""Compile the real feedback fixture; GPU runs require a separate explicit opt-in.

Ordinary test collection compiles only. The integrator schedules GPU ownership
before setting I2_FEEDBACK_GPU_TEST=1; no game or RPCS3 is started by this module.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FeedbackGPUFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("the D3D12 feedback fixture requires Windows")
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-feedback-gpu-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        cls.executable = directory / "rsx_feedback_gpu_probe.exe"
        compiled = subprocess.run([
            compiler, "/nologo", "/std:c++17", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(ROOT / "tools/ps3recomp/libs/video"),
            str(ROOT / "tests/native/rsx_feedback_gpu_probe.cpp"),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "feedback.obj"),
            "/link", "d3d12.lib", "dxgi.lib", "dxguid.lib", "d3dcompiler.lib",
        ], capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def test_fixture_compiles_without_opening_a_gpu_device(self):
        self.assertTrue(self.executable.is_file(), "the fixture compiler produced no executable")

    @unittest.skipUnless(os.environ.get("I2_FEEDBACK_GPU_TEST") == "1",
                         "set I2_FEEDBACK_GPU_TEST=1 only with exclusive GPU ownership")
    def test_two_draws_refresh_the_real_snapshot(self):
        result = subprocess.run([str(self.executable)], capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("PASS draw=1 red=11,21 expected=11,21", output)
        self.assertIn("PASS draw=2 red=12,22 expected=12,22", output)
        self.assertIn("errors=0 warnings=0", output)
        self.assertIn("draws=2 exact_rgba_pixels=4 adapter=hardware", output)

    @unittest.skipUnless(os.environ.get("I2_FEEDBACK_GPU_TEST") == "1",
                         "set I2_FEEDBACK_GPU_TEST=1 only with exclusive GPU ownership")
    def test_readback_rejects_a_stale_second_snapshot(self):
        result = subprocess.run([str(self.executable), "--stale-second-snapshot"],
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("PASS draw=1 red=11,21 expected=11,21", output)
        self.assertIn("FAIL draw=2 red=11,21 expected=12,22", output)
        self.assertIn("errors=0 warnings=0", output)
