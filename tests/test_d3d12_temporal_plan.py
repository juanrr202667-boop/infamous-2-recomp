"""Exercise the production record planner, using the real record layout."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

DRIVER = r'''
#define MAX_DRAWS 8
#define MAX_OFF_RTS 32
static struct { D3D12DrawRecord draws[MAX_DRAWS]; u32 draw_count; } s_d3d;
static rsx_linear_version s_off_rt_views[MAX_OFF_RTS];
static u32 s_off_rt_count;
static int off_rt_get_view(const rsx_linear_view* view) {
    u32 slot;
    return rsx_linear_reserve_view(s_off_rt_views,MAX_OFF_RTS,&s_off_rt_count,view,&slot) ? -1 : (int)slot;
}
#if __has_include("rsx_d3d12_temporal_plan.h")
#include "rsx_d3d12_temporal_plan.h"
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s (%s)\n",__LINE__,#c,s_temporal_error?s_temporal_error:""); return 1; } } while (0)
static void record(unsigned i,u32 offset,u32 height) {
    D3D12DrawRecord* r = &s_d3d.draws[i];
    r->is_vp=1; r->rt_off=offset; r->rt_fmt=0x148; r->rt_w=1280; r->rt_h=height;
    r->surface_meta.valid=1; r->surface_meta.color_target=1;
    r->surface_meta.color_offset[0]=offset; r->surface_meta.color_pitch[0]=5120;
    r->surface_meta.color_dma[0]=0xFEED0000; r->surface_meta.dma_seen=1;
    r->surface_meta.clip_w=r->surface_meta.scissor_w=1280;
    r->surface_meta.clip_h=r->surface_meta.scissor_h=height;
}
static void texture(unsigned op,unsigned unit,u32 height,u32 domain) {
    s_d3d.draws[op].tex[unit].set=1;
    s_d3d.draws[op].tex[unit].raw=0xAB8000;
    s_d3d.draws[op].tex[unit].w=1280; s_d3d.draws[op].tex[unit].h=height;
    s_d3d.draws[op].tex[unit].format_word=0x0001A528|domain;
    s_d3d.draws[op].tex[unit].ctrl3=0x00101400;
}
int main(int argc,char** argv) {
    if(argc!=2)return 2;
    s_d3d.draw_count=3;
    record(0,0xAB8000,720); record(1,0xAB8000,180); record(2,0xE3C000,180);
    texture(2,0,180,1); texture(2,5,720,1);
    if(!strcmp(argv[1],"identities")) {
        CHECK(temporal_prepare_targets()); CHECK(s_off_rt_count==3);
        CHECK(temporal_prepare_textures(2,0x21));
        CHECK(s_rt_plan[0].target[0]==0 && s_rt_plan[1].target[0]==1 && s_rt_plan[2].target[0]==2);
        CHECK(s_off_rt_views[0].view.height==720 && s_off_rt_views[1].view.height==180);
        CHECK(s_d3d.draws[2].tex_rt[0]==1 && s_d3d.draws[2].tex_rt[5]==0);
    } else if(!strcmp(argv[1],"domains")) {
        s_d3d.draws[1].surface_meta.color_dma[0]=0xFEED0001;
        texture(2,0,180,2);
        CHECK(temporal_prepare_targets()); CHECK(temporal_prepare_textures(2,0x21));
        CHECK(s_d3d.draws[2].tex_rt[0]==1 && s_d3d.draws[2].tex_rt[5]==0);
        CHECK(s_off_rt_views[1].view.location==2 && s_off_rt_views[0].view.location==1);
    } else if(!strcmp(argv[1],"feedback")) {
        texture(1,5,720,1);
        CHECK(temporal_prepare_targets()); CHECK(temporal_prepare_textures(1,0));
        CHECK(s_d3d.draws[1].tex_rt[5]==-1);
        CHECK(!temporal_prepare_textures(1,1u<<5));
        CHECK(strstr(s_temporal_error,"feedback")!=NULL);
    } else if(!strcmp(argv[1],"feedback-exact")) {
        s_d3d.draw_count=1;
        record(0,0xAB8000,720);
        texture(0,2,720,1); texture(0,15,720,1);
        CHECK(temporal_prepare_targets()); CHECK(temporal_prepare_textures(0,0));
        CHECK(s_d3d.draws[0].tex_rt[2]==-1 && s_d3d.draws[0].tex_rt[15]==-1);
        CHECK(s_rt_plan[0].feedback_mask==0);
        CHECK(temporal_prepare_textures(0,(1u<<2)|(1u<<15)));
        CHECK(s_d3d.draws[0].tex_rt[2]==s_rt_plan[0].target[0]);
        CHECK(s_d3d.draws[0].tex_rt[15]==s_rt_plan[0].target[0]);
        CHECK(s_rt_plan[0].feedback_mask==((1u<<2)|(1u<<15)));
        CHECK(temporal_prepare_textures(0,1u<<2));
        CHECK(s_rt_plan[0].feedback_mask==(1u<<2));
        CHECK(s_d3d.draws[0].tex_rt[15]==-1);
        CHECK(temporal_prepare_textures(0,0));
        CHECK(s_rt_plan[0].feedback_mask==0);
    } else if(!strcmp(argv[1],"legacy")) {
        s_d3d.draw_count=1; s_d3d.draws[0].is_vp=0;
        CHECK(!temporal_prepare_targets());
        CHECK(s_temporal_error && strstr(s_temporal_error,"legacy"));
        CHECK(!s_off_rt_count);
    } else if(!strcmp(argv[1],"missing-dma")) {
        s_d3d.draws[0].surface_meta.dma_seen=0;
        CHECK(!temporal_prepare_targets()); CHECK(!s_off_rt_count);
    } else if(!strcmp(argv[1],"capacity")) {
        for(unsigned i=0;i<32;++i) {
            rsx_linear_view v={0x10000000ull+i*0x100000ull,16,16,64,4,0x85,1};
            CHECK(off_rt_get_view(&v)==(int)i);
        }
        CHECK(!temporal_prepare_targets()); CHECK(s_off_rt_count==32);
    } else if(!strcmp(argv[1],"clear")) {
        s_d3d.draw_count=1; s_d3d.draws[0].is_clear=1;
        s_d3d.draws[0].surface_meta.valid=0; s_d3d.draws[0].surface_meta.clear_flags=3;
        CHECK(temporal_prepare_targets()); CHECK(!s_off_rt_count);
        s_d3d.draws[0].surface_meta.valid=1; s_d3d.draws[0].surface_meta.clear_flags=0x10;
        CHECK(!temporal_prepare_targets()); CHECK(!s_off_rt_count);
    } else return 2;
    puts("PASS temporal record planning"); return 0;
}
#else
int main(void) { puts("FAIL temporal record planner not implemented"); return 1; }
#endif
'''


class TemporalPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        text = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        end = text.index("} D3D12DrawRecord;") + len("} D3D12DrawRecord;")
        start = text.rfind("typedef struct {", 0, end)
        prelude = '#include <stdio.h>\n#include <string.h>\n#include "rsx_linear_metadata.h"\n#include "rsx_linear_catalog.h"\n'
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-temporal-plan-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "probe.c"
        source.write_text(prelude + text[start:end] + DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([compiler,"/nologo","/O1","/D_CRT_SECURE_NO_WARNINGS",
            "/I"+str(SDK/"include"),"/I"+str(SDK/"libs/video"),str(source),
            "/Fe"+str(cls.executable),"/Fo"+str(directory/"probe.obj")],capture_output=True,text=True)
        if built.returncode: raise AssertionError(built.stdout+built.stderr)

    def test_record_plan(self):
        for case in ("identities","domains","feedback","missing-dma","capacity","clear"):
            with self.subTest(case=case):
                result=subprocess.run([str(self.executable),case],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_exact_view_feedback(self):
        result=subprocess.run([str(self.executable),"feedback-exact"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_legacy_draw_is_rejected_before_unordered_replay(self):
        result=subprocess.run([str(self.executable),"legacy"],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
