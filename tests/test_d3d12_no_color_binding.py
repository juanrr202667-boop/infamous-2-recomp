"""Execute the production VP target binding, viewport, rebind and epilogue."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


def braced(source, start):
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


DRIVER = r'''
#define CINTERFACE
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include "rsx_linear_metadata.h"
#define MAX_DRAWS 4
#define MAX_VERTICES 4096
__RECORD__
static struct {
    u32 width, height, vp_parity;
    void* vp_vb_mapped;
    ID3D12GraphicsCommandList* cmd_list;
    struct {u32 w,h,off;} off_rt[4];
} s_d3d;
static struct { int target[4]; u32 display_target; } s_rt_plan[MAX_DRAWS];
static D3D12DrawRecord records[MAX_DRAWS];
static int s_last_drawn_slot, s_last_drawn_off;
static int bound_count=1, transitions, draw_count, case_bad, omitted_handles;
static SIZE_T bound_handle=500;
static float viewport_w, viewport_h;
static jmp_buf failure;
static void temporal_abort(u32 d,int unit,const char* reason) {
    (void)d;(void)unit; fprintf(stderr,"abort: %s\n",reason); longjmp(failure,1);
}
static void off_rt_transition(int slot,D3D12_RESOURCE_STATES state) {
    if(slot<0 || slot>=4 || state!=D3D12_RESOURCE_STATE_RENDER_TARGET) case_bad=1;
    ++transitions;
}
static D3D12_CPU_DESCRIPTOR_HANDLE off_rt_rtv(int slot) {
    D3D12_CPU_DESCRIPTOR_HANDLE handle={(SIZE_T)(100+slot)}; return handle;
}
static void screen_copy_capture(u32 fi) {(void)fi;}
static void subvp_capture(u32 fi) {(void)fi;}
static void STDMETHODCALLTYPE capture_targets(ID3D12GraphicsCommandList* list,UINT n,
        const D3D12_CPU_DESCRIPTOR_HANDLE* handles,BOOL single,
        const D3D12_CPU_DESCRIPTOR_HANDLE* depth) {
    (void)list;(void)single;
    if(!depth || depth->ptr!=700 || (n && !handles)) case_bad=1;
    if(!n && handles) omitted_handles=1;
    bound_count=n; bound_handle=n?handles[0].ptr:0;
}
static void STDMETHODCALLTYPE viewport(ID3D12GraphicsCommandList* list,UINT n,const D3D12_VIEWPORT* vp) {
    (void)list; if(n!=1) case_bad=1; viewport_w=vp->Width;viewport_h=vp->Height;
}
static void STDMETHODCALLTYPE scissor(ID3D12GraphicsCommandList* list,UINT n,const D3D12_RECT* rect) {
    (void)list;(void)rect;if(n!=1)case_bad=1;
}
static void STDMETHODCALLTYPE clear_depth(ID3D12GraphicsCommandList* list,D3D12_CPU_DESCRIPTOR_HANDLE h,
        D3D12_CLEAR_FLAGS f,FLOAT z,UINT8 stencil,UINT count,const D3D12_RECT* rect) {
    (void)list;(void)h;(void)f;(void)z;(void)stencil;(void)count;(void)rect;
}
static void STDMETHODCALLTYPE clear_color(ID3D12GraphicsCommandList* list,D3D12_CPU_DESCRIPTOR_HANDLE h,
        const FLOAT* rgba,UINT count,const D3D12_RECT* rect) {
    (void)list;(void)h;(void)rgba;(void)count;(void)rect;case_bad=1;
}
static void STDMETHODCALLTYPE draw(ID3D12GraphicsCommandList* list,UINT n,UINT instances,UINT vertex,UINT instance) {
    (void)list;(void)n;(void)instances;(void)vertex;(void)instance;
    D3D12DrawRecord* record=&records[draw_count++];
    if(record->surface_meta.color_target==CELL_GCM_SURFACE_TARGET_NONE) {
        if(bound_count!=0 || bound_handle || viewport_w!=1024 || viewport_h!=1024) case_bad=1;
    } else {
        SIZE_T expected=record->rt_off?100:500;
        if(bound_count!=1 || bound_handle!=expected) case_bad=1;
    }
}
__COUNT__
__RECT__
int main(int argc,char** argv) {
    if(argc!=2)return 2;
    int count=1, use_none=1;
    if(!strcmp(argv[1],"rebind"))_putenv_s("RT_REBIND","1");
    else _putenv_s("RT_REBIND","");
    _putenv_s("RT_CLEARDBG","");_putenv_s("VP_TESTTRI","");
    if(!strcmp(argv[1],"none-display") || !strcmp(argv[1],"none-offscreen") ||
       !strcmp(argv[1],"offscreen-none") || !strcmp(argv[1],"two-none")) count=2;
    if(!strcmp(argv[1],"offscreen-none"))use_none=0;
    for(int d=0;d<count;d++) {
        D3D12DrawRecord* record=&records[d];
        int none=d?strcmp(argv[1],"none-display") && strcmp(argv[1],"none-offscreen"):use_none;
        record->is_vp=1;record->vertex_count=3;
        record->surface_meta.valid=1;
        record->surface_meta.color_target=none?CELL_GCM_SURFACE_TARGET_NONE:CELL_GCM_SURFACE_TARGET_0;
        record->surface_meta.clip_w=record->surface_meta.clip_h=none?1024:16;
        record->surface_meta.scissor_w=record->surface_meta.scissor_h=4096;
        record->rt_off=none?0x0F2F0000:(!strcmp(argv[1],"none-display")?0:0x4000);
        record->rt_w=record->rt_h=none?1024:16;
        record->vp_w=record->vp_h=none?1024:16;
        for(int m=0;m<4;m++)s_rt_plan[d].target[m]=-1;
        if(!none) {s_rt_plan[d].display_target=!record->rt_off;if(record->rt_off)s_rt_plan[d].target[0]=0;}
    }
    s_d3d.width=1280;s_d3d.height=720;s_d3d.off_rt[0].w=s_d3d.off_rt[0].h=16;
    ID3D12GraphicsCommandListVtbl table={0};ID3D12GraphicsCommandList list={&table};
    table.OMSetRenderTargets=capture_targets;table.RSSetViewports=viewport;table.RSSetScissorRects=scissor;
    table.ClearDepthStencilView=clear_depth;table.ClearRenderTargetView=clear_color;table.DrawInstanced=draw;
    s_d3d.cmd_list=&list;
    D3D12_CPU_DESCRIPTOR_HANDLE rtv_handle={500},dsv_handle={700};u32 fi=0;
    int cur_rt=-1,cur_m[3]={-1,-1,-1};float vp_last_w=0,vp_last_h=0;
    float dvp_x=0,dvp_y=0,dvp_w=0,dvp_h=0;
    if(setjmp(failure))return 1;
    for(u32 d=0;d<(u32)count;d++) {
        const D3D12DrawRecord* dr=&records[d];
__BIND__
__VIEWPORT__
__REBIND_DRAW__
    }
__EPILOGUE__
    if(case_bad || omitted_handles || draw_count!=count || bound_count!=1 || bound_handle!=500)return 1;
    if(!strcmp(argv[1],"two-none") && transitions)return 1;
    puts("PASS zero color binding preserves draw, DSV, viewport and transitions");return 0;
}
'''


class NoColorBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        end = backend.index("} D3D12DrawRecord;") + len("} D3D12DrawRecord;")
        record = backend[backend.rfind("typedef struct {", 0, end):end]
        binding_start = backend.index("/* Render-to-texture: retarget when this op's surfaces differ.")
        binding_end = backend.index("                if (dr->is_clear)", binding_start)
        viewport_start = backend.index("/* Per-draw viewport: the guest rect when sane", binding_end)
        viewport_end = backend.index("/* Per-draw textures:", viewport_start)
        draw_start = backend.index("/* RT_REBIND=1:", viewport_end)
        draw_end = backend.index("                temporal_after_op(d);", draw_start)
        epilogue_start = backend.index("/* Leave the backbuffer bound for the dump/present epilogue.", draw_end)
        source = DRIVER.replace("__RECORD__", record)
        for marker, code in {
            "__COUNT__": braced(backend, backend.index("static int dr_num_rts(")),
            "__RECT__": braced(backend, backend.index("static D3D12_RECT temporal_clear_rect(")),
            "__BIND__": backend[binding_start:binding_end],
            "__VIEWPORT__": backend[viewport_start:viewport_end],
            "__REBIND_DRAW__": backend[draw_start:draw_end],
            "__EPILOGUE__": braced(backend, backend.index("if (cur_rt", epilogue_start)),
        }.items():
            source = source.replace(marker, code)
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-no-color-binding-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(source, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"), str(probe),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")],
            capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_zero_color_binds_no_rtv_and_keeps_depth_draw_and_extent(self):
        self.run_case("none")

    def test_zero_color_to_display_rebinds_display(self):
        self.run_case("none-display")

    def test_zero_color_to_offscreen_rebinds_offscreen(self):
        self.run_case("none-offscreen")

    def test_offscreen_to_zero_color_removes_color_binding(self):
        self.run_case("offscreen-none")

    def test_debug_rebind_preserves_zero_color(self):
        self.run_case("rebind")

    def test_consecutive_zero_color_does_not_transition_color_resources(self):
        self.run_case("two-none")


if __name__ == "__main__":
    unittest.main()
