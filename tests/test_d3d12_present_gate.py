"""Execute the backend's actual present function without a device or game.

Device submission and the unexpected fatal boundary are doubles. The exact
production callbacks and surface helpers run against synthetic recorded
batches. This tests when submission happens, not pixels or GPU completion.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

PRELUDE = r'''
#include "rsx_d3d12_backend.h"
#include "rsx_d3d12_surface_metadata.h"
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* BACKEND_RECORD */
static struct {
    u32 width, height, draw_count, vb_offset, vp_vb_offset; int initialized;
    u64 last_fps_time; u32 fps;
    float clear_color[4]; const rsx_state* current_rsx_state; D3D12DrawRecord draws[2048];
    struct { int pinned; } vp_vs[2]; int vp_vs_n;
} s_d3d;
#define MAX_DRAWS 2048
static unsigned s_clear_presents, s_dbg_clears_since_present, s_last_present_flip;
static void* s_composite_src;
static int s_present_this_frame = 1;
static unsigned flip_count, submissions;
static int submitted_display = -1;
static void temporal_abort(u32 draw, int unit, const char* reason) {
    fprintf(stderr, "FAIL unexpected temporal fatal: %s\n", reason); exit(90);
}
static int blink_dbg(void) { return 0; }
unsigned cellGcm_flip_request_count(void) { return flip_count; }
int cellGcmOffsetIsDisplay(u32 offset) { return offset == 0x3B0000; }
void rsx_reset_upload_claims(void) { abort(); }
static void render_frame(void) {
    ++submissions;
    submitted_display = s_present_this_frame;
    s_d3d.draw_count = 0;
}
'''

DRIVER = r'''
static void record_display_draw(unsigned index) {
    D3D12DrawRecord* draw = &s_d3d.draws[index];
    memset(draw, 0, sizeof(*draw));
    draw->is_vp = 1; draw->vertex_count = 3;
    draw->rt_w = s_d3d.width; draw->rt_h = s_d3d.height;
    draw->rt_fmt = s_d3d.current_rsx_state->surface_format;
    record_surface_metadata(draw, 0, 1.0f, 0);
}
static void record_offscreen_draw(unsigned index) {
    const rsx_state* previous = s_d3d.current_rsx_state;
    rsx_state offscreen = *previous;
    offscreen.surface_color_offset[0] = 0xAB8000;
    s_d3d.current_rsx_state = &offscreen;
    record_display_draw(index);
    s_d3d.draws[index].rt_off = 0xAB8000;
    s_d3d.current_rsx_state = previous;
}
static int clear_only_present_case(const char* mode, rsx_state* state) {
    const int callback = strstr(mode, "callback") != NULL;
    const int depth_only = strstr(mode, "depth-only") != NULL;
    const int offscreen = strstr(mode, "offscreen-clear") != NULL;
    const int presentable = !depth_only && !offscreen;
    s_d3d.draw_count = 0;
    if (depth_only) {
        /* Keep real offscreen work pending, so an empty boot/idle present
         * cannot be mistaken for presentation caused by a depth clear. */
        record_offscreen_draw(0);
        s_d3d.draw_count = 1;
    }
    if (offscreen) state->surface_color_offset[0] = 0xAB8000;
    d3d12_clear(NULL, depth_only ? 1u : 0xF0u, 0xFF336699, 1.0f, 0);
    if (submissions || s_d3d.draw_count != 1 ||
            (!depth_only && (!s_d3d.draws[0].is_clear ||
                            s_d3d.draws[0].surface_meta.clear_flags != 0xF0u))) {
        puts("FAIL clear callback did not preserve one pending operation"); return 1;
    }
    if (callback) d3d12_present(NULL, 0);
    else rsx_d3d12_backend_present();
    const unsigned expected_submissions = callback && !presentable ? 0u : 1u;
    const unsigned expected_flip = !callback && presentable ? flip_count : 50u;
    printf("%s: submissions=%u display=%d consumed_flip=%u pending=%u\n",
           mode, submissions, submitted_display, s_last_present_flip, s_d3d.draw_count);
    if (submissions != expected_submissions ||
            (submissions && submitted_display != presentable) || s_last_present_flip != expected_flip) {
        puts("FAIL clear-only presentation gate or flip consumption"); return 1;
    }
    if (!callback && presentable) {
        /* A second wrapper call must preserve the next batch for this flip. */
        record_display_draw(0); s_d3d.draw_count = 1;
        rsx_d3d12_backend_present();
        if (submissions != 1 || s_d3d.draw_count != 1) {
            puts("FAIL clear-only flip was not consumed once"); return 1;
        }
    }
    puts("PASS clear-only presentation case"); return 0;
}
static int no_color_present_case(const char* mode) {
    D3D12DrawRecord* draw=&s_d3d.draws[0];
    draw->surface_meta.color_target=CELL_GCM_SURFACE_TARGET_NONE;
    draw->cmask=0;
    draw->rt_off=strstr(mode,"registered") ? 0x3B0000 : 0;
    if(strstr(mode,"clear-boundary")) {
        d3d12_clear(NULL,0xF0,0xFF336699,1.0f,0);
        if(submissions || s_d3d.draw_count!=2 || s_last_present_flip!=50) {
            puts("FAIL no-color draw caused display-clear frame boundary"); return 1;
        }
    } else if(strstr(mode,"callback")) {
        d3d12_present(NULL,0);
        if(submissions || s_d3d.draw_count!=1 || s_last_present_flip!=50) {
            puts("FAIL no-color draw caused callback presentation"); return 1;
        }
    } else {
        rsx_d3d12_backend_present();
        if(submissions!=1 || submitted_display!=0 || s_last_present_flip!=50) {
            puts("FAIL no-color flush presented or consumed the composite flip"); return 1;
        }
        record_display_draw(0);s_d3d.draw_count=1;
        rsx_d3d12_backend_present();
        if(submissions!=2 || submitted_display!=1 || s_last_present_flip!=51) {
            puts("FAIL no-color flush prevented following display composite"); return 1;
        }
    }
    puts("PASS no-color presentation case");return 0;
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    _putenv_s("VP_SUBMIT", "0"); _putenv_s("TEX_REMAP", "0");
    _putenv_s("GCM_GUEST_FB", "");
    _putenv_s("RSX_ACCUM_FRAME", "0"); _putenv_s("CLEAR_RGB", "");
    _putenv_s("RT_DISPLAY_BY_SIZE", "");
    rsx_state state = {0};
    state.color_target = 1; state.surface_color_offset[0] = 0x3B0000;
    state.surface_format = 0x108;
    state.surface_clip_w = state.surface_clip_h = 8;
    state.scissor_w = state.scissor_h = 4096;
    s_d3d.current_rsx_state = &state; s_d3d.width = s_d3d.height = 8;
    s_d3d.initialized = 1; s_d3d.draw_count = 1;
    record_display_draw(0);
    s_d3d.vp_vs_n = 2; s_d3d.vp_vs[0].pinned = s_d3d.vp_vs[1].pinned = 1;
    s_clear_presents = 1; s_last_present_flip = 50; flip_count = 51;
    if(!strncmp(argv[1],"none-",5))return no_color_present_case(argv[1]);
    if (!strcmp(argv[1], "clear-only-wrapper") || !strcmp(argv[1], "clear-only-callback") ||
            !strcmp(argv[1], "depth-only-wrapper") || !strcmp(argv[1], "depth-only-callback") ||
            !strcmp(argv[1], "offscreen-clear-wrapper") || !strcmp(argv[1], "offscreen-clear-callback"))
        return clear_only_present_case(argv[1], &state);
    unsigned expected = 1; int display = 1;
    if (!strcmp(argv[1], "same-flip")) { flip_count = 50; expected = 0; }
    else if (!strcmp(argv[1], "empty")) { s_d3d.draw_count = 0; expected = 0; }
    else if (!strcmp(argv[1], "offscreen")) { record_offscreen_draw(0); display = 0; }
    else if (!strcmp(argv[1], "boot")) { s_clear_presents = flip_count = 0; }
    else if (!strcmp(argv[1], "deferred-clear")) {
        _putenv_s("RSX_ACCUM_FRAME", "1");
        d3d12_clear(NULL, 0xF0, 0, 1.0f, 0);
    }
    else if (!strcmp(argv[1], "empty-clear")) {
        const float previous[4] = {0.125f, 0.25f, 0.5f, 0.75f};
        memcpy(s_d3d.clear_color, previous, sizeof(previous));
        d3d12_clear(NULL, 0, 0, 1.0f, 0);
        if (submissions || s_d3d.draw_count != 1 ||
                memcmp(s_d3d.clear_color, previous, sizeof(previous)) ||
                !s_d3d.vp_vs[0].pinned || !s_d3d.vp_vs[1].pinned) {
            puts("FAIL empty clear changed color or consumed pending frame"); return 1;
        }
    }
    else if (!strcmp(argv[1], "clear-consumed")) {
        d3d12_clear(NULL, 0xF0, 0, 1.0f, 0);
        s_d3d.draw_count = 1;
    }
    else if (!strcmp(argv[1], "complex-to-video")) {
        s_d3d.draw_count = 1373;
        d3d12_clear(NULL, 0xF0, 0, 1.0f, 0);
        record_display_draw(0);
        s_d3d.draw_count = 1; flip_count = 52;
        d3d12_clear(NULL, 0xF0, 0, 1.0f, 0);
        expected = 2;
    }
    else if (strcmp(argv[1], "new-flip")) return 2;
    if (!strcmp(argv[1], "deferred-clear") &&
            (!s_d3d.vp_vs[0].pinned || !s_d3d.vp_vs[1].pinned)) {
        puts("FAIL deferred clear released pending shader pins"); return 1;
    }
    if ((!strcmp(argv[1], "clear-consumed") || !strcmp(argv[1], "complex-to-video")) &&
            (s_d3d.vp_vs[0].pinned || s_d3d.vp_vs[1].pinned)) {
        puts("FAIL consumed clear retained old shader pins"); return 1;
    }
    rsx_d3d12_backend_present();
    if (submissions != expected || (expected && submitted_display != display)) {
        printf("FAIL %s: submissions=%u expected=%u display=%d\n", argv[1], submissions, expected, submitted_display);
        return 1;
    }
    if (expected && flip_count) {
        if (!display) {
            /* Offscreen work can flush before the composite of this flip. */
            record_display_draw(0); s_d3d.draw_count = 1;
            rsx_d3d12_backend_present(); ++expected;
            if (submissions != expected || submitted_display != 1) {
                printf("FAIL offscreen flush consumed composite flip\n"); return 1;
            }
        }
        /* The next frame's head must survive a second call for the same flip. */
        record_display_draw(0); s_d3d.draw_count = 1;
        rsx_d3d12_backend_present();
        if (submissions != expected || s_d3d.draw_count != 1) {
            printf("FAIL %s: duplicate flip consumed next batch\n", argv[1]); return 1;
        }
    }
    printf("PASS %s\n", argv[1]); return 0;
}
'''


class PresentGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-present-gate-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        start = source.index("void rsx_d3d12_backend_present(void)\n{")
        end = source.index("\n#else /* !_WIN32 */", start)
        probe = directory / "probe.c"
        clear_start = source.index("static void d3d12_clear(void* ud,")
        clear_end = source.index("static void d3d12_set_render_target", clear_start)
        pins_start = source.index("static void vp_vs_release_pins(void)")
        pins_end = source.index("static void vp_vs_cache_clear(void)", pins_start)
        dependencies = ""
        for signature in ("static void record_surface_metadata(", "static D3D12_RECT temporal_clear_rect(",
                          "static u32 current_rt_off("):
            helper_start = source.index(signature)
            helper_end = source.index("\n}", helper_start) + 2
            dependencies += source[helper_start:helper_end] + "\n"
        record_end = source.index("} D3D12DrawRecord;") + len("} D3D12DrawRecord;")
        record_start = source.rfind("typedef struct {", 0, record_end)
        prelude = PRELUDE.replace("/* BACKEND_RECORD */", source[record_start:record_end])
        present_start = source.index("static void d3d12_present(void* ud,")
        present_end = source.index("\n}", present_start) + 2
        probe.write_text(prelude + dependencies + source[pins_start:pins_end] + source[present_start:present_end] +
                         source[clear_start:clear_end] +
                         source[start:end] + DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        result = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "runtime/platform"),
            "/I" + str(SDK / "libs/video"), str(probe),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def test_completed_flip_submits_small_batch_without_repeating(self):
        for mode in ("new-flip", "same-flip", "empty", "offscreen", "boot",
                     "deferred-clear", "clear-consumed", "complex-to-video", "empty-clear"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.executable), mode], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_display_color_clear_only_wrapper_presents_and_consumes_flip(self):
        result = subprocess.run([str(self.executable), "clear-only-wrapper"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_display_color_clear_only_callback_presents(self):
        result = subprocess.run([str(self.executable), "clear-only-callback"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_depth_only_and_offscreen_clears_do_not_make_batch_presentable(self):
        for mode in ("depth-only-wrapper", "depth-only-callback",
                     "offscreen-clear-wrapper", "offscreen-clear-callback"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.executable), mode], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_color_draw_never_makes_display_content_or_consumes_composite_flip(self):
        for mode in ("none-zero", "none-registered", "none-callback", "none-clear-boundary",
                     "none-registered-clear-boundary"):
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.executable), mode], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
