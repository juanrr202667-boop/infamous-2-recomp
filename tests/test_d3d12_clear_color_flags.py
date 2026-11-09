"""Run the actual ordered-clear branch with a command-list call recorder."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

PRELUDE = r'''
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#include <stdlib.h>
#include "rsx_d3d12_surface_metadata.h"
typedef struct {
    int is_clear;
    float cc[4];
    rsx_d3d12_surface_metadata surface_meta;
} D3D12DrawRecord;
static struct { ID3D12GraphicsCommandList* cmd_list; unsigned width, height; struct { unsigned w,h; } off_rt[4]; } s_d3d;
static unsigned color_calls, wrong_region;
static int s_had_direct_backbuffer = 0;
static void STDMETHODCALLTYPE mock_noop(void* self) { (void)self; }
static void temporal_after_op(unsigned d) { (void)d; }
__CLEAR_RECT_HELPER__
static void STDMETHODCALLTYPE capture_clear(ID3D12GraphicsCommandList* command,
        D3D12_CPU_DESCRIPTOR_HANDLE handle, const FLOAT color[4], UINT count,
        const D3D12_RECT* rects) {
    ++color_calls;
    if (count != 1 || !rects || rects[0].left != 2 || rects[0].top != 1 ||
        rects[0].right != 4 || rects[0].bottom != 4) ++wrong_region;
}
static D3D12_CPU_DESCRIPTOR_HANDLE off_rt_rtv(int slot) {
    D3D12_CPU_DESCRIPTOR_HANDLE handle = {(SIZE_T)(100 + slot)}; return handle;
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    _putenv_s("RT_CLEARDBG", "");
    ID3D12GraphicsCommandListVtbl table = {0};
    table.ClearRenderTargetView = capture_clear;
    table.ClearDepthStencilView = (void*)mock_noop;
    ID3D12GraphicsCommandList command = {&table}; s_d3d.cmd_list = &command;
    D3D12DrawRecord record = {0}; record.is_clear = 1;
    record.surface_meta.valid = 1;
    record.surface_meta.clip_x=1; record.surface_meta.clip_y=1;
    record.surface_meta.clip_w=4; record.surface_meta.clip_h=3;
    record.surface_meta.scissor_x=2; record.surface_meta.scissor_y=0;
    record.surface_meta.scissor_w=2; record.surface_meta.scissor_h=4;
    s_d3d.width=s_d3d.height=8;
    for (int i=0;i<4;++i) s_d3d.off_rt[i].w=s_d3d.off_rt[i].h=8;
    record.surface_meta.clear_flags = (u32)strtoul(argv[1], NULL, 0);
    const D3D12DrawRecord* dr = &record;
    int cur_rt = 0, cur_m[3] = {1, 2, 3};
    static unsigned long long s_off_rt_epoch;
    static struct { unsigned long long complete_at; } s_off_rt_views[32];
    D3D12_CPU_DESCRIPTOR_HANDLE rtv_handle = {50}, dsv_handle = {60};
    for (unsigned operation = 0; operation < 1; ++operation) {
    unsigned d=operation;
'''
SUFFIX = r'''
    }
    unsigned expected = (record.surface_meta.clear_flags & 0xF0) ? 4 : 0;
    if (color_calls != expected) {
        printf("FAIL flags=0x%X color_calls=%u expected=%u\n",
               record.surface_meta.clear_flags, color_calls, expected); return 1;
    }
    if (wrong_region) { puts("FAIL ordered clear ignored captured clip/scissor"); return 1; }
    puts("PASS ordered color-clear selection and region"); return 0;
}
'''


class ClearColorFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        anchor = backend.index("/* RT_CLEARDBG=1: clear offscreen")
        start = backend.rfind("if (dr->is_clear) {", 0, anchor)
        end = backend.index("/* Per-draw pipeline:", anchor)
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-clear-color-flags-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "probe.c"
        helper = ""
        if "static D3D12_RECT temporal_clear_rect(" in backend:
            helper_start=backend.index("static D3D12_RECT temporal_clear_rect(")
            helper_end=backend.index("\n}",helper_start)+2
            helper=backend[helper_start:helper_end]
        source.write_text(PRELUDE.replace("__CLEAR_RECT_HELPER__",helper) + backend[start:end] + SUFFIX, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"),
            str(source), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")],
            capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_depth_stencil_and_empty_clear_do_not_clear_color(self):
        for flags in ("1", "3", "0", "2", "0x80000003", "0xF0", "0xF1", "0xF3"):
            with self.subTest(flags=flags):
                result = subprocess.run([str(self.executable), flags], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
