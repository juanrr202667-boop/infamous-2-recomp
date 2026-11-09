"""Compile the real display bridge fixture; hardware runs require explicit opt-in.

The integrator schedules exclusive GPU ownership before setting
I2_DISPLAY_BRIDGE_GPU_TEST=1. Default collection compiles but never executes
the probe. No game or RPCS3 is launched by this module.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DisplayBridgeGPUFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("the D3D12 display bridge fixture requires Windows")
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-display-bridge-gpu-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        cls.executable = directory / "rsx_display_bridge_gpu_probe.exe"
        compiled = subprocess.run([
            compiler, "/nologo", "/std:c++17", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(ROOT / "tools/ps3recomp/libs/video"),
            str(ROOT / "tests/native/rsx_display_bridge_gpu_probe.cpp"),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "display_bridge.obj"),
            "/link", "d3d12.lib", "dxgi.lib", "dxguid.lib", "d3dcompiler.lib",
        ], capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def test_fixture_compiles_without_opening_a_gpu_device(self):
        self.assertTrue(self.executable.is_file(), "the fixture compiler produced no executable")

    @unittest.skipUnless(os.environ.get("I2_DISPLAY_BRIDGE_GPU_TEST") == "1",
                         "set I2_DISPLAY_BRIDGE_GPU_TEST=1 only with exclusive GPU ownership")
    def test_two_masked_draws_preserve_and_publish_canonical_pixels(self):
        result = subprocess.run([str(self.executable)], capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("PASS draw=1 red=11,21 expected=11,21 channels_preserved=1", output)
        self.assertIn("PASS draw=2 red=12,22 expected=12,22 channels_preserved=1", output)
        self.assertIn("errors=0 warnings=0", output)
        self.assertIn("draws=2 exact_rgba_pixels=4 channels=77,88,255 adapter=hardware", output)

    @unittest.skipUnless(os.environ.get("I2_DISPLAY_BRIDGE_GPU_TEST") == "1",
                         "set I2_DISPLAY_BRIDGE_GPU_TEST=1 only with exclusive GPU ownership")
    def test_readback_detects_omitted_display_load(self):
        result = subprocess.run([str(self.executable), "--omit-load"],
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("FAIL draw=1 red=11,21 expected=11,21 channels_preserved=0", output)
        self.assertIn("errors=0 warnings=0", output)

    @unittest.skipUnless(os.environ.get("I2_DISPLAY_BRIDGE_GPU_TEST") == "1",
                         "set I2_DISPLAY_BRIDGE_GPU_TEST=1 only with exclusive GPU ownership")
    def test_readback_detects_omitted_display_writeback(self):
        result = subprocess.run([str(self.executable), "--omit-writeback"],
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("FAIL draw=1 red=10,20 expected=11,21 channels_preserved=1", output)
        self.assertIn("errors=0 warnings=0", output)
