"""Test that explicit depth/stencil clears are emitted and RT switches do not clear DSV."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = Path(os.environ.get("I2_SDK_DIR", ROOT / "tools/ps3recomp"))

PROBE_SOURCE = r'''
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#include <stdlib.h>
#include "rsx_d3d12_surface_metadata.h"

typedef struct {
    int is_vp;
    int is_clear;
    float cc[4];
    int rt_off;
    int rt_mrt[3];
    rsx_d3d12_surface_metadata surface_meta;
} D3D12DrawRecord;

static struct {
    ID3D12GraphicsCommandList* cmd_list;
    unsigned width, height;
    struct { unsigned w, h; } off_rt[4];
} s_d3d;

static unsigned s_depth_clear_calls = 0;
static unsigned s_color_clear_calls = 0;
static D3D12_CLEAR_FLAGS s_last_dsv_flags = (D3D12_CLEAR_FLAGS)0;
static FLOAT s_last_depth = -1.0f;
static UINT8 s_last_stencil = 0xFF;
static float vp_last_w = 0.0f, vp_last_h = 0.0f;
static int s_had_direct_backbuffer = 0;

static void STDMETHODCALLTYPE mock_ClearDepthStencilView(
    ID3D12GraphicsCommandList* self,
    D3D12_CPU_DESCRIPTOR_HANDLE DepthStencilView,
    D3D12_CLEAR_FLAGS ClearFlags,
    FLOAT Depth,
    UINT8 Stencil,
    UINT NumRects,
    const D3D12_RECT* pRects)
{
    (void)self; (void)DepthStencilView; (void)NumRects; (void)pRects;
    s_depth_clear_calls++;
    s_last_dsv_flags = ClearFlags;
    s_last_depth = Depth;
    s_last_stencil = Stencil;
}

static void STDMETHODCALLTYPE mock_ClearRenderTargetView(
    ID3D12GraphicsCommandList* self,
    D3D12_CPU_DESCRIPTOR_HANDLE RenderTargetView,
    const FLOAT ColorRGBA[4],
    UINT NumRects,
    const D3D12_RECT* pRects)
{
    (void)self; (void)RenderTargetView; (void)ColorRGBA; (void)NumRects; (void)pRects;
    s_color_clear_calls++;
}

static void STDMETHODCALLTYPE mock_noop(void* self) { (void)self; }

static D3D12_RECT temporal_clear_rect(const D3D12DrawRecord* dr, unsigned w, unsigned h) {
    (void)dr;
    D3D12_RECT r = {0, 0, (LONG)w, (LONG)h};
    return r;
}

static void temporal_after_op(unsigned d) { (void)d; }
static void off_rt_transition(int slot, D3D12_RESOURCE_STATES state) { (void)slot; (void)state; }
static D3D12_CPU_DESCRIPTOR_HANDLE off_rt_rtv(int slot) {
    (void)slot;
    D3D12_CPU_DESCRIPTOR_HANDLE h = {3};
    return h;
}

int main(int argc, char** argv) {
    if (argc < 2) return 2;
    unsigned test_mode = (unsigned)atoi(argv[1]);

    ID3D12GraphicsCommandListVtbl vtbl = {0};
    vtbl.ClearDepthStencilView = mock_ClearDepthStencilView;
    vtbl.ClearRenderTargetView = mock_ClearRenderTargetView;
    vtbl.OMSetRenderTargets = (void*)mock_noop;
    vtbl.RSSetViewports = (void*)mock_noop;
    vtbl.RSSetScissorRects = (void*)mock_noop;

    ID3D12GraphicsCommandList cmd = {&vtbl};
    s_d3d.cmd_list = &cmd;
    s_d3d.width = 1280; s_d3d.height = 720;
    for (int i = 0; i < 4; ++i) { s_d3d.off_rt[i].w = 1280; s_d3d.off_rt[i].h = 720; }

    D3D12_CPU_DESCRIPTOR_HANDLE dsv_handle = {1};
    D3D12_CPU_DESCRIPTOR_HANDLE rtv_handle = {2};

    if (test_mode == 1 || test_mode == 2) {
        D3D12DrawRecord dr_record = {0};
        dr_record.is_clear = 1;
        dr_record.is_vp = 1;
        dr_record.surface_meta.valid = 1;
        dr_record.surface_meta.clip_w = 1280;
        dr_record.surface_meta.clip_h = 720;
        dr_record.surface_meta.scissor_w = 1280;
        dr_record.surface_meta.scissor_h = 720;
        if (test_mode == 1) {
            dr_record.surface_meta.clear_flags = 0x01; /* CELL_GCM_CLEAR_Z */
            dr_record.surface_meta.clear_depth = 0.5f;
            dr_record.surface_meta.clear_stencil = 0;
        } else {
            dr_record.surface_meta.clear_flags = 0x03; /* CELL_GCM_CLEAR_Z | CELL_GCM_CLEAR_S */
            dr_record.surface_meta.clear_depth = 0.75f;
            dr_record.surface_meta.clear_stencil = 42;
        }
        const D3D12DrawRecord* dr = &dr_record;
        int cur_rt = 0;
        int cur_m[3] = {-1, -1, -1};
        for (unsigned d = 0; d < 1; d++) {
            __REPLAY_CLEAR_BLOCK__
        }

        if (test_mode == 1) {
            if (s_depth_clear_calls != 1) {
                printf("FAIL: expected 1 depth clear call, got %u\n", s_depth_clear_calls);
                return 1;
            }
            if (!(s_last_dsv_flags & D3D12_CLEAR_FLAG_DEPTH)) {
                printf("FAIL: missing D3D12_CLEAR_FLAG_DEPTH (flags=0x%X)\n", s_last_dsv_flags);
                return 1;
            }
            if (s_last_depth != 0.5f) {
                printf("FAIL: expected depth=0.5, got %f\n", s_last_depth);
                return 1;
            }
            if (s_color_clear_calls != 0) {
                printf("FAIL: unexpected color clear calls on depth-only clear: %u\n", s_color_clear_calls);
                return 1;
            }
            printf("PASS depth-only clear\n");
            return 0;
        } else {
            if (s_depth_clear_calls != 1) {
                printf("FAIL: expected 1 depth clear call, got %u\n", s_depth_clear_calls);
                return 1;
            }
            if ((s_last_dsv_flags & (D3D12_CLEAR_FLAG_DEPTH | D3D12_CLEAR_FLAG_STENCIL)) !=
                (D3D12_CLEAR_FLAG_DEPTH | D3D12_CLEAR_FLAG_STENCIL)) {
                printf("FAIL: missing depth/stencil flags: 0x%X\n", s_last_dsv_flags);
                return 1;
            }
            if (s_last_depth != 0.75f) {
                printf("FAIL: expected depth=0.75, got %f\n", s_last_depth);
                return 1;
            }
            if (s_last_stencil != 42) {
                printf("FAIL: expected stencil=42, got %u\n", s_last_stencil);
                return 1;
            }
            printf("PASS depth-stencil clear\n");
            return 0;
        }
    } else if (test_mode == 3) {
        D3D12DrawRecord dr_record = {0};
        dr_record.surface_meta.valid = 1;
        dr_record.surface_meta.clip_w = 1280;
        dr_record.surface_meta.clip_h = 720;
        const D3D12DrawRecord* dr = &dr_record;
        int no_color = 0;
        int cur_rt = 0, want = 1;
        int cur_m[3] = {-1, -1, -1}, wantm[3] = {-1, -1, -1};
        for (unsigned d = 0; d < 1; d++) {
            __REPLAY_RT_SWITCH_BLOCK__
        }

        if (s_depth_clear_calls != 0) {
            printf("FAIL: RT switch inopportunely cleared DSV (%u calls)!\n", s_depth_clear_calls);
            return 1;
        }
        printf("PASS RT switch preserves DSV\n");
        return 0;
    }

    return 2;
}
'''

class DepthClearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")

        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")

        # Extract the RT switch block (up to RT_CLEARDBG=2).
        rt_switch_anchor = "if (want != cur_rt || wantm[0] != cur_m[0]"
        rt_switch_start = backend.index(rt_switch_anchor)
        rt_switch_end = backend.index("/* RT_CLEARDBG=2:", rt_switch_start)
        rt_switch_code = backend[rt_switch_start:rt_switch_end] + "\ncur_rt = want;\n}"

        # Extract the clear block inside the temporal loop.
        loop_anchor = "for (u32 d = 0; d < s_d3d.draw_count"
        loop_pos = backend.index(loop_anchor)
        clear_start = backend.index("if (dr->is_clear) {", loop_pos)
        clear_end = backend.index("/* Per-draw pipeline:", clear_start)
        clear_code = backend[clear_start:clear_end]

        cls.directory = tempfile.TemporaryDirectory(prefix="i2-depth-clear-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "probe.c"

        code = PROBE_SOURCE.replace("__REPLAY_CLEAR_BLOCK__", clear_code)
        code = code.replace("__REPLAY_RT_SWITCH_BLOCK__", rt_switch_code)
        source.write_text(code, encoding="utf-8")

        cls.executable = directory / "probe.exe"
        built = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"),
            str(source), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")
        ], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_depth_only_clear_emits_dsv_clear(self):
        result = subprocess.run([str(self.executable), "1"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_depth_stencil_clear_emits_dsv_clear(self):
        result = subprocess.run([str(self.executable), "2"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rt_switch_does_not_clear_dsv(self):
        result = subprocess.run([str(self.executable), "3"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == "__main__":
    unittest.main()
