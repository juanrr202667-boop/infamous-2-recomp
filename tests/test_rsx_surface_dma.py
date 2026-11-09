"""Exercise real RSX method dispatch for raw surface DMA metadata on CPU.

The probe includes the production command processor. Only unrelated VM writes
and milestone logging are doubles; reaching either aborts the probe.
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
#include "rsx_commands.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void vm_write32(uint32_t address, uint32_t value) {
    (void)address; (void)value; abort();
}
void ps3_ms(const char* key) { (void)key; abort(); }
#include "rsx_commands.c"

#define CHECK(condition) do { \
    if (!(condition)) { printf("FAIL line %d: %s\n", __LINE__, #condition); return 1; } \
} while (0)

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    rsx_state state;
    rsx_state_init(&state);
    if (!strcmp(argv[1], "mapping")) {
        CHECK(state.surface_dma_seen == 0);
        CHECK(rsx_process_method(&state, 0x018C, 0xFEED0001u) == 0);
        CHECK(state.surface_dma_seen == 0x02u);
        CHECK(state.surface_color_dma[1] == 0xFEED0001u);
        CHECK(state.surface_color_dma[0] == 0 && state.surface_color_dma[2] == 0);
        CHECK(state.surface_color_dma[3] == 0 && state.surface_zeta_dma == 0);
        CHECK(rsx_process_method(&state, 0x01B8, 0x00000000u) == 0);
        CHECK(state.surface_dma_seen == 0x0Au && state.surface_color_dma[3] == 0);
        CHECK(rsx_process_method(&state, 0x0194, 0xDEADBEEFu) == 0);
        CHECK(state.surface_dma_seen == 0x0Bu && state.surface_color_dma[0] == 0xDEADBEEFu);
        CHECK(rsx_process_method(&state, 0x0198, 0xFEED0000u) == 0);
        CHECK(state.surface_dma_seen == 0x1Bu && state.surface_zeta_dma == 0xFEED0000u);
        CHECK(rsx_process_method(&state, 0x01B4, 0x12345678u) == 0);
        CHECK(state.surface_dma_seen == 0x1Fu);
        CHECK(state.surface_color_dma[0] == 0xDEADBEEFu);
        CHECK(state.surface_color_dma[1] == 0xFEED0001u);
        CHECK(state.surface_color_dma[2] == 0x12345678u);
        CHECK(state.surface_color_dma[3] == 0x00000000u);
        CHECK(state.surface_zeta_dma == 0xFEED0000u);
    } else if (!strcmp(argv[1], "unknown")) {
        CHECK(rsx_process_method(&state, 0x0194, 0) == 0);
        CHECK(state.surface_dma_seen == 1u && state.surface_color_dma[0] == 0);
        CHECK(rsx_process_method(&state, 0x0194, 0xA5F01234u) == 0);
        CHECK(state.surface_dma_seen == 1u && state.surface_color_dma[0] == 0xA5F01234u);
        CHECK(rsx_process_method(&state, 0x0194, 0xFEED0001u) == 0);
        CHECK(state.surface_dma_seen == 1u && state.surface_color_dma[0] == 0xFEED0001u);
    } else if (!strcmp(argv[1], "reset")) {
        CHECK(rsx_process_method(&state, 0x0194, 0x11111111u) == 0);
        CHECK(rsx_process_method(&state, 0x018C, 0x22222222u) == 0);
        CHECK(rsx_process_method(&state, 0x01B4, 0x33333333u) == 0);
        CHECK(rsx_process_method(&state, 0x01B8, 0x44444444u) == 0);
        CHECK(rsx_process_method(&state, 0x0198, 0x55555555u) == 0);
        CHECK(state.surface_dma_seen == 0x1Fu);
        rsx_state_init(&state);
        CHECK(state.surface_dma_seen == 0);
        CHECK(state.surface_color_dma[0] == 0 && state.surface_color_dma[1] == 0);
        CHECK(state.surface_color_dma[2] == 0 && state.surface_color_dma[3] == 0);
        CHECK(state.surface_zeta_dma == 0);
        CHECK(state.color_mask == 0x01010101u);
    } else if (!strcmp(argv[1], "preserve")) {
        rsx_state expected;
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_FORMAT, 0x148) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_COLOR_AOFFSET, 0xAB8000) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_COLOR_BOFFSET, 0xE3C000) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_PITCH_A, 5120) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_PITCH_B, 5376) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_COLOR_TARGET, 0x13) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_CLIP_HORIZONTAL, (1280u << 16) | 13u) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SURFACE_CLIP_VERTICAL, (720u << 16) | 7u) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SCISSOR_HORIZONTAL, (640u << 16) | 19u) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_SCISSOR_VERTICAL, (360u << 16) | 11u) == 0);
        CHECK(rsx_process_method(&state, NV4097_SET_COLOR_MASK, 0x00010101u) == 0);
        state.surface_dirty = 0;
        memcpy(&expected, &state, sizeof(expected));
        expected.surface_color_dma[0] = 0xFEED0000u;
        expected.surface_color_dma[1] = 0xFEED0001u;
        expected.surface_color_dma[2] = 0xABCDEF01u;
        expected.surface_color_dma[3] = 0xABCDEF02u;
        expected.surface_zeta_dma = 0xABCDEF03u;
        expected.surface_dma_seen = 0x1Fu;
        CHECK(rsx_process_method(&state, 0x0194, 0xFEED0000u) == 0);
        CHECK(rsx_process_method(&state, 0x018C, 0xFEED0001u) == 0);
        CHECK(rsx_process_method(&state, 0x01B4, 0xABCDEF01u) == 0);
        CHECK(rsx_process_method(&state, 0x01B8, 0xABCDEF02u) == 0);
        CHECK(rsx_process_method(&state, 0x0198, 0xABCDEF03u) == 0);
        CHECK(memcmp(&state, &expected, sizeof(state)) == 0);
    } else {
        u32 method = (u32)strtoul(argv[1], NULL, 16);
        int result = rsx_process_method(&state, method, 0xFEED0001u);
        if (result != 0) {
            printf("FAIL surface DMA method 0x%04X returned %d\n", method, result);
            return 1;
        }
    }
    puts("PASS surface DMA dispatch");
    return 0;
}
'''


class SurfaceDmaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-surface-dma-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        cls.compilation = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"), "/I" + str(SDK / "include"),
            str(probe), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], capture_output=True, text=True)

    def run_probe(self, mode):
        self.assertEqual(self.compilation.returncode, 0,
                         self.compilation.stdout + self.compilation.stderr)
        env = os.environ.copy()
        for key in ("RSX_TRACE", "SURFDBG"):
            env.pop(key, None)
        result = subprocess.run([str(self.executable), mode], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dma_a_method_is_recognized(self):
        self.run_probe("0194")

    def test_dma_b_method_is_recognized(self):
        self.run_probe("018C")

    def test_dma_c_method_is_recognized(self):
        self.run_probe("01B4")

    def test_dma_d_method_is_recognized(self):
        self.run_probe("01B8")

    def test_dma_zeta_method_is_recognized(self):
        self.run_probe("0198")

    def test_nonsequential_methods_keep_each_mrt_and_zeta_identity(self):
        self.run_probe("mapping")

    def test_unknown_raw_handles_are_not_normalized_to_local(self):
        self.run_probe("unknown")

    def test_reset_forgets_seen_bits_and_all_raw_handles(self):
        self.run_probe("reset")

    def test_dma_writes_preserve_other_state_and_dirty_bits(self):
        self.run_probe("preserve")


if __name__ == "__main__":
    unittest.main()
