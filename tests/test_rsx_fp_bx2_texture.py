"""The _bx2 (EXP_TEX) fetch modifier on the production fragment decompiler.

A texture fetch whose destination word carries bit 21 returns x*2-1 on every
lane: the hardware re-centres a signed value fetched from an unsigned texture
(RPCS3 FragmentProgramDecompiler, _process_texel). The program below is a
synthetic word stream, so a pass proves the modifier is emitted, not that any
title renders correctly.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

DRIVER = r'''
#include "rsx_fp_decompiler.h"
#include <stdio.h>
#include <string.h>

#define CHECK(condition) do { \
    if (!(condition)) { printf("FAIL line %d: %s\n", __LINE__, #condition); return 1; } \
} while (0)

#define OPC(x)      ((u32)(x) << 24)
#define OUTMASK_ALL (0xFu << 9)
#define END         (1u << 0)
#define TEX_UNIT(u) ((u32)(u) << 17)
#define EXP_TEX     (1u << 21)
#define T_TEMP      0u
#define SWZ_IDENT   ((0u << 9) | (1u << 11) | (2u << 13) | (3u << 15))
#define REG(i)      ((u32)(i) << 2)
#define EXEC_ALWAYS ((7u << 18) | (0u << 21) | (1u << 23) | (2u << 25) | (3u << 27))

static void put_word(u8* p, u32 h) {
    u32 be = (h << 16) | (h >> 16);
    p[0] = (u8)(be >> 24); p[1] = (u8)(be >> 16);
    p[2] = (u8)(be >> 8);  p[3] = (u8)be;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    static char hlsl[65536];
    u8 program[16];
    u32 dest = OPC(0x17) | OUTMASK_ALL | TEX_UNIT(0) | END;

    if (!strcmp(argv[1], "bx2")) dest |= EXP_TEX;
    else if (strcmp(argv[1], "plain")) return 2;

    put_word(program + 0, dest);
    put_word(program + 4, T_TEMP | REG(1) | SWZ_IDENT | EXEC_ALWAYS);
    put_word(program + 8, 0);
    put_word(program + 12, 0);

    int count = rsx_fp_decompile(program, sizeof program, RSX_FP_CTRL_AUTO, hlsl, sizeof hlsl);
    CHECK(count > 0);
    CHECK(strstr(hlsl, "Sample(") != NULL);

    if (!strcmp(argv[1], "bx2"))
        CHECK(strstr(hlsl, "* 2.0 - 1.0") != NULL);
    else
        CHECK(strstr(hlsl, "* 2.0 - 1.0") == NULL);

    puts("PASS bx2 fetch");
    return 0;
}
'''


class Bx2TextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-fp-bx2-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        common = [compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
                  "/I" + str(SDK / "libs/video"), "/I" + str(SDK / "include")]
        library = directory / "decompiler.obj"
        cls.compilation = subprocess.run(common + [
            "/c", str(SDK / "libs/video/rsx_fp_decompiler.c"), "/Fo" + str(library),
        ], capture_output=True, text=True)
        if not cls.compilation.returncode:
            probe_object = directory / "probe.obj"
            cls.compilation = subprocess.run(common + [
                "/c", str(probe), "/Fo" + str(probe_object),
            ], capture_output=True, text=True)
        if not cls.compilation.returncode:
            cls.compilation = subprocess.run([
                compiler, "/nologo", str(probe_object), str(library),
                "/Fe" + str(cls.executable),
            ], capture_output=True, text=True)

    def run_probe(self, mode):
        self.assertEqual(self.compilation.returncode, 0,
                         self.compilation.stdout + self.compilation.stderr)
        result = subprocess.run([str(self.executable), mode], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exp_tex_fetch_is_recentred(self):
        self.run_probe("bx2")

    def test_plain_fetch_is_not_recentred(self):
        self.run_probe("plain")


if __name__ == "__main__":
    unittest.main()