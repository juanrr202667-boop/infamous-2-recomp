"""Compile the no-color D3D12 fixture; default execution verifies only its CPU oracle.

I2_NO_COLOR_GPU_TEST=1 requires separately scheduled exclusive GPU ownership.
No game or RPCS3 is launched by this module.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NoColorGPUFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("the D3D12 no-color fixture requires Windows")
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-no-color-gpu-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        cls.executable = directory / "rsx_no_color_gpu_probe.exe"
        compiled = subprocess.run([
            compiler, "/nologo", "/std:c++17", "/W4", "/WX", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            str(ROOT / "tests/native/rsx_no_color_gpu_probe.cpp"),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "no_color.obj"),
            "/link", "d3d12.lib", "dxgi.lib", "dxguid.lib", "d3dcompiler.lib",
        ], capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def test_fixture_compiles_without_opening_a_gpu_device(self):
        self.assertTrue(self.executable.is_file(), "the compiler produced no executable")

    def test_cpu_oracle_rejects_lost_discard_depth_test_or_sentinel_corruption(self):
        result = subprocess.run([str(self.executable), "--oracle-self-test"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS CPU oracle cases=5 gpu_calls=0", result.stdout)

    def test_shader_compiles_with_sv_target_output_without_opening_a_device(self):
        result = subprocess.run([str(self.executable), "--shader-self-test"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS offline shaders ps_sv_target=1 gpu_calls=0", result.stdout)

    def test_cpu_validation_accepts_only_one_observed_unbound_output_warning_with_ps(self):
        result = subprocess.run([str(self.executable), "--validation-self-test"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS CPU validation cases=6 gpu_calls=0", result.stdout)

    @unittest.skipUnless(os.environ.get("I2_NO_COLOR_GPU_TEST") == "1",
                         "set I2_NO_COLOR_GPU_TEST=1 only with exclusive GPU ownership")
    def test_zero_color_targets_preserve_discard_and_depth_with_sv_target_output(self):
        result = subprocess.run([str(self.executable)], capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("STATUS num_render_targets=0 om_rtv_count=0 ps_sv_target=1", output)
        self.assertIn("PASS depth draw=1 mismatches=0 left=1 right=0.25", output)
        self.assertIn("PASS depth draw=2 mismatches=0 left=0.75 right=0.25", output)
        self.assertIn("PASS sentinel pixels=16 mismatches=0", output)
        self.assert_validation_messages(output, bound_ps=True)
        self.assertIn("PASS zero-color depth: draws=2 depth_pixels=32 sentinel_pixels=16 adapter=hardware", output)

    @unittest.skipUnless(os.environ.get("I2_NO_COLOR_GPU_TEST") == "1",
                         "set I2_NO_COLOR_GPU_TEST=1 only with exclusive GPU ownership")
    def test_depth_readback_detects_omitted_discard(self):
        self.assert_negative_control("--omit-discard")

    @unittest.skipUnless(os.environ.get("I2_NO_COLOR_GPU_TEST") == "1",
                         "set I2_NO_COLOR_GPU_TEST=1 only with exclusive GPU ownership")
    def test_depth_readback_detects_omitted_pixel_shader(self):
        self.assert_negative_control("--omit-ps")

    def assert_negative_control(self, argument):
        result = subprocess.run([str(self.executable), argument], capture_output=True,
                                text=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("FAIL depth draw=1 mismatches=8 left=0.25 right=0.25", output)
        self.assertIn("FAIL depth draw=2 mismatches=8 left=0.25 right=0.25", output)
        self.assertIn("PASS sentinel pixels=16 mismatches=0", output)
        self.assert_validation_messages(output, bound_ps=argument != "--omit-ps")

    def assert_validation_messages(self, output, bound_ps):
        expected = int(bound_ps)
        self.assertIn(f"errors=0 warnings={expected} expected_unbound_output_warnings={expected} "
                      "unexpected_warnings=0", output)
        if bound_ps:
            self.assertIn("[D3D12-IQ][severity=2 id=679]", output)
        else:
            self.assertNotIn("[D3D12-IQ][severity=2 id=679]", output)
