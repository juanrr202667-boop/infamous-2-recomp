/* CPU execution of production metadata/planning and extracted PSO statements.
 * The placeholders are filled by test_d3d12_no_color_target.py from the backend.
 * No device or game resources are created. */
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <d3dcompiler.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include "rsx_linear_metadata.h"
#include "rsx_linear_catalog.h"
#define MAX_DRAWS 4
#define MAX_OFF_RTS 32
__RECORD__
static struct {
    D3D12DrawRecord draws[MAX_DRAWS];
    u32 draw_count;
    ID3D12RootSignature* vp_root_sig;
    ID3D12Device* device;
} s_d3d;
static rsx_linear_version s_off_rt_views[MAX_OFF_RTS];
static u32 s_off_rt_count, reservations;
static int off_rt_get_view(const rsx_linear_view* view)
{
    u32 slot;
    ++reservations;
    return rsx_linear_reserve_view(s_off_rt_views,MAX_OFF_RTS,
        &s_off_rt_count,view,&slot)==RSX_LINEAR_OK ? (int)slot : -1;
}
#include "rsx_d3d12_temporal_plan.h"
__COUNT__
__DEPTH__
const GUID IID_ID3D12PipelineState = {0};
static D3D12_GRAPHICS_PIPELINE_STATE_DESC observed_pso;
static unsigned created_psos;
static ID3D12PipelineState pipeline;
static ID3DBlob bytecode;
static ID3D10BlobVtbl blob_table;
static ID3D12Device device;
static ID3D12DeviceVtbl device_table;
static unsigned char shader_bytes[16];
static void* STDMETHODCALLTYPE blob_data(ID3DBlob* self)
{
    (void)self; return shader_bytes;
}
static SIZE_T STDMETHODCALLTYPE blob_size(ID3DBlob* self)
{
    (void)self; return sizeof(shader_bytes);
}
static HRESULT STDMETHODCALLTYPE create_pipeline(ID3D12Device* self,
    const D3D12_GRAPHICS_PIPELINE_STATE_DESC* description,REFIID iid,void** out)
{
    (void)self; (void)iid;
    ++created_psos; observed_pso=*description;
    *out=&pipeline; return S_OK;
}
/* Only unrelated compiler work is omitted. The count guard and every PSO
 * descriptor assignment through CreateGraphicsPipelineState run unchanged. */
static ID3D12PipelineState* build_production_pso(int nrt,u32 cmask,u32 depth_key)
{
    DXGI_FORMAT rtfmt=DXGI_FORMAT_R8G8B8A8_UNORM;
    u32 blend=0,cull=0;
    ID3DBlob* vsb=&bytecode;
    ID3DBlob* pb=&bytecode;
    D3D12_INPUT_ELEMENT_DESC il[16]={{0}};
    HRESULT hr;
__PSO_GUARD__
__PSO_BUILD__
    return SUCCEEDED(hr) ? pso : NULL;
}
#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr,"FAIL line %d: %s; planner=%s\n",__LINE__,#condition, \
        s_temporal_error ? s_temporal_error : "none"); return 1; } } while (0)

static void capture_depth_draw(void)
{
    rsx_state state={0};
    state.color_target=CELL_GCM_SURFACE_TARGET_NONE;
    state.surface_format=0x148;
    for(int member=0;member<4;++member) {
        state.surface_color_offset[member]=0x0F2F0000;
        state.surface_color_pitch[member]=4096;
        state.surface_color_dma[member]=0xFEED0000;
    }
    state.surface_zeta_dma=0xFEED0000;
    state.surface_dma_seen=0x1F;
    state.surface_clip_w=state.scissor_w=1024;
    state.surface_clip_h=state.scissor_h=1024;
    s_d3d.draw_count=1;
    D3D12DrawRecord* draw=&s_d3d.draws[0];
    draw->is_vp=1; draw->rt_off=0x0F2F0000;
    draw->rt_w=draw->rt_h=1024; draw->rt_fmt=0x148;
    draw->fp_addr=0x0F2E9E81; draw->cmask=0;
    draw->depth_key=3u|(0x203u<<2);
    rsx_d3d12_snapshot_surface(&draw->surface_meta,&state,0,1.0f,0);
}

int main(int argc,char** argv)
{
    if(argc!=2) return 2;
    const char* mode=argv[1];
    capture_depth_draw();
    D3D12DrawRecord* draw=&s_d3d.draws[0];
    if(!strcmp(mode,"metadata-none")) {
        CHECK(rsx_declared_surface_target_count(&draw->surface_meta)==0);
    } else if(!strcmp(mode,"metadata-unknown")) {
        draw->surface_meta.color_target=3;
        CHECK(rsx_declared_surface_target_count(&draw->surface_meta)==RSX_LINEAR_META_UNSUPPORTED);
    } else if(!strcmp(mode,"metadata-missing")) {
        draw->surface_meta.valid=0;
        CHECK(rsx_declared_surface_target_count(&draw->surface_meta)==RSX_LINEAR_META_ABSENT);
        CHECK(rsx_declared_surface_target_count(NULL)==RSX_LINEAR_META_ABSENT);
    } else if(!strcmp(mode,"record-count-none")) {
        CHECK(dr_num_rts(draw)==0);
    } else if(!strncmp(mode,"pso-",4)) {
        blob_table.GetBufferPointer=blob_data; blob_table.GetBufferSize=blob_size;
        bytecode.lpVtbl=&blob_table;
        device_table.CreateGraphicsPipelineState=create_pipeline;
        device.lpVtbl=&device_table; s_d3d.device=&device;
        int count=0;
        if(!strcmp(mode,"pso-one-masked")) count=1;
        else if(!strcmp(mode,"pso-negative")) count=-1;
        else if(!strcmp(mode,"pso-overflow")) count=5;
        else CHECK(!strcmp(mode,"pso-zero"));
        ID3D12PipelineState* selected=build_production_pso(count,0,draw->depth_key);
        if(count<0 || count>4) {
            CHECK(!selected && !created_psos);
        } else {
            CHECK(selected==&pipeline && created_psos==1);
            CHECK(observed_pso.NumRenderTargets==(UINT)count);
            CHECK(observed_pso.PS.pShaderBytecode==shader_bytes &&
                observed_pso.PS.BytecodeLength==16);
            CHECK(observed_pso.DepthStencilState.DepthEnable &&
                observed_pso.DepthStencilState.DepthWriteMask==D3D12_DEPTH_WRITE_MASK_ALL &&
                observed_pso.DepthStencilState.DepthFunc==D3D12_COMPARISON_FUNC_LESS_EQUAL);
            CHECK(observed_pso.DSVFormat==DXGI_FORMAT_D24_UNORM_S8_UINT);
            if(count) CHECK(observed_pso.BlendState.RenderTarget[0].RenderTargetWriteMask==0);
        }
    } else {
        int expected_success=1,expected_targets=0,source_read=0;
        if(!strcmp(mode,"planner-none-zero-offset")) draw->rt_off=0;
        else if(!strcmp(mode,"planner-none-nonzero-mask")) {
            draw->cmask=15; expected_success=0;
        } else if(!strcmp(mode,"planner-unknown")) {
            draw->surface_meta.color_target=3; expected_success=0;
        } else if(!strcmp(mode,"planner-missing")) {
            draw->surface_meta.valid=0; expected_success=0;
        } else if(!strcmp(mode,"planner-active-a") || !strcmp(mode,"planner-active-b")) {
            draw->surface_meta.color_target=!strcmp(mode,"planner-active-a") ?
                CELL_GCM_SURFACE_TARGET_0 : CELL_GCM_SURFACE_TARGET_1;
            expected_targets=1;
        } else if(!strcmp(mode,"planner-depth-clear")) {
            draw->is_clear=1; draw->surface_meta.clear_flags=1;
        } else if(!strcmp(mode,"planner-color-clear-none")) {
            draw->is_clear=1; draw->surface_meta.clear_flags=0xF0;
            expected_success=0;
        } else if(!strcmp(mode,"planner-none-source-read")) {
            rsx_linear_view source={0x1000,16,16,64,4,0x85,1};
            u32 slot;
            CHECK(rsx_linear_reserve_view(s_off_rt_views,MAX_OFF_RTS,&s_off_rt_count,
                &source,&slot)==RSX_LINEAR_OK && slot==0);
            s_off_rt_views[0].complete_at=17;
            draw->tex[2].set=1; draw->tex[2].raw=0x1000;
            draw->tex[2].w=draw->tex[2].h=16;
            draw->tex[2].format_word=0x0001A529;
            draw->tex[2].ctrl3=0x00100040;
            source_read=1;
        } else CHECK(!strcmp(mode,"planner-none"));
        int result=temporal_prepare_targets();
        CHECK(result==expected_success);
        if(!expected_success) {
            CHECK(s_temporal_error && !reservations && !s_off_rt_count);
        } else {
            CHECK(reservations==(u32)expected_targets);
            CHECK(s_off_rt_count==(u32)(expected_targets+source_read));
            CHECK(!s_rt_plan[0].display_target);
            CHECK((s_rt_plan[0].target[0]>=0)==expected_targets);
            for(int member=1;member<4;++member) CHECK(s_rt_plan[0].target[member]==-1);
            CHECK(temporal_prepare_textures(0,source_read ? 4u : 0u));
            CHECK(!s_rt_plan[0].feedback_mask);
            if(source_read) {
                CHECK(s_d3d.draws[0].tex_rt[2]==0 && s_rt_plan[0].texture_mask==4);
                CHECK(s_off_rt_count==1 && s_off_rt_views[0].complete_at==17);
                CHECK(s_rt_plan[0].target[0]==-1);
            }
        }
    }
    puts("PASS no-color target production behavior");
    return 0;
}
