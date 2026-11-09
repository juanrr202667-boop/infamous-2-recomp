"""Real D3D12 fixture for per-draw fragment constant snapshots.

Default checks compile and execute CPU-only modes. Hardware cases require
I2_FRAGMENT_CONSTANTS_GPU_TEST=1 and separately scheduled exclusive GPU ownership.
All shader input is synthetic; no game or RPCS3 is launched.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"
GPU = os.environ.get("I2_FRAGMENT_CONSTANTS_GPU_TEST") == "1"


class FragmentConstantsGPUFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt":
            raise unittest.SkipTest("the fragment constants D3D12 fixture requires Windows")
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-fragment-constants-gpu-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        cls.executable = directory / "rsx_fragment_constants_gpu_probe.exe"
        common = [compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
                  "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video")]
        library = directory / "decompiler.obj"
        compiled = subprocess.run(common + [
            "/c", str(SDK / "libs/video/rsx_fp_decompiler.c"), "/Fo" + str(library),
        ], capture_output=True, text=True, timeout=60)
        cls.library_diagnostics = compiled.stdout + compiled.stderr
        if compiled.returncode:
            raise AssertionError(cls.library_diagnostics)
        probe = directory / "fragment_constants.obj"
        compiled = subprocess.run(common + [
            "/c", "/std:c++17", "/W4", "/WX", str(ROOT / "tests/native/rsx_fragment_constants_gpu_probe.cpp"),
            "/Fo" + str(probe),
        ], capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)
        compiled = subprocess.run([
            compiler, "/nologo", str(probe), str(library), "/Fe" + str(cls.executable),
            "/link", "d3d12.lib", "dxgi.lib", "dxguid.lib", "d3dcompiler.lib",
        ], capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)

    def run_probe(self, argument=None):
        command = [str(self.executable)] + ([argument] if argument else [])
        return subprocess.run(command, capture_output=True, text=True, timeout=30)

    def test_fixture_compiles_without_opening_a_gpu_device(self):
        self.assertTrue(self.executable.is_file())

    def test_cpu_oracle_detects_stale_binding_wrong_layout_and_invalid_channels(self):
        result = self.run_probe("--oracle-self-test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS CPU oracle cases=5 gpu_calls=0", result.stdout)

    def test_real_helpers_snapshot_before_repatch_and_hlsl_reflects_expected_offsets(self):
        result = self.run_probe("--shader-self-test")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("PASS snapshots slots=1 first_r=0.25 second_r=0.75 repatched_r=0.875", output)
        self.assertIn("STATUS reflection register=b1 alpha_offset=256 constants_offset=272 constants_size=2048", output)
        self.assertIn("STATUS linkage ps_inputs=12 matched=12 missing=0", output)
        self.assertIn("PASS offline helpers shaders=2 instructions=1 constants=1 stride=2560 gpu_calls=0", output)

    @unittest.skipUnless(GPU, "set I2_FRAGMENT_CONSTANTS_GPU_TEST=1 only with exclusive GPU ownership")
    def test_one_pipeline_consumes_distinct_snapshots_in_two_draws(self):
        result = self.run_probe()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("STATUS root_cbv=b1 stride=2560 bank_delta=2560 pso_creates=1", output)
        self.assertIn("PASS pixels draw=1 mismatches=0 rgba=0.25,0.125,0.5,1", output)
        self.assertIn("PASS pixels draw=2 mismatches=0 rgba=0.75,0.125,0.5,1", output)
        self.assertIn("errors=0 warnings=0", output)
        self.assertIn("PASS fragment constants draws=2 pso_creates=1 pixels=32 adapter=hardware", output)

    @unittest.skipUnless(GPU, "set I2_FRAGMENT_CONSTANTS_GPU_TEST=1 only with exclusive GPU ownership")
    def test_readback_detects_stale_second_cbv_without_api_validation_errors(self):
        result = self.run_probe("--stale-second-cb")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("PASS pixels draw=1 mismatches=0", output)
        self.assertIn("FAIL pixels draw=2 mismatches=16 rgba=0.25,0.125,0.5,1", output)
        self.assertIn("errors=0 warnings=0", output)
        self.assertIn("STATUS api_completed=1 fence_completed=1", output)

    @unittest.skipUnless(GPU, "set I2_FRAGMENT_CONSTANTS_GPU_TEST=1 only with exclusive GPU ownership")
    def test_readback_detects_constants_written_at_old_offset_without_api_validation_errors(self):
        result = self.run_probe("--wrong-layout")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("FAIL pixels draw=1 mismatches=16 rgba=0,0,0,0", output)
        self.assertIn("FAIL pixels draw=2 mismatches=16 rgba=0,0,0,0", output)
        self.assertIn("errors=0 warnings=0", output)
        self.assertIn("STATUS api_completed=1 fence_completed=1", output)
