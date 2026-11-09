"""Reflect actual compiled HLSL on CPU; no device, GPU, or game resources."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

DRIVER = r'''
#define CINTERFACE
#define COBJMACROS
#include "rsx_d3d12_shader_resources.h"
#include <stdio.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { \
    printf("FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)

static ID3DBlob* compile(const char* source) {
    ID3DBlob* code = NULL;
    ID3DBlob* errors = NULL;
    HRESULT hr = D3DCompile(source, strlen(source), NULL, NULL, NULL,
        "main", "ps_5_0", 0, 0, &code, &errors);
    if (FAILED(hr)) {
        printf("compile failed: %08lx\n", (unsigned long)hr);
        if (errors) printf("%s\n", (const char*)errors->lpVtbl->GetBufferPointer(errors));
    }
    if (errors) errors->lpVtbl->Release(errors);
    return code;
}

static int check_source(const char* source, u32 expected) {
    ID3DBlob* shader = compile(source);
    u32 mask;
    CHECK(shader != NULL);
    mask = rsx_d3d12_shader_texture_mask(shader);
    shader->lpVtbl->Release(shader);
    printf("mask=%08x expected=%08x\n", mask, expected);
    CHECK(mask == expected);
    return 0;
}

int main(int argc, char** argv) {
    const char* mode;
    if (argc != 2) return 2;
    mode = argv[1];
    if (!strcmp(mode, "unused")) {
        return check_source("Texture2D ignored:register(t5); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return float4(1,2,3,4);}", 0);
    }
    if (!strcmp(mode, "t0")) {
        return check_source("Texture2D tex:register(t0); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex.Sample(smp,uv);}", 1);
    }
    if (!strcmp(mode, "t5")) {
        return check_source("Texture2D tex:register(t5); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex.Sample(smp,uv);}", 0x20);
    }
    if (!strcmp(mode, "t15")) {
        return check_source("Texture2D tex:register(t15); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex.Sample(smp,uv);}", 0x8000);
    }
    if (!strcmp(mode, "combined")) {
        return check_source("Texture2D a:register(t0); Texture2D b:register(t5);"
            "Texture2D c:register(t15); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{"
            "return a.Sample(smp,uv)+b.Sample(smp,uv)+c.Sample(smp,uv);}", 0x8021);
    }
    if (!strcmp(mode, "branch")) {
        return check_source("Texture2D tex:register(t5); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{"
            "if(uv.x>0.5)return tex.Sample(smp,uv);return float4(1,2,3,4);}", 0x20);
    }
    if (!strcmp(mode, "array")) {
        return check_source("Texture2D tex[4]:register(t4); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex[2].Sample(smp,uv);}", 0x40);
    }
    if (!strcmp(mode, "array-all")) {
        return check_source("Texture2D tex[4]:register(t4); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex[0].Sample(smp,uv)"
            "+tex[1].Sample(smp,uv)+tex[2].Sample(smp,uv)+tex[3].Sample(smp,uv);}", 0xF0);
    }
    if (!strcmp(mode, "full-array")) {
        return check_source("Texture2D tex[16]:register(t0); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex[15].Sample(smp,uv);}", 0x8000);
    }
    if (!strcmp(mode, "full-array-all")) {
        return check_source(FULL_ARRAY_SOURCE, 0xFFFF);
    }
    if (!strcmp(mode, "outside")) {
        return check_source("Texture2D tex:register(t16); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex.Sample(smp,uv);}", UINT32_MAX);
    }
    if (!strcmp(mode, "array-outside")) {
        return check_source("Texture2D tex[2]:register(t15); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex[0].Sample(smp,uv)"
            "+tex[1].Sample(smp,uv);}", UINT32_MAX);
    }
    if (!strcmp(mode, "invalid")) {
        ID3DBlob* blob = NULL;
        CHECK(rsx_d3d12_shader_texture_mask(NULL) == UINT32_MAX);
        CHECK(SUCCEEDED(D3DCreateBlob(64, &blob)));
        memset(blob->lpVtbl->GetBufferPointer(blob), 0xA5, 64);
        CHECK(rsx_d3d12_shader_texture_mask(blob) == UINT32_MAX);
        blob->lpVtbl->Release(blob);
        blob = NULL;
        CHECK(SUCCEEDED(D3DCreateBlob(0, &blob)));
        CHECK(rsx_d3d12_shader_texture_mask(blob) == UINT32_MAX);
        blob->lpVtbl->Release(blob);
        puts("invalid masks=ffffffff");
        return 0;
    }
    if (!strcmp(mode, "truncated")) {
        ID3DBlob* shader = compile("Texture2D tex:register(t5); SamplerState smp:register(s0);"
            "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return tex.Sample(smp,uv);}");
        SIZE_T size, cut;
        CHECK(shader != NULL);
        size = shader->lpVtbl->GetBufferSize(shader);
        for (cut = 1; cut <= 33; ++cut) {
            ID3DBlob* short_blob = NULL;
            u32 mask;
            CHECK(SUCCEEDED(D3DCreateBlob(cut, &short_blob)));
            memcpy(short_blob->lpVtbl->GetBufferPointer(short_blob),
                shader->lpVtbl->GetBufferPointer(shader), cut);
            mask = rsx_d3d12_shader_texture_mask(short_blob);
            short_blob->lpVtbl->Release(short_blob);
            CHECK(mask == UINT32_MAX);
        }
        {
            ID3DBlob* short_blob = NULL;
            u32 mask;
            CHECK(size > 34);
            CHECK(SUCCEEDED(D3DCreateBlob(size - 1, &short_blob)));
            memcpy(short_blob->lpVtbl->GetBufferPointer(short_blob),
                shader->lpVtbl->GetBufferPointer(shader), size - 1);
            mask = rsx_d3d12_shader_texture_mask(short_blob);
            short_blob->lpVtbl->Release(short_blob);
            CHECK(mask == UINT32_MAX);
        }
        shader->lpVtbl->Release(shader);
        puts("34 truncated blobs rejected");
        return 0;
    }
    if (!strcmp(mode, "fallback-texture")) return check_source(FALLBACK_TEXTURE, 1);
    if (!strcmp(mode, "fallback-color")) return check_source(FALLBACK_COLOR, 0);
    return 2;
}
'''


def fallback_sources():
    source = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
    start = source.index("static void compile_vp(")
    source = source[start:]
    result = []
    for name in ("ps", "ps_col"):
        match = re.search(r"static const char " + name + r"\[\]\s*=\s*((?:\s*\"(?:\\.|[^\"\\])*\")+)\s*;", source)
        if not match:
            raise AssertionError("compile_vp fallback source missing: " + name)
        result.append("".join(ast.literal_eval(token) for token in
                              re.findall(r'"(?:\\.|[^"\\])*"', match.group(1))))
    return result


class ShaderResourcesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-shader-resources-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        fallback_texture, fallback_color = fallback_sources()
        full_array_source = ("Texture2D tex[16]:register(t0); SamplerState smp:register(s0);"
                             "float4 main(float2 uv:TEXCOORD0):SV_TARGET{return " +
                             "+".join(f"tex[{i}].Sample(smp,uv)" for i in range(16)) + ";}")
        source = ("#define FALLBACK_TEXTURE " + json.dumps(fallback_texture) + "\n"
                  "#define FALLBACK_COLOR " + json.dumps(fallback_color) + "\n"
                  "#define FULL_ARRAY_SOURCE " + json.dumps(full_array_source) + "\n" + DRIVER)
        cls.probes = []
        for extension in ("c", "cpp"):
            probe = directory / ("probe." + extension)
            probe.write_text(source, encoding="utf-8")
            executable = directory / ("probe-" + extension + ".exe")
            result = subprocess.run([
                compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
                "/I" + str(SDK / "libs/video"), "/I" + str(SDK / "include"),
                str(probe), "/Fe" + str(executable), "/Fo" + str(directory / (extension + ".obj")),
                "/link", "d3dcompiler.lib",
            ], capture_output=True, text=True, timeout=30)
            cls.probes.append((extension, executable, result))

    def run_probe(self, mode):
        for language, executable, compilation in self.probes:
            with self.subTest(language=language):
                self.assertEqual(compilation.returncode, 0,
                                 "Reflection API must compile:\n" + compilation.stdout + compilation.stderr)
                result = subprocess.run([str(executable), mode], capture_output=True,
                                        text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unused_declaration_is_optimized_out(self): self.run_probe("unused")
    def test_texture_zero(self): self.run_probe("t0")
    def test_texture_five(self): self.run_probe("t5")
    def test_texture_fifteen(self): self.run_probe("t15")
    def test_multiple_noncontiguous_bindings(self): self.run_probe("combined")
    def test_conditional_texture_reference_is_retained(self): self.run_probe("branch")
    def test_unused_array_elements_follow_compiler_optimization(self): self.run_probe("array")
    def test_used_resource_array_keeps_all_reflected_slots(self): self.run_probe("array-all")
    def test_optimized_sixteen_resource_array_keeps_referenced_slot(self): self.run_probe("full-array")
    def test_fully_used_sixteen_resource_array_keeps_all_bits(self): self.run_probe("full-array-all")
    def test_binding_sixteen_fails_closed(self): self.run_probe("outside")
    def test_array_crossing_sixteen_fails_closed(self): self.run_probe("array-outside")
    def test_null_and_invalid_blobs_fail_closed(self): self.run_probe("invalid")
    def test_truncated_real_shader_fails_closed(self): self.run_probe("truncated")
    def test_actual_vp_textured_fallback(self): self.run_probe("fallback-texture")
    def test_actual_vp_color_fallback(self): self.run_probe("fallback-color")


if __name__ == "__main__":
    unittest.main()
