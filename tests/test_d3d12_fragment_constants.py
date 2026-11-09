"""Effective constants through production cache, record snapshots and CB binding.

No GPU or game assets. HLSL compilation/reflection run on CPU. A correct literal
implementation or a buffered PSO with distinct per-draw payloads can both pass.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


def function(source, signature):
    search = 0
    while True:
        start = source.index(signature, search)
        opening = source.index("{", start)
        semicolon = source.find(";", start)
        if semicolon != -1 and semicolon < opening:
            search = semicolon + 1
            continue
        return source[start:source.index("\n}", opening) + 2]


def structure(source, name):
    end = source.index("} " + name + ";") + len("} " + name + ";")
    return source[source.rfind("typedef struct {", 0, end):end]


class FragmentConstantsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        planner = (SDK / "libs/video/rsx_d3d12_temporal_plan.h").read_text(encoding="utf-8")
        end = planner.index("} s_rt_plan[MAX_DRAWS];") + len("} s_rt_plan[MAX_DRAWS];")
        plan = planner[planner.rfind("static struct {", 0, end):end]
        synthetic = (SDK / "libs/video/tests/test_fp_decompiler.c").read_text(encoding="utf-8")
        headers = re.findall(r'^#include "(rsx_d3d12_[^"]+\.h)"',
                             backend[:backend.index("#define FRAME_COUNT")], re.M)
        headers = '\n'.join('#include "' + name + '"' for name in headers
                            if name != "rsx_d3d12_backend.h")
        strides = '\n'.join(re.search(r'^#define ' + name + r'\s+[^\n]*', backend, re.M).group(0)
                            for name in ("VP_CB_STRIDE", "VP_FPCB_STRIDE"))
        capacity = re.search(r'^#define VP_FP_CACHE\s+[^\n]*', backend, re.M).group(0)
        start = backend.index("/* Per-draw constants: this draw's vp_cb + FP texscale slots.")
        binding = backend[start:backend.index("/* DRAWARGS=", start)]
        start = synthetic.index("#define OPC(x)")
        bits = synthetic[start:synthetic.index("int main(void)", start)]
        source = (ROOT / "tests/native/rsx_fragment_constants_probe.c").read_text(encoding="utf-8")
        for token, replacement in (
            ("__HEADERS__", headers), ("__STRIDES__", strides), ("__CAPACITY__", capacity),
            ("__RECORD__", structure(backend, "D3D12DrawRecord")),
            ("__VS_ENTRY__", structure(backend, "VPVSEntry")),
            ("__FP_ENTRY__", structure(backend, "VPFPEntry")),
            ("__PLAN__", plan),
            ("__DEPTH__", function(backend, "static inline D3D12_COMPARISON_FUNC gcm_depth_func_d3d(")),
            ("__REPLACE__", function(backend, "static void hlsl_replace_all(")),
            ("__ABORT__", function(backend, "static void temporal_abort(")),
            ("__PSO__", function(backend, "static ID3D12PipelineState* vp_get_fp_pso(")),
            ("__RECORD_CB__", function(backend, "static void vp_record_cb(")),
            ("__BIND_CB__", binding),
            ("__SYNTHETIC_HELPERS__", function(synthetic, "static void put_word(") + '\n' +
             function(synthetic, "static void put_float(")),
            ("__SYNTHETIC_BITS__", bits),
        ):
            source = source.replace(token, replacement)
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-fragment-constants-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(source, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        result = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"),
            str(probe), str(SDK / "libs/video/rsx_fp_decompiler.c"),
            "/Fe" + str(cls.executable), "/Fo" + str(directory) + "/", "d3dcompiler.lib",
        ], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.environment = os.environ.copy()
        for name in tuple(cls.environment):
            if name.startswith(("FP_", "FPK_", "VP_", "RSX_", "DBG_")) or name in (
                    "CULL_OFF", "CULL_FLIP", "CMASK_FORCE", "DEPTH_OFF", "NO_ALPHATEST",
                    "HASHDBG", "PSOMISSDBG", "CUBEKEYDBG"):
                cls.environment.pop(name)

    def run_case(self, mode):
        result = subprocess.run([str(self.executable), mode], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS effective constants", result.stdout)
        return result

    def test_changed_literal_reaches_second_draw(self): self.run_case("changed-literal")
    def test_deferred_batch_preserves_both_record_time_values(self): self.run_case("deferred")
    def test_same_literal_can_reuse_pipeline(self): self.run_case("same-literal")
    def test_changed_code_cannot_reuse_old_pipeline(self): self.run_case("changed-code")
    def test_deferred_batch_preserves_code_before_guest_overwrites_program(self):
        result = self.run_case("deferred-changed-code")
        self.assertIn("[FP_CODE_LIFETIME]", result.stderr)
    def test_binding_uses_record_cb_slot_after_reordering(self): self.run_case("reordered-slots")
    def test_reflected_layout_consumes_distinct_record_snapshots(self): self.run_case("record-snapshots")
    def test_all_128_constant_pairs_fit_the_4096_byte_program_window(self): self.run_case("limit-128")
    def test_snapshot_preserves_scales_alpha_and_zeros_unused_constants(self): self.run_case("bank-integrity")

    def assert_snapshot_fatal(self, mode):
        result = subprocess.run([str(self.executable), mode], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("TEMPORAL_FATAL", result.stderr)

    def test_unterminated_program_cannot_publish_a_snapshot(self): self.assert_snapshot_fatal("unterminated")
    def test_truncated_inline_payload_cannot_publish_a_snapshot(self): self.assert_snapshot_fatal("truncated")
    def test_requested_fp_without_mapped_buffer_aborts(self): self.assert_snapshot_fatal("missing-fp-mapping")
    def test_requested_fp_without_vertex_mapping_aborts(self): self.assert_snapshot_fatal("missing-vp-mapping")
    def test_requested_fp_without_both_mappings_aborts(self): self.assert_snapshot_fatal("both-mappings-missing")
