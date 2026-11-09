"""Validate explicit metadata for the first supported linear RT subset."""
from pathlib import Path
import importlib.util
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("linear_metadata_compiler", ROOT / "tests/test_rsx_linear_view.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

DRIVER = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#if __has_include("rsx_linear_metadata.h")
#include "rsx_linear_metadata.h"
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    rsx_d3d12_surface_metadata m = {0};
    m.valid = 1; m.dma_seen = 15; m.clip_w = 1280; m.clip_h = 720;
    for (unsigned i = 0; i < 4; ++i) {
        m.color_offset[i] = 0xAB8000 + i * 0x100000;
        m.color_pitch[i] = 5120; m.color_dma[i] = 0xFEED0000;
    }
    rsx_linear_view v, unchanged;
    memset(&v, 0xA5, sizeof(v)); unchanged = v;
    if (!strcmp(argv[1], "surface")) {
        for (unsigned i = 0; i < 4; ++i) {
            CHECK(rsx_declared_surface_linear(&m, 0x148, i, &v) == RSX_LINEAR_META_OK);
            CHECK(v.offset == m.color_offset[i] && v.pitch == 5120);
            CHECK(v.width == 1280 && v.height == 720 && v.bytes_per_pixel == 4);
            CHECK(v.format_id == 0x85 && v.location == 1);
        }
        m.color_dma[1] = 0xFEED0001;
        CHECK(rsx_declared_surface_linear(&m, 0x148, 1, &v) == RSX_LINEAR_META_OK && v.location == 2);
    } else if (!strcmp(argv[1], "region")) {
        m.clip_x = 8; m.clip_y = 11; m.clip_w = 320; m.clip_h = 180;
        CHECK(rsx_declared_surface_linear(&m, 0x148, 0, &v) == RSX_LINEAR_META_UNSUPPORTED);
        CHECK(!memcmp(&v, &unchanged, sizeof(v))); /* Nonzero origins require a separate contract. */
    } else if (!strcmp(argv[1], "missing")) {
        m.dma_seen &= ~2u;
        CHECK(rsx_declared_surface_linear(&m, 0x148, 1, &v) == RSX_LINEAR_META_UNKNOWN_DMA);
        CHECK(!memcmp(&v, &unchanged, sizeof(v)));
        m.valid = 0;
        CHECK(rsx_declared_surface_linear(&m, 0x148, 0, &v) == RSX_LINEAR_META_ABSENT);
        m.valid = 1; m.color_dma[0] = 0;
        CHECK(rsx_declared_surface_linear(&m, 0x148, 0, &v) == RSX_LINEAR_META_UNKNOWN_DMA);
    } else if (!strcmp(argv[1], "unsupported")) {
        CHECK(rsx_declared_surface_linear(&m, 0x248, 0, &v) == RSX_LINEAR_META_UNSUPPORTED);
        CHECK(rsx_declared_surface_linear(&m, 0x1148, 0, &v) == RSX_LINEAR_META_UNSUPPORTED);
        CHECK(rsx_declared_surface_linear(&m, 0x14B, 0, &v) == RSX_LINEAR_META_UNSUPPORTED);
        CHECK(!memcmp(&v, &unchanged, sizeof(v)));
    } else if (!strcmp(argv[1], "invalid")) {
        CHECK(rsx_declared_surface_linear(&m, 0x148, 4, &v) == RSX_LINEAR_META_INVALID);
        CHECK(rsx_declared_surface_linear(NULL, 0x148, 0, &v) == RSX_LINEAR_META_ABSENT);
        CHECK(rsx_declared_surface_linear(&m, 0x148, 0, NULL) == RSX_LINEAR_META_INVALID);
        m.color_pitch[0] = 1280;
        CHECK(rsx_declared_surface_linear(&m, 0x148, 0, &v) == RSX_LINEAR_META_INVALID);
        m.color_pitch[0] = 5120; m.clip_w = UINT32_MAX;
        CHECK(rsx_declared_surface_linear(&m, 0x148, 0, &v) == RSX_LINEAR_META_INVALID);
        CHECK(!memcmp(&v, &unchanged, sizeof(v)));
    } else if (!strcmp(argv[1], "texture")) {
        CHECK(rsx_declared_texture_linear(0xAB8000,1280,180,0x0001A529,0x00101400,&v) == RSX_LINEAR_META_OK);
        CHECK(v.offset == 0xAB8000 && v.width == 1280 && v.height == 180);
        CHECK(v.pitch == 5120 && v.bytes_per_pixel == 4 && v.format_id == 0x85 && v.location == 1);
        CHECK(rsx_declared_texture_linear(0xAB8000,1280,720,0x0001E52A,0x00101400,&v) == RSX_LINEAR_META_OK);
        CHECK(v.location == 2 && v.height == 720); /* UN is not a swizzle flag. */
    } else if (!strcmp(argv[1], "texture-invalid")) {
        const uint32_t formats[] = {0x00018529,0x0001A52D,0x0001A519,0x0002A529};
        for (unsigned i = 0; i < sizeof(formats)/sizeof(formats[0]); ++i)
            CHECK(rsx_declared_texture_linear(0xAB8000,1280,180,formats[i],0x00101400,&v) == RSX_LINEAR_META_UNSUPPORTED);
        CHECK(rsx_declared_texture_linear(0xAB8000,1280,180,0x0001A528,0x00101400,&v) == RSX_LINEAR_META_UNKNOWN_DMA);
        CHECK(rsx_declared_texture_linear(0xAB8000,1280,180,0x0001A529,0,&v) == RSX_LINEAR_META_UNSUPPORTED);
        CHECK(rsx_declared_texture_linear(0xAB8000,1280,180,0x0001A529,0x00100500,&v) == RSX_LINEAR_META_INVALID);
        CHECK(!memcmp(&v, &unchanged, sizeof(v)));
    } else return 2;
    puts("PASS declared linear metadata"); return 0;
}
#else
int main(void) { puts("FAIL declared linear metadata adapter not implemented"); return 1; }
#endif
'''


class LinearMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = type("MetadataCompiler", (fixture.LinearViewTests,), {})
        # Metadata includes real SDK state types as well as geometry.
        original_driver, original_run = fixture.DRIVER, fixture.subprocess.run
        fixture.DRIVER = DRIVER
        def compile_with_types(arguments, *args, **kwargs):
            if isinstance(arguments, list) and "/nologo" in arguments:
                arguments = [*arguments, "/I" + str(fixture.SDK / "include")]
            return original_run(arguments, *args, **kwargs)
        fixture.subprocess.run = compile_with_types
        try:
            compiler.setUpClass()
        finally:
            fixture.DRIVER, fixture.subprocess.run = original_driver, original_run
        cls.addClassCleanup(compiler.doClassCleanups)
        cls.executable = compiler.executable

    def test_declared_linear_subset(self):
        for case in ("surface", "region", "missing", "unsupported", "invalid", "texture", "texture-invalid"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
