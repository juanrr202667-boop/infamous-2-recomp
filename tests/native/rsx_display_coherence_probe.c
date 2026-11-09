/* Real planner and pending replay; no GPU or model of their decisions. */
#define CINTERFACE
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d12.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rsx_linear_metadata.h"
#include "rsx_linear_catalog.h"

#define MAX_DRAWS 4
#define MAX_OFF_RTS 8
/* BACKEND_TYPES */

static struct {
    ID3D12GraphicsCommandList* cmd_list;
    D3D12DrawRecord draws[MAX_DRAWS];
    OffRT off_rt[MAX_OFF_RTS];
    u32 draw_count;
} s_d3d;
static rsx_linear_version s_off_rt_views[MAX_OFF_RTS];
static rsx_linear_version s_pending_rt_views[MAX_OFF_RTS];
static u32 s_off_rt_count;
static u64 s_off_rt_epoch, s_pending_rt_epoch;
static int s_temporal_pending;
static ID3D12Resource resources[MAX_OFF_RTS];

static int off_rt_get_view(const rsx_linear_view* view)
{
    u32 slot;
    if (rsx_linear_reserve_view(s_off_rt_views, MAX_OFF_RTS,
            &s_off_rt_count, view, &slot) != RSX_LINEAR_OK) return -1;
    s_d3d.off_rt[slot].res = &resources[slot];
    s_d3d.off_rt[slot].dxgi = DXGI_FORMAT_R8G8B8A8_UNORM;
    s_d3d.off_rt[slot].off = (u32)view->offset;
    s_d3d.off_rt[slot].w = view->width;
    s_d3d.off_rt[slot].h = view->height;
    s_d3d.off_rt[slot].st = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
    return (int)slot;
}

#include "rsx_d3d12_temporal_plan.h"

/* BACKEND_TRANSITION */

static void temporal_abort(u32 op, int unit, const char* message)
{
    fprintf(stderr, "unexpected abort op=%u unit=%d: %s\n", op, unit, message);
    exit(3);
}

/* These cases exercise after_op publication only. Feedback is independently
 * tested; no fixture behavior substitutes for a display resource bridge. */
static void temporal_snapshot_feedback(u32 draw)
{
    if (s_rt_plan[draw].feedback_mask)
        temporal_abort(draw, -1, "unexpected feedback in publication fixture");
}

/* Publication test boundary: pixels are covered by the real backend wrapper
 * and GPU bridge fixtures. Here a completed physical display write is the
 * caller precondition, and publication must still follow its completion. */
static unsigned display_completions;
static void temporal_display_before(u32 draw) { (void)draw; }
static void temporal_display_after(u32 draw) {
    if(!s_rt_plan[draw].display_target) return;
    if(s_pending_rt_epoch!=1) temporal_abort(draw,-1,"display epoch published before completion");
    ++display_completions;
}
#include "rsx_d3d12_temporal_replay.h"

#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s (%s)\n", __LINE__, #c, s_temporal_error ? s_temporal_error : ""); return 1; } } while (0)

static void surface(u32 draw, u32 recorded_offset, u32 declared_offset)
{
    D3D12DrawRecord* record = &s_d3d.draws[draw];
    record->is_vp = 1;
    record->vertex_count = 3;
    record->rt_off = recorded_offset;
    record->rt_fmt = 0x148;
    record->rt_w = record->rt_h = 4;
    record->surface_meta.valid = 1;
    record->surface_meta.color_target = 1;
    record->surface_meta.color_offset[0] = declared_offset;
    record->surface_meta.color_pitch[0] = 16;
    record->surface_meta.color_dma[0] = 0xFEED0000;
    record->surface_meta.dma_seen = 1;
    record->surface_meta.clip_w = record->surface_meta.scissor_w = 4;
    record->surface_meta.clip_h = record->surface_meta.scissor_h = 4;
}

static void texture(u32 draw, u32 unit, u32 offset, u32 height)
{
    D3D12DrawRecord* record = &s_d3d.draws[draw];
    record->tex[unit].set = 1;
    record->tex[unit].raw = offset;
    record->tex[unit].w = 4;
    record->tex[unit].h = height;
    record->tex[unit].format_word = 0x0001A529;
    record->tex[unit].ctrl3 = 0x00100010;
}

static void mrt_then_display_then_read(u32 offset, int selector_b)
{
    s_d3d.draw_count = 3;
    surface(0, 0xAB8000, 0xAB8000);
    s_d3d.draws[0].rt_mrt[0] = offset;
    s_d3d.draws[0].surface_meta.color_target = 0x13;
    s_d3d.draws[0].surface_meta.color_offset[1] = offset;
    s_d3d.draws[0].surface_meta.color_pitch[1] = 16;
    s_d3d.draws[0].surface_meta.color_dma[1] = 0xFEED0000;
    s_d3d.draws[0].surface_meta.dma_seen = 3;
    /* rt_off is a presentation sentinel here; the immutable metadata retains
     * the real guest view, including a legal guest offset of zero. */
    surface(1, 0, offset);
    if (selector_b) {
        s_d3d.draws[1].surface_meta.color_target = 2;
        s_d3d.draws[1].surface_meta.color_offset[0] = 0x7C0000;
        s_d3d.draws[1].surface_meta.color_offset[1] = offset;
        s_d3d.draws[1].surface_meta.color_pitch[1] = 16;
        s_d3d.draws[1].surface_meta.color_dma[1] = 0xFEED0000;
        s_d3d.draws[1].surface_meta.dma_seen = 3;
    }
    surface(2, 0xC00000, 0xC00000);
    texture(2, 2, offset, 4);
}

static int check_identity(u32 offset)
{
    CHECK(temporal_prepare_targets());
    CHECK(temporal_prepare_textures(2, 1u << 2));
    const int previous_mrt = s_rt_plan[0].target[1];
    const int display = s_rt_plan[1].target[0];
    const int read = s_d3d.draws[2].tex_rt[2];
    printf("identities: previous_mrt=%d display=%d later_read=%d guest_offset=0x%X\n",
           previous_mrt, display, read, offset);
    CHECK(previous_mrt >= 0);
    CHECK(display == previous_mrt);
    CHECK(read == previous_mrt);
    CHECK(s_off_rt_views[read].view.offset == offset);
    CHECK(s_off_rt_views[read].view.location == 1 && s_off_rt_views[read].view.pitch == 16);
    return 0;
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    if (!strcmp(argv[1], "inactive-members")) {
        s_d3d.draw_count = 1;
        surface(0, 0x3B0000, 0x3B0000);
        CHECK(temporal_prepare_targets());
        CHECK(s_off_rt_count == 1 && s_rt_plan[0].target[0] >= 0);
        CHECK(s_rt_plan[0].target[1] == -1 && s_rt_plan[0].target[2] == -1 &&
              s_rt_plan[0].target[3] == -1);
    } else if (!strcmp(argv[1], "display-identity")) {
        mrt_then_display_then_read(0x3B0000, 0);
        CHECK(!check_identity(0x3B0000));
    } else if (!strcmp(argv[1], "selector-b")) {
        mrt_then_display_then_read(0x3B0000, 1);
        CHECK(!check_identity(0x3B0000));
    } else if (!strcmp(argv[1], "guest-zero")) {
        mrt_then_display_then_read(0, 1);
        CHECK(!check_identity(0));
    } else if (!strcmp(argv[1], "partial-alias")) {
        mrt_then_display_then_read(0x3B0000, 0);
        texture(1, 2, 0x3B0000, 2);
        CHECK(temporal_prepare_targets());
        const int accepted = temporal_prepare_textures(1, 1u << 2);
        printf("partial display feedback: accepted=%d display=%d read=%d\n",
               accepted, s_rt_plan[1].target[0], s_d3d.draws[1].tex_rt[2]);
        CHECK(!accepted);
        CHECK(s_temporal_error && strstr(s_temporal_error, "feedback"));
    } else if (!strcmp(argv[1], "display-epoch")) {
        mrt_then_display_then_read(0x3B0000, 0);
        CHECK(temporal_prepare_targets());
        CHECK(temporal_prepare_textures(2, 1u << 2));
        const int read = s_d3d.draws[2].tex_rt[2];
        CHECK(read >= 0 && read == s_rt_plan[0].target[1]);
        memcpy(s_pending_rt_views, s_off_rt_views, sizeof(s_pending_rt_views));
        s_temporal_pending = 1;
        temporal_after_op(0);
        CHECK(s_pending_rt_epoch == 1 && s_pending_rt_views[read].complete_at == 1);
        temporal_after_op(1);
        printf("after display write: global_epoch=%llu read_view_epoch=%llu display=%d read=%d\n",
               (unsigned long long)s_pending_rt_epoch,
               (unsigned long long)s_pending_rt_views[read].complete_at,
               s_rt_plan[1].target[0], read);
        CHECK(s_pending_rt_epoch == 2 && display_completions == 1);
        CHECK(s_pending_rt_views[read].complete_at == 2);
        CHECK(!s_off_rt_views[read].complete_at && !s_off_rt_epoch);
        temporal_commit();
        CHECK(s_off_rt_epoch == 2 && s_off_rt_views[read].complete_at == 2);
    } else return 2;
    puts("PASS display temporal coherence");
    return 0;
}
