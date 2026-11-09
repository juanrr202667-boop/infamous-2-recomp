"""Compile and execute the production fragment sampler scanner on CPU.

Tiny synthetic guest programs cover static references, predicates, byte order,
inline payloads and bounds. No shader compiler, GPU or game data is used.
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

static void put_word(u8* bytes, u32 value) {
    u32 stored = (value << 16) | (value >> 16);
    bytes[0] = (u8)(stored >> 24); bytes[1] = (u8)(stored >> 16);
    bytes[2] = (u8)(stored >> 8); bytes[3] = (u8)stored;
}

static void instruction(u8* bytes, u32 opcode, u32 unit, u32 end,
        u32 src0, u32 src1, u32 src2) {
    put_word(bytes, (opcode << 24) | (unit << 17) | end);
    put_word(bytes + 4, src0);
    put_word(bytes + 8, src1);
    put_word(bytes + 12, src2);
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    u8 code[128] = {0};
    if (!strcmp(argv[1], "opcodes")) {
        const u32 opcodes[5] = {0x17, 0x18, 0x19, 0x31, 0x2F};
        const u32 units[5] = {0, 3, 7, 11, 15};
        const u32 masks[5] = {0x0001, 0x0008, 0x0080, 0x0800, 0x8000};
        for (u32 i = 0; i < 5; ++i) {
            instruction(code, opcodes[i], units[i], 1, 0, 0, 0);
            CHECK(rsx_fp_sampler_used_mask(code, 16) == masks[i]);
        }
        for (u32 i = 0; i < 5; ++i)
            instruction(code + i * 16, opcodes[i], units[i], i == 4, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 80) == 0x8889u);
    } else if (!strcmp(argv[1], "predicates")) {
        instruction(code, 0x17, 1, 0, 1u << 18, 0, 0);
        instruction(code + 16, 0x18, 9, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 32) == 0x0202u);
    } else if (!strcmp(argv[1], "literals")) {
        const u32 sources[4][3] = {{2,0,0}, {0,2,0}, {0,0,2}, {2,2,2}};
        for (u32 i = 0; i < 4; ++i) {
            instruction(code, 0x01, 0, 0, sources[i][0], sources[i][1], sources[i][2]);
            instruction(code + 16, 0x17, 15, 1, 0, 0, 0); /* Literal, not an instruction. */
            instruction(code + 32, 0x18, 3, 1, 0, 0, 0);
            CHECK(rsx_fp_sampler_used_mask(code, 48) == 0x0008u);
        }
    } else if (!strcmp(argv[1], "end")) {
        instruction(code, 0x17, 2, 1, 0, 0, 0);
        instruction(code + 16, 0x17, 15, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 32) == 0x0004u);
        CHECK(rsx_fp_sampler_used_mask(code, 0xFFFFFFFFu) == 0x0004u);
    } else if (!strcmp(argv[1], "end-literal")) {
        instruction(code, 0x17, 5, 1, 0, 0, 2);
        instruction(code + 16, 0x17, 12, 1, 0, 0, 0);
        instruction(code + 32, 0x17, 15, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 48) == 0x0020u);
        CHECK(rsx_fp_sampler_used_mask(code, 16) == 0xFFFFFFFFu);
        CHECK(rsx_fp_sampler_used_mask(code, 31) == 0xFFFFFFFFu);
    } else if (!strcmp(argv[1], "branch")) {
        instruction(code, 0x02, 14, 0, 2, 0x80000008u, 8);
        instruction(code + 16, 0x19, 7, 0, 0, 0, 0);
        instruction(code + 32, 0x00, 0, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 48) == 0x0080u);
    } else if (!strcmp(argv[1], "byte-order")) {
        const u8 raw[16] = {0x00,0x01,0x17,0x1E, 0,0,0,0, 0,0,0,0, 0,0,0,0};
        CHECK(rsx_fp_sampler_used_mask(raw, sizeof(raw)) == 0x8000u);
    } else if (!strcmp(argv[1], "no-samplers")) {
        instruction(code, 0x01, 15, 0, 0, 0, 0);
        instruction(code + 16, 0x02, 12, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 32) == 0);
    } else if (!strcmp(argv[1], "unknown-opcode")) {
        instruction(code, 0x3F, 5, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 16) == 0xFFFFFFFFu);
        instruction(code, 0x17, 3, 0, 0, 0, 0);
        instruction(code + 16, 0x3F, 5, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 32) == 0xFFFFFFFFu);
    } else if (!strcmp(argv[1], "unknown-flow")) {
        instruction(code, 0x06, 9, 1, 2, 0x80000002u, 2);
        CHECK(rsx_fp_sampler_used_mask(code, 16) == 0xFFFFFFFFu);
        instruction(code, 0x17, 3, 0, 0, 0, 0);
        instruction(code + 16, 0x06, 9, 1, 2, 0x80000002u, 2);
        CHECK(rsx_fp_sampler_used_mask(code, 32) == 0xFFFFFFFFu);
    } else if (!strcmp(argv[1], "known-isa")) {
        for (u32 op = 0; op <= 0x2A; ++op) {
            if (op == 0x17 || op == 0x18 || op == 0x19) continue;
            instruction(code, op, 15, 1, 0, 0, 0);
            CHECK(rsx_fp_sampler_used_mask(code, 16) == 0);
        }
        const u32 extra[9] = {0x2E, 0x36, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E};
        for (u32 i = 0; i < 9; ++i) {
            instruction(code, extra[i], 15, 1, 0, 0, 0);
            CHECK(rsx_fp_sampler_used_mask(code, 16) == 0);
        }
        for (u32 op = 0; op <= 5; ++op) {
            instruction(code, op, 15, 1, 2, 0x80000002u, 2);
            CHECK(rsx_fp_sampler_used_mask(code, 16) == 0);
        }
    } else if (!strcmp(argv[1], "duplicates")) {
        instruction(code, 0x17, 0, 0, 0, 0, 0);
        instruction(code + 16, 0x18, 0, 0, 0, 0, 0);
        instruction(code + 32, 0x2F, 15, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 48) == 0x8001u);
    } else if (!strcmp(argv[1], "bounds")) {
        CHECK(rsx_fp_sampler_used_mask(NULL, 16) == 0xFFFFFFFFu);
        CHECK(rsx_fp_sampler_used_mask(code, 0) == 0xFFFFFFFFu);
        CHECK(rsx_fp_sampler_used_mask(code, 15) == 0xFFFFFFFFu);
        instruction(code, 0x17, 3, 0, 0, 0, 0);
        instruction(code + 16, 0x17, 15, 1, 0, 0, 0);
        CHECK(rsx_fp_sampler_used_mask(code, 16) == 0xFFFFFFFFu);
        CHECK(rsx_fp_sampler_used_mask(code, 31) == 0xFFFFFFFFu);
        CHECK(rsx_fp_sampler_used_mask(code, 32) == 0x8008u);
    } else {
        return 2;
    }
    puts("PASS static fragment sampler references");
    return 0;
}
'''


class FragmentSamplerMaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-fp-sampler-mask-")
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

    def test_all_five_sampling_opcodes_contribute_unit_bits(self):
        self.run_probe("opcodes")

    def test_conditional_and_never_predicates_keep_static_references(self):
        self.run_probe("predicates")

    def test_inline_literals_are_skipped_once_for_any_const_operand(self):
        self.run_probe("literals")

    def test_first_end_excludes_trailing_programs(self):
        self.run_probe("end")

    def test_end_instruction_still_requires_its_literal_payload(self):
        self.run_probe("end-literal")

    def test_branch_metadata_does_not_consume_a_literal_slot(self):
        self.run_probe("branch")

    def test_raw_guest_bytes_use_big_endian_half_word_swap(self):
        self.run_probe("byte-order")

    def test_nontexture_opcodes_do_not_create_sampler_references(self):
        self.run_probe("no-samplers")

    def test_unknown_opcode_invalidates_empty_and_partial_masks(self):
        self.run_probe("unknown-opcode")

    def test_unknown_flow_invalidates_empty_and_partial_masks(self):
        self.run_probe("unknown-flow")

    def test_known_nontexture_isa_and_flow_remain_accepted(self):
        self.run_probe("known-isa")

    def test_duplicate_units_are_unioned_including_unit_fifteen(self):
        self.run_probe("duplicates")

    def test_malformed_or_truncated_programs_do_not_report_empty_usage(self):
        self.run_probe("bounds")


if __name__ == "__main__":
    unittest.main()
