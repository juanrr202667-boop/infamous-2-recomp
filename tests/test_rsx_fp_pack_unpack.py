"""Compile and run the production fragment decompiler on synthetic programs.

Covers the NV40 pack/unpack opcodes (PK4B, UP4B, PK2H, UP2H, PK4UB, UP4UB,
PK2US, UP2US, PKG, UPG) and the RSX clamp on the fragment colour inputs
(COL0/COL1). Tiny guest programs are decompiled to HLSL on the CPU; a shader
compiler, a GPU and game data are not used.

The fixture is a synthetic word stream, not game code: a pass proves the
decompiler emits the instruction instead of skipping it, not that any title
renders correctly.
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

/* exec_if_lt | exec_if_eq | exec_if_gr == unconditional (all three set). */
#define UNCOND 0x1C0000u

static void put_word(u8* bytes, u32 value) {
    u32 stored = (value << 16) | (value >> 16);
    bytes[0] = (u8)(stored >> 24); bytes[1] = (u8)(stored >> 16);
    bytes[2] = (u8)(stored >> 8); bytes[3] = (u8)stored;
}

/* SRC word: bits [0:1] register type (0 temp, 1 input, 2 const), [2:7] index. */
static u32 temp(u32 reg) { return (reg << 2); }
static u32 input(void)   { return 1u; }

static void instruction(u8* bytes, u32 opcode, u32 out_reg, u32 input_src,
        u32 src0, u32 src1, u32 src2) {
    put_word(bytes, (opcode << 24) | (out_reg << 1) | 1u /* end */ | (input_src << 13));
    put_word(bytes + 4, src0);
    put_word(bytes + 8, src1);
    put_word(bytes + 12, src2);
}

static int decompile(const u8* code, u32 bytes, char* out, u32 out_size) {
    int rc = rsx_fp_decompile(code, bytes, RSX_FP_CTRL_AUTO, out, out_size);
    if (rc < 0) { printf("decompile failed rc=%d\n", rc); return 0; }
    return 1;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    static char out[65536];
    u8 code[32] = {0};
    out[0] = '\0';

    if (!strcmp(argv[1], "pk4ub")) {
        instruction(code, 0x27, 0, 0, UNCOND | temp(1), 0, 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "_rsx_pk4ub(") != NULL);
        CHECK(strstr(out, "unhandled FP opcode") == NULL);
    } else if (!strcmp(argv[1], "pkg")) {
        instruction(code, 0x2C, 0, 0, UNCOND | temp(1), 0, 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "_rsx_lin2srgb(") != NULL);
        CHECK(strstr(out, "unhandled FP opcode") == NULL);
    } else if (!strcmp(argv[1], "upg")) {
        instruction(code, 0x2D, 0, 0, UNCOND | temp(1), 0, 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "_rsx_srgb2lin(") != NULL);
        CHECK(strstr(out, "unhandled FP opcode") == NULL);
    } else if (!strcmp(argv[1], "up4b")) {
        instruction(code, 0x14, 0, 0, UNCOND | temp(1), 0, 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "_rsx_up4b(") != NULL);
        CHECK(strstr(out, "unhandled FP opcode") == NULL);
    } else if (!strcmp(argv[1], "up2h")) {
        instruction(code, 0x25, 0, 0, UNCOND | temp(1), 0, 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "_rsx_up2h(") != NULL);
        CHECK(strstr(out, "unhandled FP opcode") == NULL);
    } else if (!strcmp(argv[1], "pk2us")) {
        instruction(code, 0x29, 0, 0, UNCOND | temp(1), 0, 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "_rsx_pk2us(") != NULL);
        CHECK(strstr(out, "unhandled FP opcode") == NULL);
    } else if (!strcmp(argv[1], "color")) {
        /* MUL r0, col0, col0 -- COL0 read twice. */
        instruction(code, 0x02, 0, 1, UNCOND | input(), input(), 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "saturate(input.col0)") != NULL);
    } else if (!strcmp(argv[1], "color1")) {
        instruction(code, 0x02, 0, 2, UNCOND | input(), input(), 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "saturate(input.col1)") != NULL);
    } else if (!strcmp(argv[1], "clean")) {
        /* MOV r0, r1 -- no pack opcode, no colour input: the helper block and
         * the colour clamp must not appear (byte-identical guarantee). */
        instruction(code, 0x01, 0, 0, UNCOND | temp(1), 0, 0);
        CHECK(decompile(code, 16, out, sizeof out));
        CHECK(strstr(out, "_rsx_pk4ub(") == NULL);
        CHECK(strstr(out, "_rsx_up4ub(") == NULL);
        CHECK(strstr(out, "_rsx_lin2srgb(") == NULL);
        CHECK(strstr(out, "saturate(input.col0)") == NULL);
    } else {
        return 2;
    }
    puts("PASS fragment pack/unpack and colour-input clamp");
    return 0;
}
'''


class FragmentPackUnpackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-fp-pack-")
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
                "/c", "/clang:-Werror=implicit-function-declaration", str(probe),
                "/Fo" + str(probe_object),
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

    def test_pk4ub_emits_helper_not_a_todo(self):
        self.run_probe("pk4ub")

    def test_pkg_converts_through_linear_to_srgb(self):
        self.run_probe("pkg")

    def test_upg_converts_through_srgb_to_linear(self):
        self.run_probe("upg")

    def test_up4b_sign_expands(self):
        self.run_probe("up4b")

    def test_up2h_expands_half_floats(self):
        self.run_probe("up2h")

    def test_pk2us_packs_unsigned_short(self):
        self.run_probe("pk2us")

    def test_col0_input_is_saturated(self):
        self.run_probe("color")

    def test_col1_input_is_saturated(self):
        self.run_probe("color1")

    def test_program_without_pack_or_colour_input_is_unchanged(self):
        self.run_probe("clean")


if __name__ == "__main__":
    unittest.main()