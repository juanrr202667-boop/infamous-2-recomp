"""Compile and run the production fragment decompiler on a predicated program.

The RSX writes a destination per-component only where the execution condition
holds. A real branch (`? :`) is required: `lerp(dst, _v, mask)` is arithmetic
(`dst + mask * (_v - dst)`), so an Inf/NaN in the operand that is *not* selected
poisons the result (0 * Inf = NaN), and a register holding raw bits (a packed
word) loses denormal/NaN patterns through the ALU.

The fixture is a synthetic word stream, not game code: a pass proves the
decompiler emits a select rather than a blend, not that any title renders
correctly.
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
#define T_TEMP      0u
#define SWZ_IDENT   ((0u << 9) | (1u << 11) | (2u << 13) | (3u << 15))
#define REG(i)      ((u32)(i) << 2)
#define EXEC_LT     (1u << 18)   /* only the LT condition set == predicated */

static void put_word(u8* p, u32 h) {
    u32 be = (h << 16) | (h >> 16);
    p[0] = (u8)(be >> 24); p[1] = (u8)(be >> 16);
    p[2] = (u8)(be >> 8);  p[3] = (u8)be;
}

static int decompile(const u8* program, u32 bytes, char* hlsl, u32 size) {
    int count = rsx_fp_decompile(program, bytes, RSX_FP_CTRL_AUTO, hlsl, size);
    if (count <= 0) { printf("decompile returned %d\n", count); return 0; }
    return 1;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    static char hlsl[65536];
    u8 program[16];
    u32 exec = EXEC_LT;

    if (!strcmp(argv[1], "unconditional")) exec = (7u << 18);  /* lt|eq|gr */

    /* ADD r0, r1, r0 with all components written, conditional per exec bits. */
    put_word(program + 0, OPC(0x02) | OUTMASK_ALL | END);
    put_word(program + 4, T_TEMP | REG(1) | SWZ_IDENT | exec);
    put_word(program + 8, T_TEMP | REG(0) | SWZ_IDENT | exec);
    put_word(program + 12, 0);
    CHECK(decompile(program, sizeof program, hlsl, sizeof hlsl));

    if (!strcmp(argv[1], "predicated")) {
        CHECK(strstr(hlsl, "? _v.") != NULL);
        CHECK(strstr(hlsl, "lerp(") == NULL);
        CHECK(strstr(hlsl, "float4 _p =") != NULL);
    } else if (!strcmp(argv[1], "unconditional")) {
        CHECK(strstr(hlsl, "lerp(") == NULL);
    } else {
        return 2;
    }
    puts("PASS predicated write");
    return 0;
}
'''


class PredicatedWriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-fp-pred-")
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

    def test_predicated_write_uses_a_select_not_lerp(self):
        self.run_probe("predicated")

    def test_unconditional_write_has_no_blend(self):
        self.run_probe("unconditional")


if __name__ == "__main__":
    unittest.main()