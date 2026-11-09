"""Exercise the backend's canonical texture binding at the SRV boundary.

The record, target, plan and feedback types come from production. No D3D12
device is created. Until the requested function exists, a no-effect placeholder
lets behavioral assertions report the missing binding/validation, not a compiler
or extraction error. Once present, the production function runs unchanged.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


def function(source, signature):
    start = source.index(signature)
    # A forward declaration is not the function body this fixture executes.
    while 0 <= source.find(";", start) < source.find("{", start):
        start = source.index(signature, start + len(signature))
    return source[start:source.index("\n}", start) + 2]


def structure(source, name):
    end = source.index("} " + name + ";") + len("} " + name + ";")
    return source[source.rfind("typedef struct {", 0, end):end]


DRIVER = r'''
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <setjmp.h>
#include "rsx_d3d12_surface_metadata.h"
#include "rsx_d3d12_guest_pipeline.h"
#include "rsx_linear_catalog.h"
#include "rsx_d3d12_feedback.h"
#define MAX_DRAWS 4
#define MAX_OFF_RTS RSX_D3D12_MAX_OFF_RTS
__RECORD__
__TARGET__
static struct {
    u32 draw_count;
    u64 frame_count;
    D3D12DrawRecord draws[MAX_DRAWS];
    OffRT off_rt[MAX_OFF_RTS];
} s_d3d;
__PLAN__
static u32 s_off_rt_count;
static rsx_linear_version s_off_rt_views[MAX_OFF_RTS];
static rsx_d3d12_feedback_context s_feedback;
static jmp_buf fatal_boundary;
static unsigned fatal_code;
static unsigned writes;
static u32 observed_heap;
static ID3D12Resource* observed_resource;
static DXGI_FORMAT observed_format;
static UINT observed_mapping;
static void capture_exit(UINT code) {
    fatal_code=code;
    longjmp(fatal_boundary, 1);
}
#define ExitProcess capture_exit
static void srv_write(u32 heap_slot, ID3D12Resource* resource,
        DXGI_FORMAT format, UINT mapping) {
    ++writes;
    observed_heap=heap_slot;
    observed_resource=resource;
    observed_format=format;
    observed_mapping=mapping;
}
typedef struct {
    ID3D12Resource api;
    D3D12_RESOURCE_DESC description;
} ProbeResource;
static ProbeResource canonical[MAX_OFF_RTS], snapshots[MAX_OFF_RTS];
static ID3D12ResourceVtbl resource_table;
static D3D12_RESOURCE_DESC* STDMETHODCALLTYPE resource_desc(
        ID3D12Resource* resource, D3D12_RESOURCE_DESC* out) {
    *out=((ProbeResource*)resource)->description;
    return out;
}
__ABORT__
__BIND__
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const char* mode=argv[1];
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    resource_table.GetDesc=resource_desc;
    s_d3d.draw_count=2;
    s_off_rt_count=MAX_OFF_RTS;
    for (int slot=0; slot<MAX_OFF_RTS; ++slot) {
        canonical[slot].api.lpVtbl=&resource_table;
        canonical[slot].description.Dimension=D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        canonical[slot].description.Width=16;
        canonical[slot].description.Height=12;
        canonical[slot].description.DepthOrArraySize=1;
        canonical[slot].description.MipLevels=1;
        canonical[slot].description.Format=DXGI_FORMAT_R8G8B8A8_UNORM;
        canonical[slot].description.SampleDesc.Count=1;
        snapshots[slot]=canonical[slot];
        s_d3d.off_rt[slot].res=&canonical[slot].api;
        s_d3d.off_rt[slot].w=16; s_d3d.off_rt[slot].h=12;
        s_d3d.off_rt[slot].dxgi=DXGI_FORMAT_R8G8B8A8_UNORM;
        s_d3d.off_rt[slot].st=D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
        s_off_rt_views[slot].view=(rsx_linear_view){
            .offset=0x1000u+(u32)slot*0x1000u, .width=16, .height=12,
            .pitch=64, .bytes_per_pixel=4, .format_id=0x85, .location=1};
        s_feedback.slots[slot].resource=&snapshots[slot].api;
        s_feedback.slots[slot].source=&canonical[slot].api;
        s_feedback.slots[slot].state=D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
        s_feedback.slots[slot].width=16; s_feedback.slots[slot].height=12;
        s_feedback.slots[slot].format=DXGI_FORMAT_R8G8B8A8_UNORM;
    }
    for (int draw=0; draw<MAX_DRAWS; ++draw) {
        for (int member=0; member<4; ++member) s_rt_plan[draw].target[member]=-1;
        for (int unit=0; unit<RSX_MAX_TEXTURES; ++unit) s_d3d.draws[draw].tex_rt[unit]=-1;
    }
    u32 draw=1;
    int unit=3, slot=1;
    int want_bound=1, want_abort=0, feedback=0;
    s_rt_plan[draw].texture_mask=1u<<unit;
    s_d3d.draws[draw].tex[unit].set=1;
    s_d3d.draws[draw].tex_rt[unit]=slot;
    if (!strcmp(mode,"inactive")) {
        s_rt_plan[draw].texture_mask=0;
        s_d3d.draws[draw].tex_rt[unit]=-2;
        want_bound=0;
    } else if (!strcmp(mode,"independent")) {
        s_d3d.draws[draw].tex_rt[unit]=-1; want_bound=0;
    } else if (!strcmp(mode,"last-slot")) {
        slot=MAX_OFF_RTS-1;
        s_d3d.draws[draw].tex_rt[unit]=slot;
    } else if (!strcmp(mode,"last-unit")) {
        unit=15; s_rt_plan[draw].texture_mask=0x8000u;
        s_d3d.draws[draw].tex[unit].set=1;
        s_d3d.draws[draw].tex_rt[unit]=slot;
    } else if (!strcmp(mode,"negative-id")) {
        s_d3d.draws[draw].tex_rt[unit]=-2; want_abort=1;
    } else if (!strcmp(mode,"catalog-id")) {
        s_off_rt_count=1; want_abort=1;
    } else if (!strcmp(mode,"capacity-id")) {
        s_d3d.draws[draw].tex_rt[unit]=MAX_OFF_RTS; want_abort=1;
    } else if (!strcmp(mode,"missing-target")) {
        s_d3d.off_rt[slot].res=NULL; want_abort=1;
    } else if (!strcmp(mode,"draw-count")) {
        draw=s_d3d.draw_count; want_abort=1;
    } else if (!strcmp(mode,"draw-capacity")) {
        draw=MAX_DRAWS; want_abort=1;
    } else if (!strcmp(mode,"negative-unit")) {
        unit=-1; want_abort=1;
    } else if (!strcmp(mode,"unit-capacity")) {
        unit=RSX_MAX_TEXTURES; want_abort=1;
    } else if (!strcmp(mode,"feedback")) {
        feedback=1;
    } else if (!strcmp(mode,"missing-snapshot")) {
        feedback=1; s_feedback.slots[slot].resource=NULL; want_abort=1;
    } else if (!strcmp(mode,"snapshot-alias")) {
        feedback=1; s_feedback.slots[slot].resource=&canonical[slot].api; want_abort=1;
    } else if (!strcmp(mode,"snapshot-wrong-source")) {
        feedback=1; s_feedback.slots[slot].source=&canonical[0].api; want_abort=1;
    } else if (!strcmp(mode,"snapshot-missing-source")) {
        feedback=1; s_feedback.slots[slot].source=NULL; want_abort=1;
    } else if (!strcmp(mode,"feedback-independent")) {
        feedback=1; s_d3d.draws[draw].tex_rt[unit]=-1; want_abort=1;
    } else if (strcmp(mode,"known")) return 2;
    if (feedback) {
        s_rt_plan[draw].feedback_mask=1u<<unit;
        s_rt_plan[draw].target[0]=slot;
    }
    if (setjmp(fatal_boundary)) {
        if (!want_abort || fatal_code!=2 || writes) {
            fprintf(stderr,"FAIL unexpected fatal=%u writes=%u expected_abort=%d\n",
                    fatal_code,writes,want_abort);
            return 3;
        }
        puts("PASS rejected before SRV write"); return 0;
    }
    int bound=temporal_bind_target_texture(draw,unit,177u);
    if (want_abort) {
        fprintf(stderr,"FAIL expected fatal before SRV write; returned=%d writes=%u\n",bound,writes);
        return 4;
    }
    if (!want_bound) {
        if (bound || writes) {
            fprintf(stderr,"FAIL independent/inactive input bound=%d writes=%u\n",bound,writes);
            return 5;
        }
        puts("PASS no canonical binding"); return 0;
    }
    ID3D12Resource* want=feedback ? &snapshots[slot].api : &canonical[slot].api;
    if (bound!=1 || writes!=1 || observed_heap!=177u || observed_resource!=want ||
            observed_format!=DXGI_FORMAT_R8G8B8A8_UNORM ||
            observed_mapping!=D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING) {
        fprintf(stderr,"FAIL canonical binding result=%d writes=%u heap=%u resource=%p expected=%p format=%d mapping=%u\n",
                bound,writes,observed_heap,(void*)observed_resource,(void*)want,
                (int)observed_format,observed_mapping);
        return 6;
    }
    puts("PASS exact canonical resource binding"); return 0;
}
'''


class TargetBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        planner = (SDK / "libs/video/rsx_d3d12_temporal_plan.h").read_text(encoding="utf-8")
        end = planner.index("} s_rt_plan[MAX_DRAWS];") + len("} s_rt_plan[MAX_DRAWS];")
        plan = planner[planner.rfind("static struct {", 0, end):end]
        signature = "static int temporal_bind_target_texture("
        if signature in backend:
            binding = function(backend, signature)
        else:
            # A missing implementation has no behavior. Do not invent the desired
            # algorithm or replace production with a copied model to obtain GREEN.
            binding = """static int temporal_bind_target_texture(u32 draw, int unit, u32 heap_slot) {
                (void)draw; (void)unit; (void)heap_slot; return 0;
            }"""
        source = (DRIVER.replace("__RECORD__", structure(backend, "D3D12DrawRecord"))
                  .replace("__TARGET__", structure(backend, "OffRT"))
                  .replace("__PLAN__", plan)
                  .replace("__ABORT__", function(backend, "static void temporal_abort("))
                  .replace("__BIND__", binding))
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-target-binding-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        probe = directory / "probe.c"
        probe.write_text(source, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"), str(probe),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_inactive_unit_does_not_bind_or_inspect_its_unused_id(self): self.run_case("inactive")
    def test_independent_texture_is_left_for_existing_asset_binding(self): self.run_case("independent")
    def test_known_texture_binds_its_exact_canonical_resource(self): self.run_case("known")
    def test_last_catalog_slot_is_valid(self): self.run_case("last-slot")
    def test_unit_fifteen_uses_its_compiled_mask_bit(self): self.run_case("last-unit")
    def test_feedback_binds_independent_snapshot_of_correct_source(self): self.run_case("feedback")
    def test_id_below_minus_one_aborts_before_binding(self): self.run_case("negative-id")
    def test_id_outside_catalog_aborts_before_binding(self): self.run_case("catalog-id")
    def test_id_outside_capacity_aborts_before_binding(self): self.run_case("capacity-id")
    def test_missing_canonical_resource_aborts_before_binding(self): self.run_case("missing-target")
    def test_draw_outside_recorded_count_aborts_before_binding(self): self.run_case("draw-count")
    def test_draw_outside_capacity_aborts_before_binding(self): self.run_case("draw-capacity")
    def test_negative_unit_aborts_before_binding(self): self.run_case("negative-unit")
    def test_unit_outside_capacity_aborts_before_binding(self): self.run_case("unit-capacity")
    def test_missing_feedback_snapshot_aborts_before_binding(self): self.run_case("missing-snapshot")
    def test_snapshot_cannot_alias_its_source(self): self.run_case("snapshot-alias")
    def test_snapshot_of_another_source_aborts_before_binding(self): self.run_case("snapshot-wrong-source")
    def test_snapshot_without_source_aborts_before_binding(self): self.run_case("snapshot-missing-source")
    def test_feedback_without_canonical_id_aborts_before_binding(self): self.run_case("feedback-independent")
