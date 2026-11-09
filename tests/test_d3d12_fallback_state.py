"""Execute production pipeline selection/preparation on CPU, without a device."""
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
    # A forward declaration is not the function body this fixture executes.
    while 0 <= source.find(";", start) < source.find("{", start):
        start = source.index(signature, start + len(signature))
    return source[start:source.index("\n}", start) + 2]


PRELUDE = r'''
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <crtdbg.h>
#include <setjmp.h>
#include "rsx_d3d12_surface_metadata.h"
#include "rsx_linear_metadata.h"
#define MAX_DRAWS 4
__RECORD__
static struct {
    ID3D12PipelineState *pipeline_state_vp, *pipeline_state_vp_color;
    int tex_ready;
    u32 vp_fp_n, draw_count;
    u64 frame_count;
    struct { ID3D12PipelineState* pso; u32 texture_mask, ucode_hash; } vp_fp[2];
    D3D12DrawRecord draws[MAX_DRAWS];
} s_d3d;
static struct { u32 texture_mask, feedback_mask; } s_rt_plan[MAX_DRAWS];
static ID3D12PipelineState *s_temporal_pso[MAX_DRAWS];
static int s_temporal_guest_fp[MAX_DRAWS];
static u32 s_temporal_fp_hash[MAX_DRAWS];
static unsigned char s_temporal_fp_hash_valid[MAX_DRAWS];
static u32 s_recorded_fp_hash[MAX_DRAWS];
static u8 s_recorded_fp_code[MAX_DRAWS][4096];
static u32 s_recorded_fp_size[MAX_DRAWS];
static u32 s_temporal_error_op;
static int s_temporal_error_unit;
static const char* s_temporal_error;
static double s_perf_pso;
static unsigned retained, feedback_preparations;
static int selected_target_count;
static int guest_ok;
static jmp_buf fatal_boundary;
static unsigned fatal_code;
static void capture_exit(UINT code) { fatal_code=code; longjmp(fatal_boundary, 1); }
#define ExitProcess capture_exit
static ULONG STDMETHODCALLTYPE retain(ID3D12PipelineState* pipeline) {
    (void)pipeline; ++retained; return 2;
}
static ID3D12PipelineStateVtbl table;
static ID3D12PipelineState atlas, color, guest;
static ID3D12PipelineState* vp_get_fp_pso(int vs_idx, u32 fp_addr, u32 blend,
        int nrt, DXGI_FORMAT fmt, int exp32, u32 cmask, u32 cull, u32 cube,
        u32 depth, const u8* ucode, u32 bytes) {
    selected_target_count=nrt;
    (void)vs_idx; (void)fp_addr; (void)blend; (void)fmt;
    (void)exp32; (void)cmask; (void)cull; (void)cube; (void)depth;
    if (ucode != s_recorded_fp_code[0] || bytes != 16) return NULL;
    return guest_ok ? &guest : NULL;
}
static int perf_on(void) { return 0; }
static double perf_now(void) { return 0; }
static int temporal_prepare_textures(u32 draw, u32 mask) {
    (void)draw; (void)mask; return 1;
}
/* This fixture owns pipeline selection, with no aliased textures. Refuse
 * unexpected feedback work rather than constructing a fake GPU device. The
 * production replay/helper fixtures exercise actual feedback preparation. */
static void temporal_prepare_feedback(u32 draw) {
    if (draw >= MAX_DRAWS || s_rt_plan[draw].feedback_mask) exit(5);
    ++feedback_preparations;
}
__HELPERS__
__ABORT__
__SELECT__
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
    _putenv_s("RSX_CUBE_TEX", "1");
    table.AddRef=retain; atlas.lpVtbl=color.lpVtbl=guest.lpVtbl=&table;
    s_d3d.pipeline_state_vp=&atlas; s_d3d.pipeline_state_vp_color=&color;
    s_d3d.draw_count=1;
    D3D12DrawRecord* record=&s_d3d.draws[0];
    record->cmask=15; record->depth_key=3u | (0x203u << 2);
    record->rt_fmt=0x108;
    record->surface_meta.valid=1;
    record->surface_meta.color_target=CELL_GCM_SURFACE_TARGET_0;
    if (!strcmp(argv[1], "zero")) record->cmask=0;
    else if (!strcmp(argv[1], "partial")) record->cmask=1;
    else if (!strcmp(argv[1], "depth-off")) record->depth_key=0;
    else if (!strcmp(argv[1], "depth-read")) record->depth_key &= ~2u;
    else if (!strcmp(argv[1], "depth-less")) record->depth_key=3u | (0x201u << 2);
    else if (!strcmp(argv[1], "float")) { record->rt_off=0x1000; record->rt_fmt=0x10b; }
    else if (!strcmp(argv[1], "unknown-format")) { record->rt_off=0x1000; record->rt_fmt=0x107; }
    else if (!strcmp(argv[1], "mrt")) record->rt_mrt[0]=0x2000;
    else if (!strcmp(argv[1], "mrt-zero"))
        record->surface_meta.color_target=CELL_GCM_SURFACE_TARGET_MRT1;
    else if (!strcmp(argv[1], "unknown-selector")) record->surface_meta.color_target=3;
    else if (!strcmp(argv[1], "none-selector")) record->surface_meta.color_target=CELL_GCM_SURFACE_TARGET_NONE;
    else if (!strcmp(argv[1], "missing-state")) record->surface_meta.valid=0;
    else if (!strcmp(argv[1], "single-b")) record->surface_meta.color_target=CELL_GCM_SURFACE_TARGET_1;
    else if (!strcmp(argv[1], "blend")) record->blend_key=1;
    else if (!strcmp(argv[1], "cull")) record->cull=1;
    else if (!strcmp(argv[1], "cube")) {
        s_d3d.tex_ready=1; record->tex[0].set=1; record->tex[0].cube=1;
    }
    else if (!strcmp(argv[1], "guest-failed")) { record->fp_addr=0x1234; record->cmask=1; }
    else if (!strcmp(argv[1], "guest-failed-all")) record->fp_addr=0x1234;
    else if (!strcmp(argv[1], "guest") || !strcmp(argv[1], "guest-mrt-zero")) {
        record->fp_addr=0x1234; record->cmask=1; record->depth_key=0;
        guest_ok=1; s_d3d.vp_fp_n=1;
        s_d3d.vp_fp[0].pso=&guest; s_d3d.vp_fp[0].texture_mask=0x20;
        s_d3d.vp_fp[0].ucode_hash=0x1234ABCD;
        if (!strcmp(argv[1], "guest-mrt-zero")) record->surface_meta.color_target=CELL_GCM_SURFACE_TARGET_MRT1;
    }
    else if (!strcmp(argv[1], "atlas")) s_d3d.tex_ready=1;
    else if (!strcmp(argv[1], "offscreen")) record->rt_off=0x1000;
    else if (!strcmp(argv[1], "effective-lequal")) record->depth_key=3;
    else if (strcmp(argv[1], "all") && strcmp(argv[1], "fatal")) return 2;
    if (setjmp(fatal_boundary)) {
        if (fatal_code != 2 || retained || s_temporal_pso[0]) return 4;
        puts("REJECTED exit=2 before retaining or submitting"); return 0;
    }
    if (!strcmp(argv[1], "fatal")) temporal_abort(0, -1, "probe fatal boundary");
    s_recorded_fp_size[0]=16;
    const D3D12DrawRecord* dr=record;
    u32 _d=0;
__PREPARE__
    /* This is the submission boundary. No device or GPU is created. */
    puts("SUBMIT");
    u32 want_mask=s_d3d.tex_ready ? 1u : 0u;
    ID3D12PipelineState* want=s_d3d.tex_ready ? &atlas : &color;
    if (guest_ok) { want=&guest; want_mask=0x20; }
    if (retained != 1 || feedback_preparations != 1 || s_temporal_pso[0] != want || draw_tex_mask != want_mask ||
            s_temporal_guest_fp[0] != guest_ok) return 3;
    if (guest_ok && selected_target_count != (record->surface_meta.color_target==CELL_GCM_SURFACE_TARGET_MRT1 ? 2 : 1)) return 6;
    if (s_temporal_fp_hash_valid[0] != guest_ok ||
            (guest_ok && s_temporal_fp_hash[0] != 0x1234ABCD)) return 7;
    puts("PASS selected compatible effective pipeline");
    return 0;
}
'''


class FallbackStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        end = backend.index("} D3D12DrawRecord;") + len("} D3D12DrawRecord;")
        record = backend[backend.rfind("typedef struct {", 0, end):end]
        helpers = "\n".join(function(backend, signature) for signature in (
            "static int dr_num_rts(", "static u32 dr_cube_mask(",
            "static inline D3D12_COMPARISON_FUNC gcm_depth_func_d3d(",
            "static DXGI_FORMAT rsx_surface_dxgi("))
        start = backend.index("static ID3D12PipelineState* temporal_select_pipeline(")
        selection = backend[start:backend.index("static D3D12_RECT temporal_clear_rect(", start)]
        start = backend.index("/* Pin the actual pipeline selected for this operation.")
        preparation = backend[start:backend.index("double _sv0", start)]
        source = (PRELUDE.replace("__RECORD__", record).replace("__HELPERS__", helpers)
                  .replace("__ABORT__", function(backend, "static void temporal_abort("))
                  .replace("__SELECT__", selection).replace("__PREPARE__", preparation))
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-fallback-state-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(source, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"), str(probe),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")],
            capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def assert_rejected(self, mode, reason="fixed fallback incompatible"):
        result = subprocess.run([str(self.executable), mode], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("REJECTED exit=2", result.stdout, mode + " silently submitted")
        self.assertIn("TEMPORAL_FATAL", result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertNotIn("SUBMIT", result.stdout)

    def test_zero_color_mask_aborts(self): self.assert_rejected("zero")
    def test_partial_color_mask_aborts(self): self.assert_rejected("partial")
    def test_depth_disabled_aborts(self): self.assert_rejected("depth-off")
    def test_depth_read_only_aborts(self): self.assert_rejected("depth-read")
    def test_depth_less_aborts(self): self.assert_rejected("depth-less")
    def test_float_target_aborts(self): self.assert_rejected("float")
    def test_unknown_target_format_aborts(self): self.assert_rejected("unknown-format")
    def test_multiple_targets_abort(self): self.assert_rejected("mrt")
    def test_active_mrt_with_secondary_offset_zero_aborts(self): self.assert_rejected("mrt-zero")
    def test_unknown_color_target_selector_aborts(self): self.assert_rejected("unknown-selector")
    def test_disabled_color_target_selector_aborts(self): self.assert_rejected("none-selector")
    def test_missing_captured_color_target_state_aborts(self): self.assert_rejected("missing-state")
    def test_blending_aborts(self): self.assert_rejected("blend")
    def test_culling_aborts(self): self.assert_rejected("cull")
    def test_cube_sampler_aborts(self): self.assert_rejected("cube")
    def test_guest_compile_failure_cannot_bypass_state_guard(self):
        self.assert_rejected("guest-failed", "guest FP pipeline failed")

    def test_requested_guest_program_failure_cannot_select_fixed_pipeline(self):
        result = subprocess.run([str(self.executable), "guest-failed-all"], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("REJECTED exit=2", result.stdout)
        self.assertIn("guest FP pipeline failed", result.stderr)
        self.assertNotIn("SUBMIT", result.stdout)

    def test_fatal_boundary_uses_predictable_process_exit(self):
        result = subprocess.run([str(self.executable), "fatal"], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("REJECTED exit=2", result.stdout)
        self.assertIn("TEMPORAL_FATAL", result.stderr)

    def test_compatible_fixed_and_guest_pipelines_remain_selectable(self):
        for mode in ("all", "single-b", "atlas", "offscreen", "effective-lequal", "guest", "guest-mrt-zero"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.executable), mode], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS selected compatible effective pipeline", result.stdout)
