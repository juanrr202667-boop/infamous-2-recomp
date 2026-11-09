"""Run the production linear-view catalog on CPU without GPU or game assets."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

DRIVER = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#if __has_include("rsx_linear_catalog.h")
#include "rsx_linear_catalog.h"
#define CHECK(condition) do { \
    if (!(condition)) { printf("FAIL line %d: %s\n", __LINE__, #condition); return 1; } \
} while (0)

static rsx_linear_version views[16];
static rsx_linear_version before[16];
static const rsx_linear_view ab720 = {0xAB8000, 1280, 720, 5120, 4, 8, 1};
static uint32_t count, slot;
static unsigned spans, pixels;
static int bad_span;

static void reset(void) {
    memset(views, 0xA5, sizeof(views));
    count = 0; slot = 0xC0DEu;
}

static int emit(void* unused, uint32_t src, uint32_t dst, const rsx_linear_copy_span* span) {
    (void)unused;
    if (src != 0 || dst != 1 || span->src_x || span->dst_x ||
            span->src_y != spans || span->dst_y != spans || span->width != 1280)
        bad_span = 1;
    ++spans; pixels += span->width;
    return 1;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    reset();
    if (!strcmp(argv[1], "temporal")) {
        rsx_linear_view ab180 = ab720;
        ab180.height = 180;
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_OK);
        CHECK(slot == 0 && count == 1 && views[0].complete_at == 0);
        views[0].complete_at = 17;
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab180, &slot) == RSX_LINEAR_OK);
        uint32_t t0 = slot;
        CHECK(t0 == 1 && count == 2 && views[1].complete_at == 0);
        CHECK(views[0].view.height == 720 && views[0].view.pitch == 5120);
        CHECK(views[0].complete_at == 17 && views[1].view.height == 180);
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_OK);
        uint32_t t5 = slot;
        CHECK(t5 == 0 && t0 != t5 && count == 2 && views[t5].complete_at == 17);
    } else if (!strcmp(argv[1], "keys")) {
        const rsx_linear_view base = {0x1000, 4, 2, 32, 4, 8, 1};
        CHECK(rsx_linear_reserve_view(views, 16, &count, &base, &slot) == RSX_LINEAR_OK);
        rsx_linear_view changed[7];
        for (unsigned i = 0; i < 7; ++i) changed[i] = base;
        changed[0].offset = 0x100001000ull;
        changed[1].width = 3;
        changed[2].height = 1;
        changed[3].pitch = 64;
        changed[4].bytes_per_pixel = 8;
        changed[5].format_id = 9;
        changed[6].location = 2;
        for (unsigned i = 0; i < 7; ++i) {
            CHECK(rsx_linear_reserve_view(views, 16, &count, &changed[i], &slot) == RSX_LINEAR_OK);
            CHECK(slot == i + 1 && count == i + 2 && views[slot].complete_at == 0);
            CHECK(rsx_linear_reserve_view(views, 16, &count, &changed[i], &slot) == RSX_LINEAR_OK);
            CHECK(slot == i + 1 && count == i + 2);
        }
        CHECK(views[0].view.offset == 0x1000 && views[1].view.offset == 0x100001000ull);
        CHECK(views[5].view.bytes_per_pixel == 8 && views[6].view.format_id == 9);
        CHECK(views[7].view.location == 2);
    } else if (!strcmp(argv[1], "persistent-hit")) {
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_OK);
        views[0].complete_at = 0x123456789ABCDEF0ull;
        memcpy(before, views, sizeof(views));
        for (unsigned batch = 0; batch < 4; ++batch) {
            slot = 0xC0DEu;
            CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_OK);
            CHECK(slot == 0 && count == 1);
            CHECK(memcmp(before, views, sizeof(views)) == 0);
        }
    } else if (!strcmp(argv[1], "capacity")) {
        rsx_linear_view small = ab720;
        small.height = 180;
        CHECK(rsx_linear_reserve_view(views, 1, &count, &ab720, &slot) == RSX_LINEAR_OK);
        views[0].complete_at = 57;
        memcpy(before, views, sizeof(views));
        slot = 0xC0DEu;
        CHECK(rsx_linear_reserve_view(views, 1, &count, &small, &slot) == -4);
        CHECK(count == 1 && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
        CHECK(rsx_linear_reserve_view(views, 1, &count, &ab720, &slot) == RSX_LINEAR_OK);
        CHECK(slot == 0 && count == 1 && memcmp(before, views, sizeof(views)) == 0);
    } else if (!strcmp(argv[1], "invalid-views")) {
        const rsx_linear_view base = {0x1000, 4, 2, 32, 4, 8, 1};
        CHECK(rsx_linear_reserve_view(views, 16, &count, &base, &slot) == RSX_LINEAR_OK);
        rsx_linear_view invalid[11];
        for (unsigned i = 0; i < 11; ++i) invalid[i] = base;
        invalid[0].width = 0; invalid[1].height = 0; invalid[2].pitch = 0;
        invalid[3].bytes_per_pixel = 0; invalid[4].format_id = 0;
        invalid[5].location = 0; invalid[6].location = 3;
        invalid[7].pitch = 12; invalid[8].pitch = 17; invalid[9].offset = 0x1001;
        invalid[10].offset = UINT64_MAX - 3u;
        memcpy(before, views, sizeof(views));
        for (unsigned i = 0; i < 11; ++i) {
            slot = 0xC0DEu;
            CHECK(rsx_linear_reserve_view(views, 16, &count, &invalid[i], &slot) == RSX_LINEAR_BAD_VIEW);
            CHECK(count == 1 && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
        }
    } else if (!strcmp(argv[1], "arguments")) {
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_OK);
        memcpy(before, views, sizeof(views)); slot = 0xC0DEu;
        CHECK(rsx_linear_reserve_view(NULL, 16, &count, &ab720, &slot) == RSX_LINEAR_BAD_VIEW);
        CHECK(rsx_linear_reserve_view(views, 16, NULL, &ab720, &slot) == RSX_LINEAR_BAD_VIEW);
        CHECK(rsx_linear_reserve_view(views, 16, &count, NULL, &slot) == RSX_LINEAR_BAD_VIEW);
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, NULL) == RSX_LINEAR_BAD_VIEW);
        CHECK(count == 1 && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &count) == RSX_LINEAR_BAD_VIEW);
        CHECK(count == 1 && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
        count = 17;
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_BAD_VIEW);
        CHECK(count == 17 && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
    } else if (!strcmp(argv[1], "zero-capacity")) {
        memcpy(before, views, sizeof(views));
        CHECK(rsx_linear_reserve_view(views, 0, &count, &ab720, &slot) == -4);
        CHECK(count == 0 && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
    } else if (!strcmp(argv[1], "overflow")) {
        memcpy(before, views, sizeof(views));
        count = UINT32_MAX;
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_BAD_VIEW);
        CHECK(count == UINT32_MAX && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
        count = 0;
        rsx_linear_view huge = {UINT64_MAX - 3u, 1, 1, 4, 4, 8, 1};
        CHECK(rsx_linear_reserve_view(views, 16, &count, &huge, &slot) == RSX_LINEAR_BAD_VIEW);
        huge.offset = 0; huge.width = UINT32_MAX; huge.bytes_per_pixel = UINT32_MAX;
        CHECK(rsx_linear_reserve_view(views, 16, &count, &huge, &slot) == RSX_LINEAR_BAD_VIEW);
        CHECK(count == 0 && slot == 0xC0DEu && memcmp(before, views, sizeof(views)) == 0);
    } else if (!strcmp(argv[1], "coherence")) {
        rsx_linear_view small = ab720;
        small.height = 180;
        CHECK(rsx_linear_reserve_view(views, 16, &count, &ab720, &slot) == RSX_LINEAR_OK);
        views[0].complete_at = 1;
        CHECK(rsx_linear_reserve_view(views, 16, &count, &small, &slot) == RSX_LINEAR_OK);
        CHECK(rsx_linear_plan_materialize(views, count, slot, 1, emit, NULL) == RSX_LINEAR_OK);
        CHECK(spans == 180 && pixels == 230400 && !bad_span);
        CHECK(views[0].complete_at == 1 && views[1].complete_at == 0);
    } else return 2;
    puts("PASS stable linear view reservation");
    return 0;
}
#else
int main(void) { puts("FAIL linear view catalog API not implemented"); return 1; }
#endif
'''


class LinearCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-linear-catalog-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        cls.compilation = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"), str(probe),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], capture_output=True, text=True)

    def run_probe(self, mode):
        self.assertEqual(self.compilation.returncode, 0,
                         self.compilation.stdout + self.compilation.stderr)
        result = subprocess.run([str(self.executable), mode], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_late_small_view_keeps_earlier_target_and_distinct_sampler_slots(self):
        self.run_probe("temporal")

    def test_every_view_field_contributes_to_identity(self):
        self.run_probe("keys")

    def test_hits_across_batches_preserve_complete_at_and_catalog(self):
        self.run_probe("persistent-hit")

    def test_full_catalog_rejects_new_view_without_mutation_but_allows_hits(self):
        self.run_probe("capacity")

    def test_invalid_geometry_does_not_change_catalog_count_or_slot(self):
        self.run_probe("invalid-views")

    def test_invalid_arguments_are_rejected_before_mutation(self):
        self.run_probe("arguments")

    def test_zero_capacity_is_distinct_from_invalid_geometry(self):
        self.run_probe("zero-capacity")

    def test_count_and_address_overflow_fail_without_mutation(self):
        self.run_probe("overflow")

    def test_reserved_slots_work_with_real_coherence_planner(self):
        self.run_probe("coherence")


if __name__ == "__main__":
    unittest.main()
