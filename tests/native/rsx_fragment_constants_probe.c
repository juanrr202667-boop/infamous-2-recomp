/* CPU cache/record/binding integration. Production functions and record types
 * replace the tokens below. Shader compilation and reflection are real; only
 * device/pipeline/resource/command-list COM objects are CPU stores. */
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <d3dcompiler.h>
#include <d3d11shader.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include "rsx_commands.h"
#include "rsx_fp_decompiler.h"
__HEADERS__
#define MAX_DRAWS 8
__STRIDES__
__CAPACITY__
__RECORD__
__VS_ENTRY__
__FP_ENTRY__
__PLAN__
typedef struct {
    ID3D12PipelineState api;
    ID3DBlob* shader;
    char source[32768];
} ProbePipeline;
typedef struct {
    ID3D12Resource api;
    D3D12_GPU_VIRTUAL_ADDRESS address;
} ProbeResource;
static ProbePipeline pipelines[8];
static ProbeResource vertex_resource,fragment_resource;
static ID3D12Device device;
static ID3D12DeviceVtbl device_table;
static ID3D12PipelineStateVtbl pipeline_table;
static ID3D12ResourceVtbl resource_table;
static ID3D12GraphicsCommandList command_list;
static ID3D12GraphicsCommandListVtbl command_table;
static unsigned compiles,creates;
static char last_source[32768];
static D3D12_GPU_VIRTUAL_ADDRESS bound_cb[3];
static unsigned char vertex_store[2*MAX_DRAWS*VP_CB_STRIDE];
static unsigned char fragment_store[2*MAX_DRAWS*VP_FPCB_STRIDE];
static unsigned char memory[8192];
static u32 s_recorded_fp_hash[MAX_DRAWS];
static u8 s_recorded_fp_prefix[MAX_DRAWS][32];
static u8 s_recorded_fp_code[MAX_DRAWS][4096];
static u32 s_recorded_fp_size[MAX_DRAWS];
uint8_t* vm_base=memory;
#define PSO_HISTORY_CAP 16384
static u64 s_pso_history[PSO_HISTORY_CAP];
static int s_perf_pso_hashbytes,s_perf_pso_calls,s_perf_pso_miss;
static int s_perf_pso_evictions,s_perf_pso_reused_evicted,s_perf_pso_new_keys;
static u64 s_pso_lru_counter;
static int s_diff_vs_idx,s_diff_vs_identity,s_diff_gen,s_diff_blend,s_diff_nrt,s_diff_rtfmt;
static int s_diff_exp32,s_diff_ucode_hash,s_diff_cmask,s_diff_cull,s_diff_cube_mask,s_diff_depth_key;
static int s_diff_no_fp_match;
static int s_lock_valid;
static float s_lock_x,s_lock_y;
static struct {
    int vp_vs_n,vp_fp_n,vp_uses_c03;
    u32 vp_gen,vp_parity;
    u32 draw_count;
    u64 frame_count;
    D3D12DrawRecord draws[MAX_DRAWS];
    VPVSEntry vp_vs[2];
    VPFPEntry vp_fp[VP_FP_CACHE];
    ID3DBlob* vp_vs_blob;
    ID3D12RootSignature* vp_root_sig;
    ID3D12Device* device;
    ID3D12Resource *vp_cb,*vp_fpcb;
    ID3D12GraphicsCommandList* cmd_list;
    void *vp_cb_mapped,*vp_fpcb_mapped;
    const rsx_state* current_rsx_state;
} s_d3d;
const GUID IID_ID3D12PipelineState={0};
u32 cellGcmResolveLocated(int local,u32 raw) { (void)local;return raw; }

static HRESULT capture_compile(LPCVOID data,SIZE_T size,LPCSTR name,
    const D3D_SHADER_MACRO* macros,ID3DInclude* include,LPCSTR entry,LPCSTR target,
    UINT flags,UINT flags2,ID3DBlob** out,ID3DBlob** error)
{
    if(size>=sizeof(last_source)) return E_FAIL;
    ++compiles;
    memcpy(last_source,data,size);last_source[size]=0;
    return D3DCompile(data,size,name,macros,include,entry,target,flags,flags2,out,error);
}
#define D3DCompile capture_compile
static ULONG STDMETHODCALLTYPE pipeline_release(ID3D12PipelineState* self)
{
    ProbePipeline* pipeline=(ProbePipeline*)self;
    if(pipeline->shader) {
        pipeline->shader->lpVtbl->Release(pipeline->shader);
        pipeline->shader=NULL;
    }
    return 0;
}
static HRESULT STDMETHODCALLTYPE create_pipeline(ID3D12Device* self,
    const D3D12_GRAPHICS_PIPELINE_STATE_DESC* desc,REFIID iid,void** out)
{
    (void)self;(void)iid;
    if(creates>=8) return E_FAIL;
    ProbePipeline* pipeline=&pipelines[creates++];
    pipeline->api.lpVtbl=&pipeline_table;
    HRESULT hr=D3DCreateBlob(desc->PS.BytecodeLength,&pipeline->shader);
    if(FAILED(hr)) return hr;
    memcpy(pipeline->shader->lpVtbl->GetBufferPointer(pipeline->shader),
        desc->PS.pShaderBytecode,desc->PS.BytecodeLength);
    strcpy(pipeline->source,last_source);
    *out=&pipeline->api;return S_OK;
}
static D3D12_GPU_VIRTUAL_ADDRESS STDMETHODCALLTYPE resource_address(ID3D12Resource* self)
{
    return ((ProbeResource*)self)->address;
}
static void STDMETHODCALLTYPE bind_cb(ID3D12GraphicsCommandList* self,
    UINT root,D3D12_GPU_VIRTUAL_ADDRESS address)
{
    (void)self;
    if(root<3) bound_cb[root]=address;
}
__DEPTH__
__REPLACE__
__ABORT__
__PSO__
__RECORD_CB__
static D3D12_GPU_VIRTUAL_ADDRESS bind_production_record(const D3D12DrawRecord* dr)
{
    bound_cb[2]=0;
__BIND_CB__
    return bound_cb[2];
}
#define CHECK(c) do { if(!(c)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#c);return 1; } } while(0)

/* Use the guest encoder and instruction fields already exercised by the SDK
 * tests. Prepending test1's MOV COL0 to test3's MAD makes CONST observable. */
__SYNTHETIC_HELPERS__
__SYNTHETIC_BITS__
static void program(void)
{
    u8* p=vm_base+0x100;
    put_word(p,OPC(0x01)|OUTMASK_ALL|INSRC(1));
    put_word(p+4,T_INPUT|SWZ_IDENT|EXEC_ALWAYS);
    put_word(p+8,0);put_word(p+12,0);
    put_word(p+16,OPC(0x04)|OUTMASK_ALL|(1u<<31)|END);
    put_word(p+20,T_TEMP|REG(0)|SWZ_IDENT|EXEC_ALWAYS);
    put_word(p+24,T_CONST|SWZ_IDENT);
    put_word(p+28,T_TEMP|REG(0)|SWZ_IDENT);
    put_float(p+32,0.5f);put_float(p+36,0.25f);
    put_float(p+40,0.0f);put_float(p+44,1.0f);
}
static void constant_pairs(unsigned count,int terminated)
{
    u8* p=vm_base+0x100;
    memset(p,0,4096);
    for(unsigned i=0;i<count;++i) {
        put_word(p+i*32,OPC(0x01)|OUTMASK_ALL|
            ((terminated && i+1==count)?END:0));
        put_word(p+i*32+4,T_CONST|SWZ_IDENT|EXEC_ALWAYS);
        put_word(p+i*32+8,0);put_word(p+i*32+12,0);
        put_float(p+i*32+16,(float)(i+1));put_float(p+i*32+20,0.25f);
        put_float(p+i*32+24,0.0f);put_float(p+i*32+28,1.0f);
    }
}
static void init_record(D3D12DrawRecord* draw,u32 slot)
{
    memset(draw,0,sizeof(*draw));
    draw->is_vp=1;draw->fp_addr=0x101;draw->fp_exp32=1;
    draw->vs_idx=0;draw->cb_slot=slot;draw->cmask=15;
    draw->alpha_ctl=(1u<<16)|(0x204u<<8)|128u;
    draw->tex[0].set=1;draw->tex[0].fmt=0x40;
    draw->tex[0].w=64;draw->tex[0].h=32;
}
static ID3D12PipelineState* prepare(const D3D12DrawRecord* draw)
{
    return vp_get_fp_pso(draw->vs_idx,draw->fp_addr,0,1,
        DXGI_FORMAT_R8G8B8A8_UNORM,draw->fp_exp32,draw->cmask,0,0,0,
        s_recorded_fp_code[draw->cb_slot],s_recorded_fp_size[draw->cb_slot]);
}
static int effective_constant_at(ID3D12PipelineState* state,
    const D3D12DrawRecord* draw,float wanted,unsigned constant_index)
{
    CHECK(state);
    ProbePipeline* pipeline=(ProbePipeline*)state;
    static const GUID reflection_iid={0x8d536ca1,0x0cca,0x4956,
        {0xa8,0x37,0x78,0x69,0x63,0x75,0x55,0x84}};
    ID3D11ShaderReflection* reflection=NULL;
    CHECK(SUCCEEDED(D3DReflect(pipeline->shader->lpVtbl->GetBufferPointer(pipeline->shader),
        pipeline->shader->lpVtbl->GetBufferSize(pipeline->shader),&reflection_iid,(void**)&reflection)));
    ID3D11ShaderReflectionConstantBuffer* cb=
        reflection->lpVtbl->GetConstantBufferByName(reflection,"PSConstants");
    D3D11_SHADER_BUFFER_DESC buffer;
    int buffered=SUCCEEDED(cb->lpVtbl->GetDesc(cb,&buffer));
    D3D12_GPU_VIRTUAL_ADDRESS address=bind_production_record(draw);
    CHECK(address>=fragment_resource.address);
    u64 offset=address-fragment_resource.address;
    CHECK(offset+VP_FPCB_STRIDE<=sizeof(fragment_store));
    if(buffered) {
        D3D11_SHADER_INPUT_BIND_DESC binding;
        CHECK(SUCCEEDED(reflection->lpVtbl->GetResourceBindingDescByName(
            reflection,"PSConstants",&binding)));
        CHECK(binding.Type==D3D_SIT_CBUFFER && binding.BindPoint==1);
        ID3D11ShaderReflectionVariable* variable=cb->lpVtbl->GetVariableByName(cb,"fp_constants");
        D3D11_SHADER_VARIABLE_DESC constants,alpha;
        CHECK(SUCCEEDED(variable->lpVtbl->GetDesc(variable,&constants)));
        variable=cb->lpVtbl->GetVariableByName(cb,"fp_alpha");
        CHECK(SUCCEEDED(variable->lpVtbl->GetDesc(variable,&alpha)));
        /* Selected D3D12 contract: 16 scales, alpha c16, constants c17.
         * Read reflected offsets, rather than trusting writer macros. */
        CHECK(constants.StartOffset==272 && constants.Size>=(constant_index+1)*16);
        CHECK(alpha.StartOffset==256 && alpha.Size==16);
        CHECK(constants.StartOffset+(constant_index+1)*16<=VP_FPCB_STRIDE);
        const float* values=(const float*)(fragment_store+offset+
            constants.StartOffset+constant_index*16);
        CHECK(values[0]==wanted && values[1]==0.25f && values[2]==0.0f && values[3]==1.0f);
        const float* a=(const float*)(fragment_store+offset+alpha.StartOffset);
        CHECK(a[0]==1.0f && a[1]==128.0f/255.0f && a[2]==4.0f);
    } else {
        char literal[80];
        snprintf(literal,sizeof(literal),"float4(%g,0.25,0,1)",(double)wanted);
        if(!strstr(pipeline->source,literal)) {
            fprintf(stderr,"FAIL effective unbuffered literal wanted=%g; old PSO reused\n",(double)wanted);
            reflection->lpVtbl->Release(reflection);return 1;
        }
    }
    reflection->lpVtbl->Release(reflection);
    return 0;
}
static int effective_constant(ID3D12PipelineState* state,
    const D3D12DrawRecord* draw,float wanted)
{
    return effective_constant_at(state,draw,wanted,0);
}

int main(int argc,char** argv)
{
    if(argc!=2)return 2;
    const char* mode=argv[1];
    if(!strcmp(mode,"deferred-changed-code")) _putenv_s("RSX_CAP_FP_STATE","1");
    device_table.CreateGraphicsPipelineState=create_pipeline;device.lpVtbl=&device_table;
    pipeline_table.Release=pipeline_release;
    resource_table.GetGPUVirtualAddress=resource_address;
    vertex_resource.api.lpVtbl=fragment_resource.api.lpVtbl=&resource_table;
    vertex_resource.address=0x400000;fragment_resource.address=0x800000;
    command_table.SetGraphicsRootConstantBufferView=bind_cb;command_list.lpVtbl=&command_table;
    s_d3d.device=&device;s_d3d.cmd_list=&command_list;
    s_d3d.vp_cb=&vertex_resource.api;s_d3d.vp_fpcb=&fragment_resource.api;
    s_d3d.vp_cb_mapped=vertex_store;s_d3d.vp_fpcb_mapped=fragment_store;
    s_d3d.vp_vs_n=1;s_d3d.vp_gen=7;s_d3d.vp_parity=1;
    CHECK(SUCCEEDED(D3DCreateBlob(4,&s_d3d.vp_vs[0].vs)));
    s_d3d.vp_vs[0].identity=99;
    rsx_state state={0};state.vertex_constants[0][0]=1;
    s_d3d.current_rsx_state=&state;
    D3D12DrawRecord a,b;
    init_record(&a,!strcmp(mode,"reordered-slots") ? 3u : 0u);
    init_record(&b,1);
    program();
    if(!strcmp(mode,"limit-128")) {
        constant_pairs(128,1);
        vp_record_cb(a.cb_slot,0,&a);
        CHECK(VP_FPCB_STRIDE>=272+128*16);
        ID3D12PipelineState* limit=prepare(&a);
        CHECK(effective_constant_at(limit,&a,1.0f,0)==0);
        CHECK(effective_constant_at(limit,&a,128.0f,127)==0);
        const float* values=(const float*)(fragment_store+
            ((u64)MAX_DRAWS+a.cb_slot)*VP_FPCB_STRIDE+272);
        for(unsigned i=0;i<128;++i)CHECK(values[i*4]==(float)(i+1));
        puts("PASS effective constants 128 pairs in 4096-byte window");return 0;
    }
    if(!strcmp(mode,"unterminated") || !strcmp(mode,"truncated") ||
            !strcmp(mode,"missing-fp-mapping") || !strcmp(mode,"missing-vp-mapping") ||
            !strcmp(mode,"both-mappings-missing")) {
        if(!strcmp(mode,"missing-fp-mapping"))s_d3d.vp_fpcb_mapped=NULL;
        else if(!strcmp(mode,"missing-vp-mapping"))s_d3d.vp_cb_mapped=NULL;
        else if(!strcmp(mode,"both-mappings-missing")) {
            s_d3d.vp_cb_mapped=NULL;s_d3d.vp_fpcb_mapped=NULL;
        }
        else if(!strcmp(mode,"unterminated"))constant_pairs(128,0);
        else {
            constant_pairs(127,0);
            u8* p=vm_base+0x100;
            put_word(p+4064,OPC(0x01)|OUTMASK_ALL|INSRC(1));
            put_word(p+4068,T_INPUT|SWZ_IDENT|EXEC_ALWAYS);
            put_word(p+4080,OPC(0x01)|OUTMASK_ALL|END);
            put_word(p+4084,T_CONST|SWZ_IDENT|EXEC_ALWAYS);
        }
        vp_record_cb(a.cb_slot,0,&a);
        fprintf(stderr,"FAIL invalid fragment snapshot accepted: %s\n",mode);
        return 1;
    }
    if(!strcmp(mode,"bank-integrity")) {
        memset(fragment_store,0xA5,sizeof(fragment_store));
        vp_record_cb(a.cb_slot,0,&a);
        const float* bank=(const float*)(fragment_store+
            ((u64)MAX_DRAWS+a.cb_slot)*VP_FPCB_STRIDE);
        for(unsigned unit=0;unit<16;++unit) {
            CHECK(bank[unit*4]==(unit?1.0f:1.0f/64.0f));
            CHECK(bank[unit*4+1]==(unit?1.0f:1.0f/32.0f));
            CHECK(bank[unit*4+2]==0.0f && bank[unit*4+3]==0.0f);
        }
        CHECK(bank[64]==1.0f && bank[65]==128.0f/255.0f && bank[66]==4.0f && bank[67]==0.0f);
        CHECK(bank[68]==0.5f && bank[69]==0.25f && bank[70]==0.0f && bank[71]==1.0f);
        const unsigned char* unused=(const unsigned char*)(bank+72);
        unsigned unused_bytes=127*16;
        if(unused_bytes>VP_FPCB_STRIDE-288)unused_bytes=VP_FPCB_STRIDE-288;
        for(unsigned i=0;i<unused_bytes;++i)CHECK(unused[i]==0);
        CHECK(VP_FPCB_STRIDE>=272+128*16);
        puts("PASS effective constants preserve scales/alpha and clear unused slots");return 0;
    }
    vp_record_cb(a.cb_slot,0,&a);
    ID3D12PipelineState* first=NULL;
    int deferred=!strcmp(mode,"deferred") || !strcmp(mode,"deferred-changed-code");
    int same=!strcmp(mode,"same-literal");
    if(!deferred)first=prepare(&a);
    if(!same)put_float(vm_base+0x100+32,0.75f);
    int changed_code=!strcmp(mode,"changed-code") || !strcmp(mode,"deferred-changed-code");
    if(changed_code)put_word(vm_base+0x100+16,OPC(0x02)|OUTMASK_ALL|(1u<<31)|END);
    vp_record_cb(b.cb_slot,0,&b);
    if(deferred)first=prepare(&a);
    ID3D12PipelineState* second=prepare(&b);
    CHECK(first && second);
    if(same)CHECK(first==second);
    if(changed_code) {
        CHECK(first!=second);
        CHECK(strcmp(((ProbePipeline*)first)->source,((ProbePipeline*)second)->source));
    }
    /* Reverse binding order so an accidental shared/current CB cannot pass. */
    CHECK(effective_constant(second,&b,same?0.5f:0.75f)==0);
    CHECK(effective_constant(first,&a,0.5f)==0);
    if(!strcmp(mode,"record-snapshots")) {
        const float* va=(const float*)(fragment_store+
            ((u64)MAX_DRAWS+a.cb_slot)*VP_FPCB_STRIDE+272);
        const float* vb=(const float*)(fragment_store+
            ((u64)MAX_DRAWS+b.cb_slot)*VP_FPCB_STRIDE+272);
        CHECK(va[0]==0.5f && vb[0]==0.75f && va!=vb);
    }
    printf("PASS effective constants mode=%s compiles=%u psos=%u same_pso=%d\n",
        mode,compiles,creates,first==second);
    for(unsigned i=0;i<creates;++i)pipeline_release(&pipelines[i].api);
    s_d3d.vp_vs[0].vs->lpVtbl->Release(s_d3d.vp_vs[0].vs);
    return 0;
}
