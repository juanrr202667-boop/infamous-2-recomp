"""Run the production surface snapshot helper on CPU, without GPU or game data.

The native probe includes the SDK header directly and uses the real rsx_state.
Literal expectations catch mixed MRT fields, live-state aliases and stale data.
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
#include "rsx_d3d12_surface_metadata.h"
#include <stdio.h>
#include <string.h>

#define CHECK(condition) do { \
    if (!(condition)) { printf("FAIL line %d: %s\n", __LINE__, #condition); return 1; } \
} while (0)

static void seed(rsx_state* state) {
    memset(state, 0, sizeof(*state));
    state->surface_color_offset[0] = 0xAB8000;
    state->surface_color_offset[1] = 0xE3C000;
    state->surface_color_offset[2] = 0x3B0000;
    state->surface_color_offset[3] = 0xD06000;
    state->surface_color_pitch[0] = 5120;
    state->surface_color_pitch[1] = 5376;
    state->surface_color_pitch[2] = 5632;
    state->surface_color_pitch[3] = 5888;
    state->surface_color_dma[0] = 0xFEED0000u;
    state->surface_color_dma[1] = 0xFEED0001u;
    state->surface_color_dma[2] = 0xFEED0002u;
    state->surface_color_dma[3] = 0xFEED0003u;
    state->surface_zeta_dma = 0xDEADBEEFu;
    state->surface_dma_seen = 0x1Fu;
    state->surface_clip_x = 13;
    state->surface_clip_y = 7;
    state->surface_clip_w = 1280;
    state->surface_clip_h = 720;
    state->scissor_x = 19;
    state->scissor_y = 11;
    state->scissor_w = 640;
    state->scissor_h = 360;
    state->color_target = 0x1F;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    rsx_state state;
    rsx_d3d12_surface_metadata snapshot;
    seed(&state);
    memset(&snapshot, 0xA5, sizeof(snapshot));

    if (!strcmp(argv[1], "mrt")) {
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 0.0f, 0);
        CHECK(snapshot.valid == 1);
        CHECK(snapshot.color_offset[0] == 0xAB8000);
        CHECK(snapshot.color_offset[1] == 0xE3C000);
        CHECK(snapshot.color_offset[2] == 0x3B0000);
        CHECK(snapshot.color_offset[3] == 0xD06000);
        CHECK(snapshot.color_pitch[0] == 5120);
        CHECK(snapshot.color_pitch[1] == 5376);
        CHECK(snapshot.color_pitch[2] == 5632);
        CHECK(snapshot.color_pitch[3] == 5888);
    } else if (!strcmp(argv[1], "regions")) {
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 0.0f, 0);
        CHECK(snapshot.clip_x == 13 && snapshot.clip_y == 7);
        CHECK(snapshot.clip_w == 1280 && snapshot.clip_h == 720);
        CHECK(snapshot.scissor_x == 19 && snapshot.scissor_y == 11);
        CHECK(snapshot.scissor_w == 640 && snapshot.scissor_h == 360);
        CHECK(snapshot.color_target == 0x1F);
        state.color_target = 2;
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 0.0f, 0);
        CHECK(snapshot.color_target == 2);
        CHECK(snapshot.color_pitch[0] == 5120 && snapshot.color_pitch[1] == 5376);
    } else if (!strcmp(argv[1], "clear")) {
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0x80000091u, 0.375f, 0xA5);
        CHECK(snapshot.valid == 1);
        CHECK(snapshot.clear_flags == 0x80000091u);
        CHECK(snapshot.clear_depth == 0.375f);
        CHECK(snapshot.clear_stencil == 0xA5u);
    } else if (!strcmp(argv[1], "mutate")) {
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0x10u, 0.625f, 0x5A);
        memset(&state, 0x5A, sizeof(state));
        CHECK(snapshot.valid == 1);
        CHECK(snapshot.color_offset[0] == 0xAB8000 && snapshot.color_offset[3] == 0xD06000);
        CHECK(snapshot.color_pitch[1] == 5376 && snapshot.color_pitch[2] == 5632);
        CHECK(snapshot.clip_x == 13 && snapshot.clip_y == 7);
        CHECK(snapshot.clip_w == 1280 && snapshot.clip_h == 720);
        CHECK(snapshot.scissor_x == 19 && snapshot.scissor_y == 11);
        CHECK(snapshot.scissor_w == 640 && snapshot.scissor_h == 360);
        CHECK(snapshot.color_target == 0x1F);
        CHECK(snapshot.clear_flags == 0x10u && snapshot.clear_depth == 0.625f);
        CHECK(snapshot.clear_stencil == 0x5Au);
    } else if (!strcmp(argv[1], "missing")) {
        rsx_d3d12_surface_metadata expected;
        memset(&expected, 0, sizeof(expected));
        expected.clear_flags = 0x12u;
        expected.clear_depth = 0.75f;
        expected.clear_stencil = 0xC3u;
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0xF0u, 0.125f, 0xFF);
        rsx_d3d12_snapshot_surface(&snapshot, NULL, 0x12u, 0.75f, 0xC3);
        CHECK(snapshot.valid == 0);
        CHECK(memcmp(&snapshot, &expected, sizeof(snapshot)) == 0);
    } else if (!strcmp(argv[1], "zero-state")) {
        rsx_d3d12_surface_metadata expected;
        memset(&expected, 0, sizeof(expected));
        expected.valid = 1;
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0xF0u, 0.5f, 0xFF);
        memset(&state, 0, sizeof(state));
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 0.0f, 0);
        CHECK(memcmp(&snapshot, &expected, sizeof(snapshot)) == 0);
    } else if (!strcmp(argv[1], "dma-mrt")) {
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 1.0f, 0);
        CHECK(snapshot.valid == 1);
        CHECK(snapshot.color_dma[0] == 0xFEED0000u);
        CHECK(snapshot.color_dma[1] == 0xFEED0001u);
        CHECK(snapshot.color_dma[2] == 0xFEED0002u);
        CHECK(snapshot.color_dma[3] == 0xFEED0003u);
        CHECK(snapshot.zeta_dma == 0xDEADBEEFu);
        CHECK(snapshot.dma_seen == 0x1Fu);
    } else if (!strcmp(argv[1], "dma-mask")) {
        for (u32 mask = 0; mask < 32; ++mask) {
            state.surface_dma_seen = mask;
            rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 1.0f, 0);
            CHECK(snapshot.valid == 1);
            CHECK(snapshot.dma_seen == mask);
            CHECK(snapshot.color_dma[0] == 0xFEED0000u);
            CHECK(snapshot.color_dma[1] == 0xFEED0001u);
            CHECK(snapshot.color_dma[2] == 0xFEED0002u);
            CHECK(snapshot.color_dma[3] == 0xFEED0003u);
            CHECK(snapshot.zeta_dma == 0xDEADBEEFu);
        }
    } else if (!strcmp(argv[1], "dma-raw")) {
        state.surface_color_dma[0] = 0;
        state.surface_color_dma[1] = 1;
        state.surface_color_dma[2] = 0xFFFFFFFFu;
        state.surface_color_dma[3] = 0x12345678u;
        state.surface_zeta_dma = 0x87654321u;
        state.surface_dma_seen = 0x80000012u;
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 1.0f, 0);
        CHECK(snapshot.valid == 1);
        CHECK(snapshot.color_dma[0] == 0);
        CHECK(snapshot.color_dma[1] == 1);
        CHECK(snapshot.color_dma[2] == 0xFFFFFFFFu);
        CHECK(snapshot.color_dma[3] == 0x12345678u);
        CHECK(snapshot.zeta_dma == 0x87654321u);
        CHECK(snapshot.dma_seen == 0x80000012u);
    } else if (!strcmp(argv[1], "dma-mutate")) {
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0, 1.0f, 0);
        memset(&state, 0, sizeof(state));
        CHECK(snapshot.valid == 1);
        CHECK(snapshot.color_dma[0] == 0xFEED0000u);
        CHECK(snapshot.color_dma[1] == 0xFEED0001u);
        CHECK(snapshot.color_dma[2] == 0xFEED0002u);
        CHECK(snapshot.color_dma[3] == 0xFEED0003u);
        CHECK(snapshot.zeta_dma == 0xDEADBEEFu);
        CHECK(snapshot.dma_seen == 0x1Fu);
    } else if (!strcmp(argv[1], "dma-null-reset")) {
        rsx_d3d12_surface_metadata expected;
        memset(&expected, 0, sizeof(expected));
        expected.clear_flags = 0xA1u;
        expected.clear_depth = 0.625f;
        expected.clear_stencil = 0xB7u;
        rsx_d3d12_snapshot_surface(&snapshot, &state, 0xF0u, 0.5f, 0xFF);
        CHECK(snapshot.dma_seen == 0x1Fu);
        rsx_d3d12_snapshot_surface(&snapshot, NULL, 0xA1u, 0.625f, 0xB7);
        CHECK(memcmp(&snapshot, &expected, sizeof(snapshot)) == 0);
    } else {
        return 2;
    }
    puts("PASS surface metadata snapshot");
    return 0;
}
'''


class SurfaceMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-surface-metadata-")
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
        self.assertEqual(
            self.compilation.returncode, 0,
            "Surface snapshot API must compile:\n" + self.compilation.stdout + self.compilation.stderr,
        )
        result = subprocess.run([str(self.executable), mode], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_mrt_offsets_keep_their_own_pitches(self):
        self.run_probe("mrt")

    def test_clip_scissor_and_target_are_not_confused(self):
        self.run_probe("regions")

    def test_clear_arguments_are_preserved_without_masking(self):
        self.run_probe("clear")

    def test_mutating_state_cannot_change_an_earlier_snapshot(self):
        self.run_probe("mutate")

    def test_missing_state_clears_old_fields_but_keeps_clear_arguments(self):
        self.run_probe("missing")

    def test_zero_state_is_valid_and_clears_stale_metadata(self):
        self.run_probe("zero-state")

    def test_dma_mrt_and_zeta_keep_their_own_raw_handles(self):
        self.run_probe("dma-mrt")

    def test_dma_seen_mask_does_not_filter_unseen_raw_values(self):
        self.run_probe("dma-mask")

    def test_dma_unknown_handles_and_mask_bits_are_not_interpreted(self):
        self.run_probe("dma-raw")

    def test_dma_snapshot_survives_later_state_mutation(self):
        self.run_probe("dma-mutate")

    def test_dma_missing_state_resets_snapshot_and_keeps_clear_arguments(self):
        self.run_probe("dma-null-reset")


if __name__ == "__main__":
    unittest.main()
