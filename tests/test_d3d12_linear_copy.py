"""Validate the real copy adapter's commands without opening a GPU device."""
from pathlib import Path
import importlib.util
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("linear_copy_compiler", ROOT / "tests/test_rsx_linear_view.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

DRIVER = r'''
#define CINTERFACE
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#include <string.h>
#if __has_include("rsx_d3d12_linear_copy.h")
#include "rsx_d3d12_linear_copy.h"
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
static unsigned barriers, copies, bad;
static ID3D12Resource source, destination;
static void STDMETHODCALLTYPE barrier(ID3D12GraphicsCommandList* command, UINT count, const D3D12_RESOURCE_BARRIER* b) {
    barriers += count;
    for (UINT i = 0; i < count; ++i) {
        if (b[i].Type != D3D12_RESOURCE_BARRIER_TYPE_TRANSITION ||
            b[i].Transition.Subresource != D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES) ++bad;
        if (b[i].Transition.pResource == &source) {
            if (b[i].Transition.StateBefore != D3D12_RESOURCE_STATE_RENDER_TARGET) ++bad;
            if (b[i].Transition.StateAfter != D3D12_RESOURCE_STATE_COPY_SOURCE) ++bad;
        } else if (b[i].Transition.pResource == &destination) {
            if (b[i].Transition.StateBefore != D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE) ++bad;
            if (b[i].Transition.StateAfter != D3D12_RESOURCE_STATE_COPY_DEST) ++bad;
        } else ++bad;
    }
}
static void STDMETHODCALLTYPE copy(ID3D12GraphicsCommandList* command, const D3D12_TEXTURE_COPY_LOCATION* dst,
        UINT x, UINT y, UINT z, const D3D12_TEXTURE_COPY_LOCATION* src, const D3D12_BOX* box) {
    ++copies;
    if (src->pResource != &source || dst->pResource != &destination ||
        src->Type != D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX || src->SubresourceIndex ||
        dst->Type != D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX || dst->SubresourceIndex ||
        x || y || z || !box || box->left || box->top || box->front ||
        box->right != 2 || box->bottom != 1 || box->back != 1) ++bad;
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    ID3D12GraphicsCommandListVtbl table = {0}; table.ResourceBarrier = barrier; table.CopyTextureRegion = copy;
    ID3D12GraphicsCommandList command = {&table};
    D3D12_RESOURCE_STATES states[2] = {D3D12_RESOURCE_STATE_RENDER_TARGET,D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE};
    rsx_d3d12_linear_resource resources[2] = {0};
    resources[0].resource = &source; resources[1].resource = &destination;
    for (unsigned i = 0; i < 2; ++i) {
        rsx_linear_view view = {0xAB8000,4,4,16,4,0x85,1};
        resources[i].view = view; resources[i].state = &states[i];
        resources[i].format = DXGI_FORMAT_R8G8B8A8_UNORM;
    }
    resources[1].view.width = resources[1].view.height = 2;
    rsx_d3d12_linear_copy_context context = {&command,resources,2};
    rsx_linear_copy_span span = {0,0,0,0,2};
    if (!strcmp(argv[1], "copy")) {
        CHECK(rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        CHECK(barriers == 2 && copies == 1 && !bad);
        CHECK(states[0] == D3D12_RESOURCE_STATE_COPY_SOURCE && states[1] == D3D12_RESOURCE_STATE_COPY_DEST);
        CHECK(rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        CHECK(barriers == 2 && copies == 2 && !bad);
    } else if (!strcmp(argv[1], "invalid")) {
        CHECK(!rsx_d3d12_linear_copy_emit(NULL,0,1,&span));
        CHECK(!rsx_d3d12_linear_copy_emit(&context,0,2,&span));
        CHECK(!rsx_d3d12_linear_copy_emit(&context,0,1,NULL));
        CHECK(!rsx_d3d12_linear_copy_emit(&context,0,0,&span));
        span.width = 0; CHECK(!rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        span.width = 2; span.src_x = 1; CHECK(!rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        span.src_x = 0; span.dst_x = UINT32_MAX; CHECK(!rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        span.dst_x = 0; resources[1].format = DXGI_FORMAT_R32_FLOAT;
        CHECK(!rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        resources[1].format = DXGI_FORMAT_R8G8B8A8_UNORM; resources[1].view.location = 2;
        CHECK(!rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        resources[1].view.location = 1; resources[1].state = resources[0].state;
        CHECK(!rsx_d3d12_linear_copy_emit(&context,0,1,&span));
        CHECK(!barriers && !copies);
        CHECK(states[0] == D3D12_RESOURCE_STATE_RENDER_TARGET && states[1] == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    } else return 2;
    puts("PASS D3D12 linear copy adapter"); return 0;
}
#else
int main(void) { puts("FAIL D3D12 linear copy adapter not implemented"); return 1; }
#endif
'''


class LinearCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = type("CopyCompiler", (fixture.LinearViewTests,), {})
        original = fixture.DRIVER
        fixture.DRIVER = DRIVER
        try:
            compiler.setUpClass()
        finally:
            fixture.DRIVER = original
        cls.addClassCleanup(compiler.doClassCleanups)
        cls.executable = compiler.executable

    def test_copy_commands(self):
        for case in ("copy", "invalid"):
            with self.subTest(case=case):
                result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
