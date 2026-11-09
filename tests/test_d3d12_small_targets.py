"""Run the production surface selector with valid small render targets.

Only the display-offset query is a double. No GPU or game assets are used.
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
#include <stdio.h>
#include <stdlib.h>
static struct {
    const rsx_state* current_rsx_state; u32 width, height;
} s_d3d;
int cellGcmOffsetIsDisplay(u32 off) { return off == 0x3B0000; }
'''

DRIVER = r'''
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    rsx_state state = {0};
    state.color_target = 1;
    state.surface_color_offset[0] = 0xD06000;
    state.surface_clip_w = (u32)strtoul(argv[1], NULL, 10);
    state.surface_clip_h = (u32)strtoul(argv[2], NULL, 10);
    s_d3d.current_rsx_state = &state;
    s_d3d.width = 1280; s_d3d.height = 720;
    u32 w = 0xFFFFFFFF, h = 0xFFFFFFFF, mrt[3];
    u32 off = current_rt_off(&w, &h, mrt);
    printf("%u %u %u\n", off, w, h);
    return 0;
}
'''


class SmallTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-small-target-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        start = source.index("static u32 current_rt_off(")
        end = source.index("\n}\n", start) + len("\n}\n")
        probe = directory / "probe.c"
        probe.write_text(PRELUDE + source[start:end] + DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        result = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"), "/I" + str(SDK / "include"),
            str(probe), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")
        ], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def select(self, width, height):
        env = os.environ.copy()
        for key in ("RT_DISPLAY_BY_SIZE", "RT_SEQDBG", "RT_OFFDBG"):
            env.pop(key, None)
        result = subprocess.run([str(self.executable), str(width), str(height)],
                                env=env, capture_output=True, text=True, check=True)
        return tuple(map(int, result.stdout.split()))

    def test_downsample_pyramid_keeps_20_by_11(self):
        self.assertEqual(self.select(20, 11), (0xD06000, 20, 11))

    def test_single_pixel_target_is_not_replaced_by_window_size(self):
        self.assertEqual(self.select(1, 1), (0xD06000, 1, 1))

    def test_standard_surface_is_preserved(self):
        self.assertEqual(self.select(1280, 720), (0xD06000, 1280, 720))

    def test_zero_dimension_retains_the_existing_fallback(self):
        self.assertEqual(self.select(20, 0), (0xD06000, 0, 0))


if __name__ == "__main__":
    unittest.main()
