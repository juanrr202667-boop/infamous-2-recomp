"""Execute the complete production display clear callback on CPU."""
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


DRIVER = r'''
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
#include "rsx_d3d12_backend.h"
#define MAX_DRAWS 64
__RECORD__
static struct {
    u32 width, height, draw_count, vb_offset, vp_vb_offset;
    u64 frame_count;
    int initialized;
    float clear_color[4];
    const rsx_state* current_rsx_state;
    D3D12DrawRecord draws[MAX_DRAWS];
} s_d3d;
static struct { u32 texture_mask; } s_rt_plan[MAX_DRAWS];
static u32 s_last_present_flip, s_clear_presents, s_dbg_clears_since_present;
static unsigned rendered, reported_flip;
static jmp_buf fatal_boundary;
static unsigned fatal_code;
static void capture_exit(UINT code) { fatal_code=code; longjmp(fatal_boundary, 1); }
#define ExitProcess capture_exit
int cellGcmOffsetIsDisplay(u32 offset) { return offset == 0x8000; }
unsigned cellGcm_flip_request_count(void) { return reported_flip; }
static int blink_dbg(void) { return 0; }
static void vp_vs_release_pins(void) {}
static void render_frame(void) { ++rendered; puts("SUBMIT"); }
__ABORT__
__RECT__
__CURRENT_RT__
__RECORD_META__
__CLEAR__
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
    _putenv_s("CLEAR_RGB", ""); _putenv_s("RSX_ACCUM_FRAME", "0");
    _putenv_s("RT_DISPLAY_BY_SIZE", "");
    rsx_state state={0};
    state.color_target=1; state.surface_color_offset[0]=0x8000;
    state.surface_format=0x108;
    state.surface_clip_w=state.surface_clip_h=8;
    state.scissor_w=state.scissor_h=4096;
    s_d3d.current_rsx_state=&state; s_d3d.width=s_d3d.height=8;
    s_d3d.initialized=1; s_d3d.draw_count=1;
    /* Pending display work carries the selector captured at its own draw.
     * A zeroed record now means NONE, not an implicit display draw. */
    s_d3d.draws[0].surface_meta.valid=1;
    s_d3d.draws[0].surface_meta.color_target=CELL_GCM_SURFACE_TARGET_0;
    s_d3d.clear_color[0]=0.125f; s_d3d.clear_color[1]=0.25f;
    s_d3d.clear_color[2]=0.5f; s_d3d.clear_color[3]=0.75f;
    const float previous[4]={0.125f,0.25f,0.5f,0.75f};
    u32 flags=0xf0;
    if (!strcmp(argv[1], "depth")) flags=1;
    else if (!strcmp(argv[1], "stencil")) flags=2;
    else if (!strcmp(argv[1], "depth-stencil")) flags=3;
    else if (!strcmp(argv[1], "empty")) flags=0;
    else if (!strcmp(argv[1], "mask")) flags=0x10;
    else if (!strcmp(argv[1], "scissor")) state.scissor_w=4;
    else if (!strcmp(argv[1], "clip")) state.surface_clip_h=4;
    else if (!strcmp(argv[1], "origin")) state.scissor_x=1;
    else if (!strcmp(argv[1], "outside")) state.scissor_x=10;
    else if (!strcmp(argv[1], "missing-state")) s_d3d.current_rsx_state=NULL;
    else if (!strcmp(argv[1], "mrt")) {
        state.color_target=0x13; state.surface_color_offset[1]=0x9000;
    }
    else if (!strcmp(argv[1], "mrt-zero")) state.color_target=CELL_GCM_SURFACE_TARGET_MRT1;
    else if (!strcmp(argv[1], "unknown-selector")) state.color_target=3;
    else if (!strcmp(argv[1], "none-selector")) state.color_target=CELL_GCM_SURFACE_TARGET_NONE;
    else if (!strcmp(argv[1], "single-b")) {
        state.color_target=CELL_GCM_SURFACE_TARGET_1;
        state.surface_color_offset[0]=0x1000; state.surface_color_offset[1]=0x8000;
    }
    else if (!strcmp(argv[1], "first")) s_d3d.draw_count=0;
    else if (!strcmp(argv[1], "pending-offscreen")) s_d3d.draws[0].rt_off=0x9000;
    else if (!strcmp(argv[1], "same-flip")) reported_flip=s_last_present_flip=1;
    else if (!strcmp(argv[1], "accumulation")) _putenv_s("RSX_ACCUM_FRAME", "1");
    else if (!strcmp(argv[1], "intermediate")) {
        s_d3d.draw_count=32; reported_flip=1;
        d3d12_clear(NULL,0xf0,0xff000000,1.0f,0);
        s_d3d.draw_count=1; memset(&s_d3d.draws[0],0,sizeof(s_d3d.draws[0]));
        s_d3d.draws[0].surface_meta.valid=1;
        s_d3d.draws[0].surface_meta.color_target=CELL_GCM_SURFACE_TARGET_0;
        reported_flip=2;
    }
    else if (!strcmp(argv[1], "full-depth")) flags=0xf3;
    else if (strcmp(argv[1], "full")) return 2;
    if (setjmp(fatal_boundary)) {
        if (fatal_code != 2 || rendered || s_d3d.draw_count != 1 ||
                memcmp(s_d3d.clear_color, previous, sizeof(previous))) return 4;
        puts("REJECTED exit=2 before color mutation or submission"); return 0;
    }
    d3d12_clear(NULL, flags, 0xff336699, 0.5f, 0x7f);
    if (!(flags & 0xf0)) {
        if (memcmp(s_d3d.clear_color, previous, sizeof(previous))) {
            puts("FAIL depth/stencil clear replaced display clear_color"); return 1;
        }
        if (rendered || s_d3d.draw_count != 1 || s_clear_presents ||
                s_dbg_clears_since_present) {
            puts("FAIL depth/stencil clear caused color submission/frame boundary"); return 1;
        }
    } else {
        int first=!strcmp(argv[1],"first"), intermediate=!strcmp(argv[1],"intermediate");
        int accumulated=first || intermediate || !strcmp(argv[1],"pending-offscreen") ||
            !strcmp(argv[1],"same-flip") || !strcmp(argv[1],"accumulation");
        unsigned expected_submits=accumulated ? (unsigned)intermediate : 1;
        unsigned expected_count=accumulated && !first ? 2 : 1;
        if (rendered != expected_submits || s_d3d.draw_count != expected_count ||
                s_clear_presents != expected_submits || s_dbg_clears_since_present != expected_submits ||
                s_d3d.clear_color[0] != 0.2f ||
                s_d3d.clear_color[1] != 0.4f || s_d3d.clear_color[2] != 0.6f ||
                s_d3d.clear_color[3] != 1.0f) {
            puts("FAIL supported full clear lost existing frame flow"); return 1;
        }
        const D3D12DrawRecord* queued=&s_d3d.draws[expected_count-1];
        if(!queued->is_vp || !queued->is_clear || queued->rt_off ||
                queued->surface_meta.color_offset[state.color_target==2 ? 1 : 0]!=0x8000 ||
                queued->surface_meta.clear_flags!=flags || queued->surface_meta.clear_depth!=0.5f ||
                queued->surface_meta.clear_stencil!=0x7f || queued->rt_fmt!=state.surface_format ||
                queued->cc[0]!=0.2f || queued->cc[1]!=0.4f || queued->cc[2]!=0.6f || queued->cc[3]!=1.0f) {
            puts("FAIL ordered display clear missing original metadata or color"); return 7;
        }
        if(!strcmp(argv[1],"same-flip") && s_last_present_flip!=1) return 8;
        if(intermediate && s_last_present_flip!=1) return 9;
    }
    puts("PASS display clear callback"); return 0;
}
'''


class DisplayClearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        end = backend.index("} D3D12DrawRecord;") + len("} D3D12DrawRecord;")
        source = DRIVER.replace("__RECORD__", backend[backend.rfind("typedef struct {", 0, end):end])
        for token, signature in (
                ("__ABORT__", "static void temporal_abort("),
                ("__RECT__", "static D3D12_RECT temporal_clear_rect("),
                ("__CURRENT_RT__", "static u32 current_rt_off("),
                ("__RECORD_META__", "static void record_surface_metadata("),
                ("__CLEAR__", "static void d3d12_clear(")):
            source = source.replace(token, function(backend, signature))
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-display-clear-")
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

    def assert_color_unchanged(self, mode):
        result = subprocess.run([str(self.executable), mode], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("SUBMIT", result.stdout)

    def test_depth_only_preserves_color_without_submission(self): self.assert_color_unchanged("depth")
    def test_stencil_only_preserves_color_without_submission(self): self.assert_color_unchanged("stencil")
    def test_depth_stencil_preserves_color_without_submission(self): self.assert_color_unchanged("depth-stencil")
    def test_empty_clear_preserves_color_without_submission(self): self.assert_color_unchanged("empty")

    def assert_rejected(self, mode):
        result = subprocess.run([str(self.executable), mode], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("REJECTED exit=2", result.stdout, mode + " silently submitted")
        self.assertIn("TEMPORAL_FATAL", result.stderr)
        self.assertIn("display clear", result.stderr)
        self.assertNotIn("SUBMIT", result.stdout)

    def test_partial_color_mask_aborts(self): self.assert_rejected("mask")
    def test_partial_scissor_aborts(self): self.assert_rejected("scissor")
    def test_partial_clip_aborts(self): self.assert_rejected("clip")
    def test_shifted_region_aborts(self): self.assert_rejected("origin")
    def test_empty_region_aborts(self): self.assert_rejected("outside")
    def test_missing_surface_state_aborts(self): self.assert_rejected("missing-state")
    def test_multiple_display_targets_abort(self): self.assert_rejected("mrt")
    def test_active_mrt_with_secondary_offset_zero_aborts(self): self.assert_rejected("mrt-zero")
    def test_unknown_color_target_selector_aborts(self): self.assert_rejected("unknown-selector")
    def test_disabled_color_target_selector_aborts(self): self.assert_rejected("none-selector")

    def test_full_display_clear_preserves_existing_frame_flow(self):
        for mode in ("full", "single-b", "full-depth"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.executable), mode], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("SUBMIT", result.stdout)

    def test_display_clear_is_recorded_in_new_or_accumulating_batch(self):
        for mode in ("first", "pending-offscreen", "same-flip", "accumulation", "intermediate"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.executable), mode], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS display clear callback", result.stdout)
