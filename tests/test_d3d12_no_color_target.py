"""Distinguish valid depth draws with NONE from unknown surface state.

Execute the real metadata/planner, real record count function and the unchanged
PSO guard/descriptor statements at a simulated CreateGraphicsPipelineState COM
boundary. This catches the production clamp that currently turns zero into one.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}", start) + 2]


class NoColorTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        end = backend.index("} D3D12DrawRecord;") + len("} D3D12DrawRecord;")
        record = backend[backend.rfind("typedef struct {", 0, end):end]
        pso = function(backend, "static ID3D12PipelineState* vp_get_fp_pso(")
        guard = pso[pso.index("{") + 1:pso.index("    if (!fp_addr")]
        build = pso[pso.index("D3D12_GRAPHICS_PIPELINE_STATE_DESC pd = {0};"):
                    pso.index("u32 texture_mask = rsx_d3d12_shader_texture_mask(pb);")]
        source = (ROOT / "tests/native/rsx_no_color_target_probe.c").read_text(encoding="utf-8")
        source = (source.replace("__RECORD__", record)
                  .replace("__COUNT__", function(backend, "static int dr_num_rts("))
                  .replace("__DEPTH__", function(backend,
                           "static inline D3D12_COMPARISON_FUNC gcm_depth_func_d3d("))
                  .replace("__PSO_GUARD__", guard).replace("__PSO_BUILD__", build))
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-no-color-target-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(source, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        result = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"), str(probe),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, mode):
        result = subprocess.run([str(self.executable), mode], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS no-color target production behavior", result.stdout)

    def test_valid_none_declares_zero_color_targets(self): self.run_case("metadata-none")
    def test_unknown_selector_keeps_unsupported_status(self): self.run_case("metadata-unknown")
    def test_missing_metadata_keeps_absent_status(self): self.run_case("metadata-missing")
    def test_actual_record_target_count_is_zero_for_none(self): self.run_case("record-count-none")
    def test_none_draw_does_not_allocate_color_target(self): self.run_case("planner-none")
    def test_none_zero_offset_does_not_become_display(self): self.run_case("planner-none-zero-offset")
    def test_none_nonzero_color_mask_is_explicitly_unsupported(self): self.run_case("planner-none-nonzero-mask")
    def test_unknown_draw_selector_fails_before_reservation(self): self.run_case("planner-unknown")
    def test_missing_draw_metadata_fails_before_reservation(self): self.run_case("planner-missing")
    def test_active_a_with_zero_mask_still_has_declared_target(self): self.run_case("planner-active-a")
    def test_active_b_with_zero_mask_still_has_declared_target(self): self.run_case("planner-active-b")
    def test_depth_only_clear_does_not_allocate_color(self): self.run_case("planner-depth-clear")
    def test_color_clear_without_target_remains_unsupported(self): self.run_case("planner-color-clear-none")
    def test_none_draw_keeps_active_texture_read_planning(self): self.run_case("planner-none-source-read")
    def test_actual_pso_preserves_zero_targets_and_depth_shader(self): self.run_case("pso-zero")
    def test_actual_pso_keeps_zero_mask_on_active_color_target(self): self.run_case("pso-one-masked")
    def test_actual_pso_rejects_negative_target_count_before_creation(self): self.run_case("pso-negative")
    def test_actual_pso_rejects_excess_target_count_before_creation(self): self.run_case("pso-overflow")
