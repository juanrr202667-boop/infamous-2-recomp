"""Execute the real display bridge with CPU stores at the D3D12 COM boundary."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


class DisplayBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.compiler = compiler
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-display-bridge-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.build_directory = Path(cls.directory.name)
        cls.executable = cls.build_directory / "probe.exe"
        built = subprocess.run([
            compiler, "/nologo", "/W4", "/WX", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"),
            str(ROOT / "tests/native/rsx_display_bridge_probe.c"),
            "/Fe" + str(cls.executable), "/Fo" + str(cls.build_directory / "probe.obj"),
        ], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_round_trip_preserves_untouched_channels_and_pixels_across_two_draws(self):
        self.run_case("partial-draws")

    def test_copy_directions_and_transition_order_use_the_canonical_tracker(self):
        self.run_case("states")

    def test_no_redundant_canonical_transition_when_already_in_copy_state(self):
        self.run_case("copy-states")

    def test_invalid_context_or_canonical_view_emits_no_commands(self):
        self.run_case("invalid")

    def test_both_actual_resource_descriptions_are_validated_before_commands(self):
        self.run_case("descriptions")

    def test_borrowed_resources_are_not_allocated_retained_or_released(self):
        self.run_case("borrowed")

    def test_cpp_cinterface_consumer_can_compile_the_real_header(self):
        source = self.build_directory / "header.cpp"
        source.write_text(r'''
#define CINTERFACE
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#if __has_include("rsx_d3d12_display_bridge.h")
#include "rsx_d3d12_display_bridge.h"
int main() {
    rsx_d3d12_display_bridge_context context = {};
    rsx_d3d12_linear_resource canonical = {};
    return rsx_d3d12_display_bridge_begin(&context, &canonical) +
           rsx_d3d12_display_bridge_end(&context, &canonical);
}
#else
int main() { puts("FAIL display bridge not implemented"); return 1; }
#endif
''', encoding="utf-8")
        executable = self.build_directory / "header.exe"
        built = subprocess.run([
            self.compiler, "/nologo", "/W4", "/WX", "/O1", "/std:c++17",
            "/I" + str(SDK / "libs/video"), str(source),
            "/Fe" + str(executable), "/Fo" + str(self.build_directory / "header.obj"),
        ], capture_output=True, text=True)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        result = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
