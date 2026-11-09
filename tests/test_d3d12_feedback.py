"""Run the production feedback helper against CPU stores at the COM boundary.

No D3D12 device, GPU, game code or proprietary asset participates.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


class FeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.compiler = compiler
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-d3d12-feedback-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        cls.build_directory = directory
        cls.executable = directory / "probe.exe"
        built = subprocess.run([
            compiler, "/nologo", "/W4", "/WX", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"),
            str(ROOT / "tests/native/rsx_d3d12_feedback_probe.c"),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_snapshot_refreshes_before_each_draw_and_tracks_barriers(self):
        self.run_case("refresh")

    def test_source_already_copy_source_has_no_redundant_transition(self):
        self.run_case("copy-source")

    def test_reserve_hits_keep_independent_stable_resources_without_commands(self):
        self.run_case("stable")

    def test_invalid_sources_and_slot_ids_cannot_allocate_or_emit(self):
        self.run_case("invalid")

    def test_incompatible_resource_descriptions_cannot_allocate_or_emit(self):
        self.run_case("description")

    def test_snapshot_validates_all_inputs_before_any_command(self):
        self.run_case("snapshot-invalid")

    def test_populated_slot_rejects_changed_identity_geometry_or_format(self):
        self.run_case("identity")

    def test_creation_failure_preserves_slots_and_hresult(self):
        self.run_case("creation-failure")

    def test_release_owns_only_snapshots_and_is_idempotent(self):
        self.run_case("release")

    def test_cpp_cinterface_consumer_can_compile_the_real_header(self):
        source = self.build_directory / "header.cpp"
        source.write_text(r'''
#define CINTERFACE
#include <windows.h>
#include <d3d12.h>
#include "rsx_d3d12_feedback.h"
const GUID IID_ID3D12Resource = {};
int main() {
    rsx_d3d12_feedback_context context = {};
    rsx_d3d12_linear_resource source = {};
    rsx_d3d12_feedback_release(&context);
    return rsx_d3d12_feedback_reserve(&context, 0, &source) +
           rsx_d3d12_feedback_snapshot(&context, 0, &source);
}
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
