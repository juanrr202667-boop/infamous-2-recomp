/* CPU fixture: production types, planner, replay, transitions and copy adapter.
 * Fake COM commands apply copies to a pixel store and validate tracked states.
 * Expected pixel values below are literals derived from the fixture addresses.
 */
#define CINTERFACE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d12.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rsx_linear_metadata.h"
#include "rsx_linear_catalog.h"
#include "rsx_d3d12_linear_copy.h"
#include "rsx_d3d12_feedback.h"
#include "rsx_d3d12_display_bridge.h"

#define MAX_DRAWS 8
#define MAX_OFF_RTS 8
/* BACKEND_TYPES */
/* BACKEND_PRESENT_CAPTURE */

static struct {
    ID3D12GraphicsCommandList* cmd_list;
    OffRT off_rt[MAX_OFF_RTS];
    D3D12DrawRecord draws[MAX_DRAWS];
    u32 draw_count;
} s_d3d;
static rsx_linear_version s_off_rt_views[MAX_OFF_RTS];
static rsx_linear_version s_pending_rt_views[MAX_OFF_RTS];
static u32 s_off_rt_count;
static u64 s_off_rt_epoch, s_pending_rt_epoch;
static int s_temporal_pending;
static int s_had_direct_backbuffer;
static rsx_d3d12_feedback_context s_feedback;
static rsx_d3d12_display_bridge_context s_display_bridge;

static int off_rt_get_view(const rsx_linear_view* view)
{
    u32 slot;
    return rsx_linear_reserve_view(s_off_rt_views, MAX_OFF_RTS,
        &s_off_rt_count, view, &slot) == RSX_LINEAR_OK ? (int)slot : -1;
}
#include "rsx_d3d12_temporal_plan.h"

/* BACKEND_TRANSITION */

static jmp_buf abort_jump;
static int allow_abort, abort_count;
static const char* abort_message;
static void temporal_abort(u32 op, int unit, const char* message)
{
    (void)op; (void)unit;
    ++abort_count;
    abort_message = message;
    if (allow_abort) longjmp(abort_jump, 1);
    fprintf(stderr, "unexpected temporal abort: %s\n", message ? message : "unknown");
    exit(3);
}

/* BACKEND_FEEDBACK */
/* BACKEND_DISPLAY */

#if __has_include("rsx_d3d12_temporal_replay.h")
#include "rsx_d3d12_temporal_replay.h"
static D3D12_CPU_DESCRIPTOR_HANDLE off_rt_rtv(int slot);
/* BACKEND_CLEAR_ONLY */

#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
static ID3D12Resource resources[MAX_OFF_RTS * 2 + 1];
static uint32_t pixels[MAX_OFF_RTS * 2 + 1][64];
static D3D12_RESOURCE_STATES command_states[MAX_OFF_RTS * 2 + 1];
static unsigned command_count, copy_count, feedback_copies, display_loads, display_writebacks, clear_calls, bad_commands;

static int resource_slot(ID3D12Resource* resource)
{
    for (u32 i = 0; i < MAX_OFF_RTS * 2 + 1; ++i)
        if (resource == &resources[i]) return (int)i;
    return -1;
}

static void STDMETHODCALLTYPE record_barrier(ID3D12GraphicsCommandList* command,
    UINT count, const D3D12_RESOURCE_BARRIER* barriers)
{
    ++command_count;
    if (command != s_d3d.cmd_list) ++bad_commands;
    for (UINT i = 0; i < count; ++i) {
        const D3D12_RESOURCE_BARRIER* b = &barriers[i];
        int slot = resource_slot(b->Transition.pResource);
        if (b->Type != D3D12_RESOURCE_BARRIER_TYPE_TRANSITION || slot < 0 ||
            b->Transition.Subresource != D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES) {
            ++bad_commands;
            continue;
        }
        if (command_states[slot] != b->Transition.StateBefore) ++bad_commands;
        command_states[slot] = b->Transition.StateAfter;
    }
}

static void STDMETHODCALLTYPE record_copy(ID3D12GraphicsCommandList* command,
    const D3D12_TEXTURE_COPY_LOCATION* destination, UINT x, UINT y, UINT z,
    const D3D12_TEXTURE_COPY_LOCATION* source, const D3D12_BOX* box)
{
    ++command_count; ++copy_count;
    int src = resource_slot(source->pResource);
    int dst = resource_slot(destination->pResource);
    if (command != s_d3d.cmd_list || src < 0 || dst < 0 || !box ||
        source->Type != D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX || source->SubresourceIndex ||
        destination->Type != D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX || destination->SubresourceIndex ||
        z || box->front || box->back != 1 || box->bottom != box->top + 1 ||
        box->right <= box->left || box->right > s_d3d.off_rt[src].w ||
        box->bottom > s_d3d.off_rt[src].h || x + box->right - box->left > s_d3d.off_rt[dst].w ||
        y >= s_d3d.off_rt[dst].h ||
        command_states[src] != D3D12_RESOURCE_STATE_COPY_SOURCE ||
        command_states[dst] != D3D12_RESOURCE_STATE_COPY_DEST) {
        ++bad_commands;
        return;
    }
    for (UINT column = 0; column < box->right - box->left; ++column)
        pixels[dst][y * s_d3d.off_rt[dst].w + x + column] =
            pixels[src][box->top * s_d3d.off_rt[src].w + box->left + column];
}

static void STDMETHODCALLTYPE record_feedback_copy(ID3D12GraphicsCommandList* command,
        ID3D12Resource* destination, ID3D12Resource* source)
{
    ++command_count;
    int src=resource_slot(source), dst=resource_slot(destination);
    int canonical=-1;
    if(src>=0 && src<MAX_OFF_RTS && dst>=MAX_OFF_RTS && dst<MAX_OFF_RTS*2) {
        ++feedback_copies; canonical=src;
    } else if(src>=0 && src<MAX_OFF_RTS && dst==MAX_OFF_RTS*2) {
        ++display_loads; canonical=src;
    } else if(src==MAX_OFF_RTS*2 && dst>=0 && dst<MAX_OFF_RTS) {
        ++display_writebacks; canonical=dst;
    }
    if(command!=s_d3d.cmd_list || canonical<0 ||
            command_states[src]!=D3D12_RESOURCE_STATE_COPY_SOURCE ||
            command_states[dst]!=D3D12_RESOURCE_STATE_COPY_DEST) { ++bad_commands; return; }
    memcpy(pixels[dst],pixels[src],s_d3d.off_rt[canonical].w*s_d3d.off_rt[canonical].h*sizeof(uint32_t));
}
static D3D12_RESOURCE_DESC* STDMETHODCALLTYPE resource_desc(ID3D12Resource* resource,
        D3D12_RESOURCE_DESC* desc)
{
    int slot=resource_slot(resource);
    memset(desc,0,sizeof(*desc));
    if(slot==MAX_OFF_RTS*2) {
        desc->Dimension=D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        desc->Width=desc->Height=4; desc->DepthOrArraySize=desc->MipLevels=1;
        desc->SampleDesc.Count=1; desc->Format=DXGI_FORMAT_R8G8B8A8_UNORM;
        return desc;
    }
    if(slot<0 || slot>=MAX_OFF_RTS) { ++bad_commands; return desc; }
    desc->Dimension=D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    desc->Width=s_d3d.off_rt[slot].w; desc->Height=s_d3d.off_rt[slot].h;
    desc->DepthOrArraySize=desc->MipLevels=1;
    desc->SampleDesc.Count=1; desc->Format=DXGI_FORMAT_R8G8B8A8_UNORM;
    return desc;
}
static D3D12_CPU_DESCRIPTOR_HANDLE off_rt_rtv(int slot) {
    D3D12_CPU_DESCRIPTOR_HANDLE handle={(SIZE_T)(1000+slot)}; return handle;
}
static void STDMETHODCALLTYPE record_color_clear(ID3D12GraphicsCommandList* command,
        D3D12_CPU_DESCRIPTOR_HANDLE handle, const FLOAT color[4], UINT count, const D3D12_RECT* rect)
{
    ++clear_calls; ++command_count;
    int slot=handle.ptr==999 ? MAX_OFF_RTS*2 : (int)handle.ptr-1000;
    if(command!=s_d3d.cmd_list || slot<0 || slot>MAX_OFF_RTS*2 || count!=1 || !rect ||
            command_states[slot]!=D3D12_RESOURCE_STATE_RENDER_TARGET) { ++bad_commands; return; }
    uint32_t rgba=0;
    for(unsigned channel=0;channel<4;++channel) rgba|=(uint32_t)(color[channel]*255+0.5f)<<(channel*8);
    for(LONG y=rect->top;y<rect->bottom;++y)
        for(LONG x=rect->left;x<rect->right;++x) pixels[slot][y*4+x]=rgba;
}
static ID3D12ResourceVtbl resource_table;

static ID3D12GraphicsCommandListVtbl command_table;
static ID3D12GraphicsCommandList command;

static void reset(void)
{
    _putenv_s("RT_CLEARDBG", "");
    memset(&s_d3d, 0, sizeof(s_d3d));
    memset(s_off_rt_views, 0, sizeof(s_off_rt_views));
    memset(s_pending_rt_views, 0, sizeof(s_pending_rt_views));
    memset(&command_table, 0, sizeof(command_table));
    memset(&s_feedback,0,sizeof(s_feedback));
    memset(&s_display_bridge,0,sizeof(s_display_bridge));
    memset(&resource_table,0,sizeof(resource_table));
    resource_table.GetDesc=resource_desc;
    for(unsigned i=0;i<MAX_OFF_RTS*2+1;++i) resources[i].lpVtbl=&resource_table;
    command_table.ResourceBarrier = record_barrier;
    command_table.CopyTextureRegion = record_copy;
    command_table.CopyResource = record_feedback_copy;
    command_table.ClearRenderTargetView = record_color_clear;
    command.lpVtbl = &command_table;
    s_d3d.cmd_list = &command;
    s_feedback.command_list=&command;
    s_display_bridge.command_list=&command;
    s_display_bridge.display=&resources[MAX_OFF_RTS*2];
    s_display_bridge.width=s_display_bridge.height=4;
    s_display_bridge.format=DXGI_FORMAT_R8G8B8A8_UNORM;
    command_states[MAX_OFF_RTS*2]=D3D12_RESOURCE_STATE_RENDER_TARGET;
    s_d3d.draw_count = 3;
    s_off_rt_count = 4;
    s_off_rt_epoch = s_pending_rt_epoch = 0;
    s_temporal_pending = 0;
    command_count = copy_count = feedback_copies = display_loads = display_writebacks = clear_calls = bad_commands = 0;
    abort_count = allow_abort = 0;
    abort_message = NULL;
    for (u32 i = 0; i < MAX_DRAWS; ++i) {
        for (u32 member = 0; member < 4; ++member) s_rt_plan[i].target[member] = -1;
        s_rt_plan[i].texture_mask = 0;
        s_rt_plan[i].feedback_mask = 0;
        s_rt_plan[i].display_target = 0;
        for (u32 unit = 0; unit < 16; ++unit) s_d3d.draws[i].tex_rt[unit] = -1;
    }
    for (u32 i = 0; i < s_off_rt_count; ++i) {
        rsx_linear_view view = {0xAB8000, 4, 4, 16, 4, 0x85, 1};
        if (i == 1) view.height = 1; /* A/B keep the 720/180 height ratio. */
        if (i == 2) { view.offset += 4; view.width = 2; view.height = 3; view.pitch = 8; }
        if (i == 3) view.offset = 0xE3C000; /* Independent draw destination. */
        s_off_rt_views[i].view = view;
        s_d3d.off_rt[i].res = &resources[i];
        s_d3d.off_rt[i].off = (u32)view.offset;
        s_d3d.off_rt[i].w = view.width;
        s_d3d.off_rt[i].h = view.height;
        s_d3d.off_rt[i].dxgi = DXGI_FORMAT_R8G8B8A8_UNORM;
        s_d3d.off_rt[i].st = command_states[i] = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
        for (u32 p = 0; p < 64; ++p) pixels[i][p] = 0xDEADBEEF;
    }
}

static void begin_batch(void)
{
    memcpy(s_pending_rt_views, s_off_rt_views, sizeof(s_off_rt_views));
    s_pending_rt_epoch = s_off_rt_epoch;
    s_temporal_pending = 1;
    for (u32 i = 0; i < s_off_rt_count; ++i) s_d3d.off_rt[i].used = 0;
}

static void write_producer(void)
{
    s_rt_plan[0].target[0] = 0;
    temporal_before_op(0);
    for (u32 p = 0; p < 16; ++p) pixels[0][p] = 0xAABBCCDD;
    temporal_after_op(0);
}

/* Mutations caught: skipping target materialization, publishing epochs during
 * recording, missing read materialization, or leaving copy states bound. */
static int pending_and_partial(void)
{
    begin_batch();
    write_producer();
    CHECK(s_pending_rt_epoch == 1 && s_pending_rt_views[0].complete_at == 1);
    CHECK(s_off_rt_epoch == 0 && s_off_rt_views[0].complete_at == 0);
    s_rt_plan[1].target[0] = 1;
    temporal_before_op(1);
    CHECK(pixels[1][0] == 0xAABBCCDD && pixels[1][3] == 0xAABBCCDD);
    CHECK(s_d3d.off_rt[1].st == D3D12_RESOURCE_STATE_RENDER_TARGET);
    pixels[1][0] = (pixels[1][0] & 0xFFFFFF00) | 0x11;
    pixels[1][1] = (pixels[1][1] & 0xFFFFFF00) | 0x11;
    temporal_after_op(1);
    CHECK(s_pending_rt_epoch == 2 && s_pending_rt_views[1].complete_at == 2);
    CHECK(s_off_rt_views[1].complete_at == 0 && s_off_rt_epoch == 0);
    s_rt_plan[2].target[0] = 3;
    s_rt_plan[2].texture_mask = 1u << 5;
    s_d3d.draws[2].tex_rt[5] = 0;
    /* An unused stale binding must not become an active source. */
    s_d3d.draws[2].tex_rt[4] = MAX_OFF_RTS + 1;
    temporal_before_op(2);
    CHECK(pixels[0][0] == 0xAABBCC11 && pixels[0][1] == 0xAABBCC11);
    CHECK(pixels[0][2] == 0xAABBCCDD && pixels[0][15] == 0xAABBCCDD);
    CHECK(s_d3d.off_rt[0].st == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    CHECK(s_d3d.off_rt[3].st == D3D12_RESOURCE_STATE_RENDER_TARGET);
    CHECK(s_off_rt_views[0].complete_at == 0 && s_off_rt_views[1].complete_at == 0);
    temporal_after_op(2);
    temporal_commit();
    CHECK(s_off_rt_epoch == 3 && !s_temporal_pending);
    CHECK(s_off_rt_views[0].complete_at == 2 && s_off_rt_views[1].complete_at == 2);
    CHECK(s_off_rt_views[3].complete_at == 3 && !bad_commands && copy_count > 0);
    return 0;
}

/* Mutations caught: treating resource rows as contiguous guest rows, ignoring
 * an interior offset, or replacing untouched source pixels with the small view. */
static int offset_and_pitch(void)
{
    begin_batch();
    write_producer();
    s_rt_plan[1].target[0] = 2;
    temporal_before_op(1);
    CHECK(pixels[2][0] == 0xAABBCCDD && pixels[2][5] == 0xAABBCCDD);
    for (u32 p = 0; p < 6; ++p) pixels[2][p] = (pixels[2][p] & 0xFFFF00FF) | 0x2200;
    temporal_after_op(1);
    s_rt_plan[2].target[0] = 3;
    s_rt_plan[2].texture_mask = 1;
    s_d3d.draws[2].tex_rt[0] = 0;
    temporal_before_op(2);
    CHECK(pixels[0][0] == 0xAABBCCDD);
    CHECK(pixels[0][1] == 0xAABB22DD && pixels[0][3] == 0xAABB22DD);
    CHECK(pixels[0][4] == 0xAABB22DD && pixels[0][6] == 0xAABB22DD);
    CHECK(pixels[0][7] == 0xAABBCCDD && pixels[0][15] == 0xAABBCCDD);
    CHECK(!bad_commands);
    return 0;
}

/* Mutations caught: clearing persistent producer epochs between batches or
 * promoting an entire large view from the newest small producer alone. */
static int cross_batch(void)
{
    begin_batch();
    write_producer();
    temporal_commit();
    CHECK(s_off_rt_epoch == 1 && s_off_rt_views[0].complete_at == 1);
    begin_batch();
    s_rt_plan[0].target[0] = 1;
    temporal_before_op(0);
    CHECK(pixels[1][0] == 0xAABBCCDD && pixels[1][3] == 0xAABBCCDD);
    pixels[1][1] = (pixels[1][1] & 0xFFFFFF00) | 0x11;
    temporal_after_op(0);
    CHECK(s_off_rt_views[1].complete_at == 0 && s_off_rt_epoch == 1);
    temporal_commit();
    CHECK(s_off_rt_epoch == 2 && s_off_rt_views[0].complete_at == 1);
    CHECK(s_off_rt_views[1].complete_at == 2 && s_off_rt_count == 4);
    begin_batch();
    s_rt_plan[0].target[0] = 3;
    s_rt_plan[0].texture_mask = 1;
    s_d3d.draws[0].tex_rt[0] = 0;
    temporal_before_op(0);
    CHECK(pixels[0][0] == 0xAABBCCDD && pixels[0][1] == 0xAABBCC11);
    CHECK(pixels[0][4] == 0xAABBCCDD && pixels[0][15] == 0xAABBCCDD);
    CHECK(s_d3d.off_rt[0].res == &resources[0] && !bad_commands);
    return 0;
}

/* Mutations caught: ignoring copy adapter rejection or committing the pending
 * producer before the command list has been accepted by the caller. */
static int copy_failure(void)
{
    rsx_linear_version before[MAX_OFF_RTS];
    memcpy(before, s_off_rt_views, sizeof(before));
    begin_batch();
    write_producer();
    s_rt_plan[1].target[0] = 1;
    s_d3d.off_rt[1].dxgi = DXGI_FORMAT_R32_FLOAT;
    allow_abort = 1;
    if (setjmp(abort_jump) == 0) {
        temporal_before_op(1);
        CHECK(0 && "unsupported copy must abort replay");
    }
    allow_abort = 0;
    CHECK(abort_count == 1 && abort_message);
    CHECK(s_off_rt_epoch == 0 && !memcmp(before, s_off_rt_views, sizeof(before)));
    CHECK(!bad_commands);
    return 0;
}

/* Mutations caught: emitting a partial plan before validating the catalog or
 * advancing a watermark after rsx_linear_plan_materialize rejects geometry. */
static int plan_failure(void)
{
    rsx_linear_version before[MAX_OFF_RTS];
    s_off_rt_epoch = s_off_rt_views[0].complete_at = 1;
    memcpy(before, s_off_rt_views, sizeof(before));
    begin_batch();
    s_pending_rt_views[2].view.pitch = 3; /* Invalid geometry in the pending catalog. */
    s_rt_plan[0].target[0] = 1;
    allow_abort = 1;
    if (setjmp(abort_jump) == 0) {
        temporal_before_op(0);
        CHECK(0 && "invalid catalog must abort replay");
    }
    allow_abort = 0;
    CHECK(abort_count == 1 && abort_message);
    CHECK(!command_count && !copy_count && !bad_commands);
    CHECK(s_off_rt_epoch == 1 && !memcmp(before, s_off_rt_views, sizeof(before)));
    return 0;
}

/* A newer partial producer must be reconciled before the snapshot. Both
 * units use one snapshot per draw; the next draw must refresh its pixels. */
static int feedback_refresh(void)
{
    s_off_rt_epoch=s_off_rt_views[1].complete_at=1;
    pixels[1][0]=10; pixels[1][1]=20;
    rsx_d3d12_feedback_slot* snapshot=&s_feedback.slots[0];
    snapshot->resource=&resources[MAX_OFF_RTS]; snapshot->source=&resources[0];
    snapshot->width=snapshot->height=4; snapshot->format=DXGI_FORMAT_R8G8B8A8_UNORM;
    snapshot->state=command_states[MAX_OFF_RTS]=D3D12_RESOURCE_STATE_COPY_DEST;
    begin_batch();
    for(u32 draw=0;draw<2;++draw) {
        s_rt_plan[draw].target[0]=0;
        s_rt_plan[draw].texture_mask=s_rt_plan[draw].feedback_mask=(1u<<2)|(1u<<15);
        s_d3d.draws[draw].tex_rt[2]=s_d3d.draws[draw].tex_rt[15]=0;
        temporal_before_op(draw);
        CHECK(feedback_copies==draw+1);
        CHECK(pixels[MAX_OFF_RTS][0]==10+draw && pixels[MAX_OFF_RTS][1]==20+draw);
        CHECK(snapshot->state==D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
        CHECK(s_d3d.off_rt[0].st==D3D12_RESOURCE_STATE_RENDER_TARGET && !bad_commands);
        pixels[0][0]=pixels[MAX_OFF_RTS][0]+1; pixels[0][1]=pixels[MAX_OFF_RTS][1]+1;
        temporal_after_op(draw);
    }
    CHECK(pixels[0][0]==12 && pixels[0][1]==22);
    CHECK(s_off_rt_epoch==1 && s_off_rt_views[0].complete_at==0);
    CHECK(s_pending_rt_epoch==3 && s_pending_rt_views[0].complete_at==3);
    CHECK(s_pending_rt_views[1].complete_at==1 && s_off_rt_count==4 && !bad_commands);
    temporal_commit();
    CHECK(s_off_rt_epoch==3 && s_off_rt_views[0].complete_at==3);
    return 0;
}
static int feedback_invalid(int mode)
{
    begin_batch();
    s_rt_plan[0].feedback_mask=1u<<2; /* Outside the compiled texture mask. */
    if(mode==1) {
        s_rt_plan[0].texture_mask=1u<<2;
        s_rt_plan[0].target[0]=0;
        s_d3d.draws[0].tex_rt[2]=3; /* Feedback must refer to an active destination. */
    } else if(mode==2) {
        s_rt_plan[0].feedback_mask=0;
        s_rt_plan[0].texture_mask=1u<<2;
        s_rt_plan[0].target[0]=s_d3d.draws[0].tex_rt[2]=0; /* Never bind active target directly. */
    }
    allow_abort=1;
    if(setjmp(abort_jump)==0) { temporal_before_op(0); CHECK(0 && "invalid feedback must abort"); }
    allow_abort=0;
    CHECK(abort_count==1 && !command_count && !feedback_copies && !bad_commands);
    CHECK(!s_off_rt_epoch && !s_pending_rt_epoch);
    return 0;
}

/* The actual backend callbacks must load after materialization, copy the
 * physical display write back, and only then promote the canonical epoch. */
static int display_bridge_roundtrip(void)
{
    s_off_rt_epoch=s_off_rt_views[1].complete_at=1;
    pixels[1][0]=0xFF584D0A; pixels[1][1]=0xFF584D14;
    pixels[MAX_OFF_RTS*2][0]=pixels[MAX_OFF_RTS*2][1]=0xFFFFFFFF;
    begin_batch();
    s_rt_plan[0].target[0]=0; s_rt_plan[0].display_target=1;
    temporal_before_op(0);
    CHECK(display_loads==1 && !display_writebacks);
    CHECK(pixels[MAX_OFF_RTS*2][0]==0xFF584D0A && pixels[MAX_OFF_RTS*2][1]==0xFF584D14);
    CHECK(command_states[MAX_OFF_RTS*2]==D3D12_RESOURCE_STATE_RENDER_TARGET);
    pixels[MAX_OFF_RTS*2][0]=(pixels[MAX_OFF_RTS*2][0]&0xFFFF00FF)|0x6400;
    pixels[MAX_OFF_RTS*2][1]=(pixels[MAX_OFF_RTS*2][1]&0xFFFF00FF)|0x6400;
    temporal_after_op(0);
    CHECK(display_writebacks==1 && pixels[0][0]==0xFF58640A && pixels[0][1]==0xFF586414);
    CHECK(s_pending_rt_epoch==2 && s_pending_rt_views[0].complete_at==2);
    CHECK(s_present_capture.direct_slot==0 && s_present_capture.direct_epoch==2);
    CHECK(s_off_rt_epoch==1 && s_off_rt_views[0].complete_at==0);
    s_rt_plan[1].target[0]=3; s_rt_plan[1].texture_mask=1u<<2;
    s_d3d.draws[1].tex_rt[2]=0;
    temporal_before_op(1);
    CHECK(pixels[0][0]==0xFF58640A && pixels[0][1]==0xFF586414);
    CHECK(s_d3d.off_rt[0].st==D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    CHECK(display_loads==1 && display_writebacks==1 && !bad_commands);
    temporal_after_op(1); temporal_commit();
    CHECK(s_off_rt_epoch==3 && s_off_rt_views[0].complete_at==2);
    return 0;
}

static int clear_without_pipeline(void)
{
    begin_batch();
    s_rt_plan[0].target[0]=0; s_rt_plan[0].display_target=1;
    D3D12DrawRecord* record=&s_d3d.draws[0];
    record->is_clear=record->is_vp=1;
    record->surface_meta.valid=1; record->surface_meta.clear_flags=0xF0;
    record->surface_meta.clip_w=record->surface_meta.clip_h=4;
    record->surface_meta.scissor_w=record->surface_meta.scissor_h=4;
    record->cc[0]=0.2f; record->cc[1]=0.4f; record->cc[2]=0.6f; record->cc[3]=1.0f;
    D3D12_CPU_DESCRIPTOR_HANDLE display={999};
    temporal_render_clear_only(0,display);
    CHECK(clear_calls==1 && display_loads==1 && display_writebacks==1 && !bad_commands);
    CHECK(pixels[0][0]==0xFF996633 && pixels[0][15]==0xFF996633);
    CHECK(s_pending_rt_epoch==1 && s_pending_rt_views[0].complete_at==1 && !s_off_rt_epoch);
    CHECK(s_present_capture.direct_slot==0 && s_present_capture.direct_epoch==1);
    return 0;
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    reset();
    int result;
    if (!strcmp(argv[1], "pending-and-partial")) result = pending_and_partial();
    else if (!strcmp(argv[1], "offset-and-pitch")) result = offset_and_pitch();
    else if (!strcmp(argv[1], "cross-batch")) result = cross_batch();
    else if (!strcmp(argv[1], "copy-failure")) result = copy_failure();
    else if (!strcmp(argv[1], "plan-failure")) result = plan_failure();
    else if (!strcmp(argv[1], "clear-no-pipeline")) result = clear_without_pipeline();
    else if (!strcmp(argv[1], "display-bridge")) result = display_bridge_roundtrip();
    else if (!strcmp(argv[1], "feedback-refresh")) result = feedback_refresh();
    else if (!strcmp(argv[1], "feedback-invalid")) result = feedback_invalid(0);
    else if (!strcmp(argv[1], "feedback-source-invalid")) result = feedback_invalid(1);
    else if (!strcmp(argv[1], "feedback-direct-invalid")) result = feedback_invalid(2);
    else return 2;
    if (!result) puts("PASS CPU D3D12 temporal replay");
    return result;
}
#else
int main(void)
{
    puts("FAIL D3D12 temporal replay API not implemented");
    return 1;
}
#endif
