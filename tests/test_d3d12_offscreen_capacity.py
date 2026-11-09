"""Exercise production RT slot selection without a device or game assets.

The input is the 19 surface addresses from a local post-intro manifest.
Resource creation is a marker; the backend's actual selection and eviction
branches run. This does not validate pixels, dimensions, formats or GPU work.
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
#define CINTERFACE
#define COBJMACROS
#include "rsx_d3d12_guest_pipeline.h"
#include <cstdio>
#include <cstdlib>
typedef struct { void* res; void* up; u32 off, w, h, dxgi; int used; } OffRT;
#define MAX_OFF_RTS RSX_D3D12_MAX_OFF_RTS
static struct {
    void* rt_rtv_heap; void* srv_heap;
    u32 width, height; OffRT off_rt[MAX_OFF_RTS];
} s_d3d;
static void defer_resource_release(void*) { std::abort(); }
'''

RESOURCE_MARKER = r'''
    OffRT* r = &s_d3d.off_rt[slot];
    r->off = off; r->w = w; r->h = h; r->dxgi = want_fmt;
    r->res = (void*)(size_t)(slot + 1); r->used = 1;
    return slot;
}
'''

DRIVER = r'''
int main(int argc, char**) {
    const u32 offsets[] = {
        0x011C0000, 0x00AB8000, 0x00E3C000, 0x003B0000,
        0x00F22000, 0x0F6F0000, 0x00B9E000, 0x01008000,
        0x01170000, 0x0F2F0000, 0x0F730000, 0x0F750000,
        0x0F770000, 0x01080000, 0x010F8000, 0x01134000,
        0x0118E000, 0x00D06000, 0x00D1A000
    };
    s_d3d.rt_rtv_heap = s_d3d.srv_heap = (void*)1;
    s_d3d.width = 1280; s_d3d.height = 720;
    if (argc > 1) {
        // Simulate descriptor writes in the same heap used by draw0/t0.
        unsigned heap[RSX_D3D12_DRAW_SRV_BASE + 16] = {};
        const UINT draw = rsx_d3d12_guest_draw_srv_index(0, 0);
        heap[draw] = 0x12345678;
        for (UINT i = 0; i < MAX_OFF_RTS; ++i)
            heap[RSX_D3D12_RT_SRV_BASE + i] = 0xABC00000 + i;
        if (heap[draw] != 0x12345678) {
            std::puts("FAIL RT descriptors overwrite draw0/t0"); return 1;
        }
        std::puts("PASS disjoint descriptor windows"); return 0;
    }
    for (u32 off : offsets) {
        const int slot = off_rt_get(off, 1280, 720, 0);
        if (slot < 0) {
            std::printf("FAIL missing target %08X; display fallback would be selected\n", off);
            return 1;
        }
    }
    for (u32 off : offsets) {
        const int slot = off_rt_find(off);
        if (slot < 0 || s_d3d.off_rt[slot].off != off) return 1;
    }
    std::puts("PASS all 19 targets retained"); return 0;
}
'''


class OffscreenCapacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-offscreen-capacity-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        if "static int off_rt_get(" not in source:
            raise unittest.SkipTest("off_rt_get superseded by rsx_d3d12_linear_targets.h")
        begin = source.index("static DXGI_FORMAT rsx_surface_dxgi(u32 fmt)")
        end = source.index("\n    OffRT* r = &s_d3d.off_rt[slot];", source.index("static int off_rt_get("))
        probe = directory / "probe.cpp"
        probe.write_text(PRELUDE + source[begin:end] + RESOURCE_MARKER + DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        result = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"), "/I" + str(SDK / "include"),
            str(probe), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")
        ], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def test_post_intro_batch_retains_every_target(self):
        result = subprocess.run([str(self.executable)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rt_descriptors_cannot_overwrite_first_draw(self):
        result = subprocess.run([str(self.executable), "descriptors"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
