"""CPU coherence plans compared with a canonical guest-address store."""
from pathlib import Path
import importlib.util
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("linear_geometry_fixture", ROOT / "tests/test_rsx_linear_view.py")
geometry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geometry)

DRIVER = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#if __has_include("rsx_linear_coherence.h")
#include "rsx_linear_coherence.h"
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
static rsx_linear_version views[4];
static uint32_t pixels[4][16], guest[64];
static unsigned seen, writes, fail;
static uint64_t last_source;
static int emit(void* unused, uint32_t src, uint32_t dst, const rsx_linear_copy_span* span) {
    if (views[src].complete_at < last_source) return 0;
    if (fail && writes == fail - 1) return 0;
    last_source = views[src].complete_at; seen |= 1u << src; ++writes;
    for (unsigned x = 0; x < span->width; ++x)
        pixels[dst][span->dst_y * views[dst].view.width + span->dst_x + x] =
            pixels[src][span->src_y * views[src].view.width + span->src_x + x];
    return 1;
}
static void reset(void) {
    memset(views, 0, sizeof(views));
    for (unsigned i = 0; i < 64; ++i) guest[i] = 0xABC00000u + i;
    for (unsigned i = 0; i < 4; ++i) {
        rsx_linear_view v = {0x1000, 4, 4, 16, 4, 1, 1};
        if (i == 1) v.width = v.height = 2;
        if (i == 2) { v.offset += 4; v.width = 1; v.height = 2; }
        views[i].view = v;
        for (unsigned y = 0; y < v.height; ++y) for (unsigned x = 0; x < v.width; ++x)
            pixels[i][y * v.width + x] = guest[(v.offset - 0x1000 + y * v.pitch) / 4 + x];
    }
    seen = writes = fail = 0; last_source = 0;
}
static int coherent(unsigned slot) {
    rsx_linear_view v = views[slot].view;
    for (unsigned y = 0; y < v.height; ++y) for (unsigned x = 0; x < v.width; ++x)
        if (pixels[slot][y * v.width + x] != guest[(v.offset - 0x1000 + y * v.pitch) / 4 + x]) return 0;
    return 1;
}
static int materialize(unsigned slot, uint64_t epoch) {
    seen = writes = 0; last_source = 0;
    int result = rsx_linear_plan_materialize(views, 4, slot, epoch, emit, NULL);
    if (result == RSX_LINEAR_OK) views[slot].complete_at = epoch;
    return result;
}
static void masked_write(unsigned slot, uint64_t epoch, uint32_t mask, uint32_t value) {
    rsx_linear_view v = views[slot].view;
    for (unsigned y = 0; y < v.height; ++y) for (unsigned x = 0; x < v.width; ++x) {
        unsigned p = y * v.width + x;
        pixels[slot][p] = (pixels[slot][p] & ~mask) | (value & mask);
        unsigned address = (unsigned)((v.offset - 0x1000 + y * v.pitch) / 4 + x);
        guest[address] = (guest[address] & ~mask) | (value & mask);
    }
    views[slot].complete_at = epoch;
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    reset();
    if (!strcmp(argv[1], "temporal")) {
        masked_write(0, 1, 0xFFFFFFFF, 0xAABBCCDD);
        CHECK(materialize(1, 1) == RSX_LINEAR_OK && coherent(1));
        CHECK(views[0].view.height == 4 && views[1].view.height == 2);
        masked_write(1, 2, 0x000000FF, 0x00000011);
        CHECK(materialize(2, 2) == RSX_LINEAR_OK && coherent(2));
        masked_write(2, 3, 0xFF000000, 0xEE000000);
        CHECK(materialize(3, 3) == RSX_LINEAR_OK && coherent(3));
        CHECK((seen & 7) == 7); /* Latest small view alone is insufficient. */
        CHECK(pixels[3][0] == 0xAABBCC11 && pixels[3][1] == 0xEEBBCC11);
        CHECK(pixels[3][15] == 0xAABBCCDD); /* Outside both small views. */
        CHECK(materialize(1, 3) == RSX_LINEAR_OK && coherent(1));
        CHECK(materialize(0, 3) == RSX_LINEAR_OK && coherent(0));
        CHECK(materialize(0, 3) == RSX_LINEAR_OK && writes == 0);
    } else if (!strcmp(argv[1], "old-source")) {
        masked_write(1, 2, 0xFFFFFFFF, 0x11111111);
        masked_write(3, 5, 0xFFFFFFFF, 0x55555555);
        CHECK(materialize(2, 5) == RSX_LINEAR_OK);
        masked_write(2, 6, 0xFFFFFFFF, 0x66666666);
        CHECK(materialize(3, 6) == RSX_LINEAR_OK && coherent(3));
        CHECK(seen == (1u << 2)); /* Older overlapping source must not restore stale pixels. */
        CHECK(pixels[3][0] == 0x55555555 && pixels[3][1] == 0x66666666);
    } else if (!strcmp(argv[1], "different-pitch")) {
        views[1].view.width = 2; views[1].view.height = 4; views[1].view.pitch = 8;
        masked_write(0, 1, 0xFFFFFFFF, 0xAABBCCDD);
        CHECK(materialize(1, 1) == RSX_LINEAR_OK && coherent(1));
        masked_write(1, 2, 0x0000FF00, 0x00002200);
        CHECK(materialize(0, 2) == RSX_LINEAR_OK && coherent(0));
        CHECK(pixels[0][7] == 0xAABB22DD && pixels[0][8] == 0xAABBCCDD);
    } else if (!strcmp(argv[1], "partial-failure")) {
        masked_write(0, 1, 0xFFFFFFFF, 0x12345678);
        rsx_linear_version before[4]; memcpy(before, views, sizeof(views));
        fail = 3;
        CHECK(materialize(1, 1) == RSX_LINEAR_OK); /* Two spans fit, no later failure. */
        views[3].complete_at = 0;
        memcpy(before, views, sizeof(views));
        CHECK(materialize(3, 1) == RSX_LINEAR_EMIT_FAILED && writes == 2);
        CHECK(!memcmp(before, views, sizeof(views)));
    } else if (!strcmp(argv[1], "failure")) {
        masked_write(0, 1, 0xFFFFFFFF, 0x12345678);
        fail = 1;
        CHECK(materialize(1, 1) == RSX_LINEAR_EMIT_FAILED);
        CHECK(views[1].complete_at == 0);
        fail = 0; views[0].complete_at = 2;
        CHECK(materialize(1, 1) == RSX_LINEAR_BAD_VIEW);
        CHECK(views[1].complete_at == 0);
    } else return 2;
    puts("PASS linear coherence plan"); return 0;
}
#else
int main(void) { puts("FAIL linear coherence planner not implemented"); return 1; }
#endif
'''


class LinearCoherenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse only the compiler setup, never a copy of the implementation.
        fixture = type("CoherenceCompiler", (geometry.LinearViewTests,), {})
        original = geometry.DRIVER
        geometry.DRIVER = DRIVER
        try:
            fixture.setUpClass()
        finally:
            geometry.DRIVER = original
        cls.addClassCleanup(fixture.doClassCleanups)
        cls.executable = fixture.executable

    def test_temporal_materialization(self):
        for case in ("temporal", "old-source", "different-pitch", "failure", "partial-failure"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
