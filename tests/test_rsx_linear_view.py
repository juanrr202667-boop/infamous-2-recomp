"""Check address-preserving copies between validated linear guest views.

This CPU fixture tests geometry only. It does not establish RSX layout,
ownership, GPU resource state or the correctness of a native frame.
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
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#if __has_include("rsx_linear_view.h")
#include "rsx_linear_view.h"
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
typedef struct {
    const rsx_linear_view* src;
    const rsx_linear_view* dst;
    unsigned values[256], output[256];
    unsigned calls, fail_after;
    int bad;
} fixture;
static int copy_span(void* user, const rsx_linear_copy_span* s) {
    fixture* f = (fixture*)user;
    if (f->fail_after && f->calls == f->fail_after) return 0;
    ++f->calls;
    if (!s->width || s->src_y >= f->src->height || s->dst_y >= f->dst->height ||
        s->src_x + s->width > f->src->width || s->dst_x + s->width > f->dst->width) {
        f->bad = 1; return 0;
    }
    if (f->src->offset + (uint64_t)s->src_y * f->src->pitch + (uint64_t)s->src_x * f->src->bytes_per_pixel !=
        f->dst->offset + (uint64_t)s->dst_y * f->dst->pitch + (uint64_t)s->dst_x * f->dst->bytes_per_pixel) {
        f->bad = 1; return 0;
    }
    for (unsigned x = 0; x < s->width; ++x)
        f->output[s->dst_y * f->dst->width + s->dst_x + x] =
            f->values[s->src_y * f->src->width + s->src_x + x];
    return 1;
}
static int check_mapping(rsx_linear_view src, rsx_linear_view dst, unsigned* calls) {
    fixture f = {0}; f.src = &src; f.dst = &dst;
    for (unsigned i = 0; i < 256; ++i) { f.values[i] = 1000 + i; f.output[i] = 999; }
    CHECK(rsx_linear_foreach_overlap(&src, &dst, copy_span, &f) == RSX_LINEAR_OK);
    CHECK(!f.bad);
    for (unsigned dy = 0; dy < dst.height; ++dy) for (unsigned dx = 0; dx < dst.width; ++dx) {
        uint64_t address = dst.offset + (uint64_t)dy * dst.pitch + dx * dst.bytes_per_pixel;
        unsigned expected = 999;
        if (src.location == dst.location)
            for (unsigned sy = 0; sy < src.height; ++sy) for (unsigned sx = 0; sx < src.width; ++sx)
                if (src.offset + (uint64_t)sy * src.pitch + sx * src.bytes_per_pixel == address)
                    expected = f.values[sy * src.width + sx];
        CHECK(f.output[dy * dst.width + dx] == expected);
    }
    if (calls) *calls = f.calls;
    return 0;
}
static unsigned random_value(unsigned* seed) {
    unsigned x = *seed; x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    return *seed = x;
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    rsx_linear_view a = {0xAB8000, 4, 4, 16, 4, 1, 1};
    rsx_linear_view b = a;
    unsigned calls = 0;
    if (!strcmp(argv[1], "same-pitch")) {
        b.width = b.height = 2;
        CHECK(!check_mapping(a, b, &calls) && calls == 2);
        CHECK(!check_mapping(b, a, &calls) && calls == 2);
    } else if (!strcmp(argv[1], "different-pitch")) {
        a.height = 2; b.width = 2; b.pitch = 8;
        CHECK(!check_mapping(a, b, &calls) && calls == 4);
        CHECK(!check_mapping(b, a, &calls) && calls == 4);
    } else if (!strcmp(argv[1], "interior-offset")) {
        b.offset += 24; b.width = 2; b.height = 3; b.pitch = 8;
        CHECK(!check_mapping(a, b, &calls) && calls == 3);
        CHECK(!check_mapping(b, a, NULL));
    } else if (!strcmp(argv[1], "padding")) {
        a.pitch = b.pitch = 32; b.width = 8;
        CHECK(!check_mapping(a, b, &calls) && calls == 4);
        b.offset += 16; b.width = 4;
        CHECK(!check_mapping(a, b, &calls) && !calls);
    } else if (!strcmp(argv[1], "disjoint")) {
        b.offset += 1024;
        CHECK(!check_mapping(a, b, &calls) && !calls);
        b.format_id = 2;
        CHECK(!check_mapping(a, b, &calls) && !calls);
        b = a; b.location = 2;
        CHECK(!check_mapping(a, b, &calls) && !calls);
    } else if (!strcmp(argv[1], "invalid")) {
        fixture f = {0}; f.src = &a; f.dst = &b;
        CHECK(rsx_linear_foreach_overlap(NULL, &b, copy_span, &f) == RSX_LINEAR_BAD_VIEW);
        CHECK(rsx_linear_foreach_overlap(&a, &b, NULL, &f) == RSX_LINEAR_BAD_VIEW);
        b.width = 0;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_BAD_VIEW);
        b = a; b.pitch = 12;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_BAD_VIEW);
        b = a; b.pitch = 17;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_BAD_VIEW);
        b = a; b.offset += 1;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_BAD_VIEW);
        b = a; b.location = 0;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_BAD_VIEW);
        b = a; b.format_id = 2;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_INCOMPATIBLE);
        b = a; b.bytes_per_pixel = 8; b.pitch = 32;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_INCOMPATIBLE);
        b = a; b.offset = UINT64_MAX - 15;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_BAD_VIEW);
        CHECK(!f.calls);
    } else if (!strcmp(argv[1], "callback-failure")) {
        fixture f = {0}; f.src = &a; f.dst = &b; f.fail_after = 2;
        CHECK(rsx_linear_foreach_overlap(&a, &b, copy_span, &f) == RSX_LINEAR_EMIT_FAILED);
        CHECK(f.calls == 2);
    } else if (!strcmp(argv[1], "oracle")) {
        unsigned seed = 0xC012AB;
        for (unsigned i = 0; i < 2000; ++i) {
            a.bytes_per_pixel = b.bytes_per_pixel = 1u << (2 + random_value(&seed) % 3);
            a.offset = 0x100000000ull + (random_value(&seed) % 64) * a.bytes_per_pixel;
            b.offset = 0x100000000ull + (random_value(&seed) % 64) * b.bytes_per_pixel;
            a.width = 1 + random_value(&seed) % 8; a.height = 1 + random_value(&seed) % 8;
            b.width = 1 + random_value(&seed) % 8; b.height = 1 + random_value(&seed) % 8;
            a.pitch = (a.width + random_value(&seed) % 8) * a.bytes_per_pixel;
            b.pitch = (b.width + random_value(&seed) % 8) * b.bytes_per_pixel;
            CHECK(!check_mapping(a, b, NULL));
        }
    } else return 2;
    puts("PASS linear view geometry"); return 0;
}
#else
int main(void) { puts("FAIL linear view overlap API not implemented"); return 1; }
#endif
'''


class LinearViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-linear-view-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "probe.c"
        source.write_text(DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"), str(source),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_copy_geometry(self):
        for case in ("same-pitch", "different-pitch", "interior-offset", "padding",
                     "disjoint", "invalid", "callback-failure", "oracle"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
