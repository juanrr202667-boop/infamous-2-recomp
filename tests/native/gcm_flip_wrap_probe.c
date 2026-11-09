/* Synthetic command contexts, real GCM flip insertion and FIFO walker.
 * The break caught here is rewinding a title-owned context during flip
 * insertion, overwriting its next command and skipping a following release.
 */
#include "cellGcmSys.c"
#include "rsx_commands.c"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t memory[0x2000000];
uint8_t* vm_base = memory;
uint32_t ppu_vm_size = sizeof memory;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
ps3_guest_caller_fn g_ps3_guest_caller;
void (*g_spu_put_hook)(u32, u32);
PPU_THREAD_LOCAL ppu_context* g_active_ctx;
static u32 observed_label_ea;
static unsigned observed_label_writes;

/* No SPU or GPU is present. Guest writes and command decoding remain real.
 * External rendering/diagnostic hooks abort if unexpectedly reached; the
 * tested stream contains only a flip, NOPs, and an NV406E label release.
 */
void ppu_resv_break_store(uint64_t ea) { (void)ea; abort(); }
void ps3_ww_report_inline(uint32_t a, uint64_t v, int w) {
    /* Observe a real inline VM store; the write-watch callback does not write
     * the label or invoke a semaphore handler. Other diagnostic calls abort. */
    if (a != observed_label_ea || v != 56 || w != 4) abort();
    ++observed_label_writes;
}
int spu_coh_is_reserved(uint32_t a) { (void)a; return 0; }
void spu_coh_notify_write(uint32_t a) { (void)a; abort(); }
void spu_lockline_lock(void) { abort(); }
void spu_lockline_unlock(void) { abort(); }
void ppu_dump_guest_stack(ppu_context* p, const char* text) { (void)p; (void)text; abort(); }
void ppu_dump_bctrl_ring(uint32_t tid, const char* text) { (void)tid; (void)text; abort(); }
void rsx_raise_user_cmd(u32 argument) { (void)argument; abort(); }
void ps3_ms(const char* key) { (void)key; }
int rsx_live_draw_enabled(void) { return 0; }
int rsx_draw_engine_enabled(void) { return 0; }
void rsx_live_draw_method(u32 method, u32 arg) { (void)method; (void)arg; abort(); }
void rsx_draw_engine_method(u32 method, u32 arg) { (void)method; (void)arg; abort(); }
void rsx_live_draw_set_display_buffer(u32 id, u32 loc, u32 off, u32 pitch, u32 w, u32 h) {
    (void)id; (void)loc; (void)off; (void)pitch; (void)w; (void)h; abort();
}
void rsx_draw_engine_set_display_buffer(u32 id, u32 loc, u32 off, u32 pitch, u32 w, u32 h) {
    (void)id; (void)loc; (void)off; (void)pitch; (void)w; (void)h; abort();
}
int rsx_live_draw_blit(u32 sl, u32 sa, u32 sp, u32 dl, u32 da, u32 dp, u32 w, u32 h) {
    (void)sl; (void)sa; (void)sp; (void)dl; (void)da; (void)dp; (void)w; (void)h; abort();
}
int rsx_live_draw_resolve_blit(u32 sl, u32 sa, u32 sp, u32 ds, u32 dt,
                              u32 dl, u32 da, u32 dp, u32 x, u32 y, u32 w, u32 h) {
    (void)sl; (void)sa; (void)sp; (void)ds; (void)dt; (void)dl; (void)da; (void)dp;
    (void)x; (void)y; (void)w; (void)h; abort();
}
void rsx_live_draw_note_resolve(u32 sl, u32 sa, u32 dl, u32 da) {
    (void)sl; (void)sa; (void)dl; (void)da; abort();
}

enum {
    CONTEXT_OUT = 0x800, CONTEXT_EA = 0x1000, CALLBACK_EA = 0x1010,
    BUFFER_BEGIN = 0x100000, NEAR_CURRENT = 0x12E000,
    TITLE_CALLBACK = 0x24680, LABEL_OFFSET = 0x100,
};
static unsigned allocations;
static u32 fixture_alloc(u32 size, u32 alignment) {
    if (allocations++ == 0 && size == 16 && alignment == 16) return CONTEXT_EA;
    if (allocations == 2 && size == 8 && alignment == 8) return CALLBACK_EA;
    fprintf(stderr, "gcm-flip-wrap ERROR: unexpected setup allocation\n");
    exit(2);
}
static int check(int condition, const char* message) {
    if (!condition) fprintf(stderr, "gcm-flip-wrap FAIL: %s\n", message);
    return condition;
}

static int setup(u32 size, int title_callback) {
    ppu_hle_inject_base = 0x300000;
    const u32 ctx = cellGcmSetupContext(CONTEXT_OUT, size, 0x100000,
        BUFFER_BEGIN, fixture_alloc, vm_write32);
    if (!check(ctx == CONTEXT_EA && vm_read32(ctx + 0xC) == CALLBACK_EA &&
               vm_read32(CALLBACK_EA) == GCM_FIFO_CALLBACK_SENTINEL_EA,
               "real setup must install the runtime callback descriptor")) return 0;
    if (title_callback) vm_write32(CALLBACK_EA, TITLE_CALLBACK);
    if (cellGcmSetDisplayBuffer(1, 0, 64, 16, 16) != CELL_OK) return 0;
    vm_write32(ctx + 8, NEAR_CURRENT);
    // A literal parked head bounds the bad-wrap path in the real walker.
    vm_write32(BUFFER_BEGIN, 0x20000000);
    s_fifo_getoff = 0x28000;
    return 1;
}

static void stage_release_tail(void) {
    // NV406E OFFSET and RELEASE, independently encoded (one arg each).
    vm_write32(NEAR_CURRENT + 4, 0x00040064);
    vm_write32(NEAR_CURRENT + 8, LABEL_OFFSET);
    vm_write32(NEAR_CURRENT + 12, 0x0004006C);
    vm_write32(NEAR_CURRENT + 16, 0x12345678);
}

static int runtime_ring(void) {
    if (!setup(0x30000, 0)) return 2;
    stage_release_tail();
    int ok = check(gcm_flip_into_fifo(CONTEXT_EA, 1) == 1, "runtime-ring flip must insert");
    ok &= check(vm_read32(NEAR_CURRENT) == 0xFEAD0101, "runtime-ring marker must retain buffer id");
    ok &= check(vm_read32(NEAR_CURRENT + 4) == 0x20000000, "runtime-owned ring must append JUMP to its mapped begin");
    ok &= check(vm_read32(CONTEXT_EA + 8) == BUFFER_BEGIN, "runtime-owned ring must rewind current");
    ok &= check(vm_read32(CONTEXT_EA + 12) == CALLBACK_EA &&
                vm_read32(CALLBACK_EA) == GCM_FIFO_CALLBACK_SENTINEL_EA,
                "runtime-owned callback descriptor must survive wrapping");
    return ok ? 0 : 1;
}

static int drain_release_tail(void);

static int title_context(int ample_room, int missing_callback) {
    if (!setup(ample_room ? 0x80000 : 0x30000, 1)) return 2;
    if (missing_callback) vm_write32(CONTEXT_EA + 12, 0);
    stage_release_tail();
    int ok = check(gcm_flip_into_fifo(CONTEXT_EA, 1) == 1, "title-context flip must insert");
    ok &= check(vm_read32(NEAR_CURRENT) == 0xFEAD0101, "title-context marker must retain buffer id");
    ok &= check(vm_read32(CONTEXT_EA + 8) == NEAR_CURRENT + 4,
                "title-owned context must advance current past the marker");
    ok &= check(vm_read32(NEAR_CURRENT + 4) == 0x00040064 &&
                vm_read32(NEAR_CURRENT + 8) == LABEL_OFFSET &&
                vm_read32(NEAR_CURRENT + 12) == 0x0004006C &&
                vm_read32(NEAR_CURRENT + 16) == 0x12345678,
                "title-owned context must preserve the following release commands");
    ok &= check(vm_read32(CONTEXT_EA + 12) == (missing_callback ? 0 : CALLBACK_EA) &&
                vm_read32(CALLBACK_EA) == TITLE_CALLBACK,
                "title-owned callback descriptor must be preserved");
    fprintf(stderr, "gcm-flip-wrap observed: current=0x%08X next=0x%08X\n",
            vm_read32(CONTEXT_EA + 8), vm_read32(NEAR_CURRENT + 4));
    if (ample_room) ok &= drain_release_tail() == 0;
    return ok ? 0 : 1;
}

static int drain_release_tail(void) {
    // Publish the staged marker and tail. The real walker owns every write to
    // the label; the fixture does not call a release handler or set the result.
    vm_write32(GCM_CONTROL_GUEST_ADDR, 0x2E014);
    cellGcm_rsx_process_fifo();
    if (!check(vm_read32(ppu_hle_inject_base + LABEL_OFFSET) == 0,
               "release must wait until the flip marker has retired")) return 1;
    cellGcm_rsx_process_fifo();
    const u32 label = vm_read32(ppu_hle_inject_base + LABEL_OFFSET);
    fprintf(stderr, "gcm-flip-wrap observed: tail label=0x%08X get=0x%08X put=0x%08X\n",
            label, vm_read32(GCM_CONTROL_GUEST_ADDR + 4), vm_read32(GCM_CONTROL_GUEST_ADDR));
    return check(label == 0x12345678, "real FIFO must execute the release after a title-context flip") ? 0 : 1;
}

static int title_tail(void) {
    if (!setup(0x30000, 1)) return 2;
    stage_release_tail();
    if (!check(gcm_flip_into_fifo(CONTEXT_EA, 1) == 1, "tail fixture flip must insert")) return 1;
    return drain_release_tail();
}

static int small_slice(int null_callback, int exact_fit) {
    if (!setup(0x30000, 1)) return 2;
    const u32 current = BUFFER_BEGIN + 8;
    const u32 end = exact_fit ? current + 4 : BUFFER_BEGIN + 0xFFC;
    vm_write32(CONTEXT_EA + 4, end);
    vm_write32(CONTEXT_EA + 8, current);
    if (null_callback) vm_write32(CONTEXT_EA + 12, 0);
    // Hand-encoded OFFSET before the flip, RELEASE after it. The geometry
    // matches a title-managed 4 KB slice: current=begin+8, end=begin+0xFFC.
    vm_write32(BUFFER_BEGIN, 0x00040064);
    vm_write32(BUFFER_BEGIN + 4, LABEL_OFFSET);
    if (!exact_fit) {
        vm_write32(current + 4, 0x0004006C);
        vm_write32(current + 8, 0x12345678);
    }
    s_fifo_getoff = 0;
    int ok = check(_cellGcmSetFlipCommand((void*)(uintptr_t)CONTEXT_EA, 1) == CELL_OK,
                   "public flip wrapper must accept a mapped title slice");
    ok &= check(vm_read32(current) == 0xFEAD0101,
                "small title slice must contain the queued flip marker");
    ok &= check(vm_read32(CONTEXT_EA + 8) == current + 4,
                "small title slice must advance current by one marker");
    ok &= check(cellGcm_take_flip_pending() == 0 && cellGcm_flip_request_count() == 0,
                "public wrapper must not publish a present before FIFO retirement");
    if (!exact_fit) {
        ok &= check(vm_read32(current + 4) == 0x0004006C &&
                    vm_read32(current + 8) == 0x12345678,
                    "small-slice flip must preserve the staged release tail");
    }
    vm_write32(GCM_CONTROL_GUEST_ADDR, exact_fit ? 0xC : 0x14);
    cellGcm_rsx_process_fifo();
    ok &= check(cellGcm_take_flip_pending() == 1 && cellGcm_flip_request_count() == 1,
                "real FIFO must publish exactly one present at the marker");
    ok &= check(vm_read32(ppu_hle_inject_base + LABEL_OFFSET) == 0,
                "marker retirement must not publish a label release");
    if (!exact_fit) {
        ok &= check(vm_read32(GCM_CONTROL_GUEST_ADDR + 4) == 0xC,
                    "marker retirement must stop before the following release");
        cellGcm_rsx_process_fifo();
        ok &= check(vm_read32(ppu_hle_inject_base + LABEL_OFFSET) == 0x12345678 &&
                    cellGcm_take_flip_pending() == 0 && cellGcm_flip_request_count() == 1,
                    "next drain must release the label without another present");
    }
    // Exact-fit ends at the marker and has no tail. The walker's separate
    // full-ring recovery may recycle GET afterwards; that is outside this
    // insertion/publication contract. The pre-drain marker/current assertions
    // above still catch rejection, overflow, or premature publication.
    fprintf(stderr, "gcm-flip-wrap slice: callback=0x%08X end=0x%08X current=0x%08X "
                    "marker=0x%08X label=0x%08X presents=%u\n",
            vm_read32(CONTEXT_EA + 12), end, vm_read32(CONTEXT_EA + 8),
            vm_read32(current), vm_read32(ppu_hle_inject_base + LABEL_OFFSET),
            cellGcm_flip_request_count());
    return ok ? 0 : 1;
}

static int rejected(void) {
    if (!setup(0x30000, 1)) return 2;
    static uint8_t before[sizeof memory];
    const struct { u32 ctx, id, cur, end, entry; const char* name; } cases[] = {
        {0, 1, NEAR_CURRENT, 0x130000, TITLE_CALLBACK, "null context"},
        {CONTEXT_EA, 8, NEAR_CURRENT, 0x130000, TITLE_CALLBACK, "invalid display id"},
        {CONTEXT_EA, 2, NEAR_CURRENT, 0x130000, TITLE_CALLBACK, "unregistered display"},
        {CONTEXT_EA, 1, 0x210000, 0x230000, TITLE_CALLBACK, "unmapped command memory"},
        {CONTEXT_EA, 1, NEAR_CURRENT, NEAR_CURRENT, TITLE_CALLBACK, "no marker room"},
        {CONTEXT_EA, 1, NEAR_CURRENT, 0x12F000, GCM_FIFO_CALLBACK_SENTINEL_EA,
            "runtime-owned ring must retain its reserve policy"},
    };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
        vm_write32(CONTEXT_EA + 8, cases[i].cur);
        vm_write32(CONTEXT_EA + 4, cases[i].end);
        vm_write32(CALLBACK_EA, cases[i].entry);
        memcpy(before, memory, sizeof memory);
        if (gcm_flip_into_fifo(cases[i].ctx, cases[i].id) != 0 ||
            memcmp(before, memory, sizeof memory) != 0) {
            fprintf(stderr, "gcm-flip-wrap FAIL: %s must reject without any guest write\n", cases[i].name);
            return 1;
        }
    }
    return 0;
}

static int parked_jump(int put_at_park, int normal_jump, unsigned parked_drains) {
    if (!setup(0x80000, 1)) return 2;
    if (!check(cellGcmMapEaIoAddress(0x200000, 0x500000, 0x100000) == CELL_OK,
               "previous command buffer must have a real IO mapping")) return 2;
    enum {
        PARK_EA = 0x22DA50, PARK_IO = 0x52DA50,
        PACKET_EA = 0x235A94, PACKET_IO = 0x535A94, PUBLISHED_PUT = 0x1005C,
    };
    /* Hand-encoded title packet: NV4097 OFFSET 0x850, RELEASE 56, then the
     * closing JUMP back to the published ring head. Only the walker releases. */
    vm_write32(PACKET_EA, 0x00041D6C);
    vm_write32(PACKET_EA + 4, 0x850);
    vm_write32(PACKET_EA + 8, 0x00041D74);
    vm_write32(PACKET_EA + 12, 56);
    vm_write32(PACKET_EA + 16, 0x2001005C);
    vm_write32(PARK_EA, normal_jump ? 0x20535A94 : 0x2052DA50);
    const u32 put = put_at_park ? PARK_IO : PUBLISHED_PUT;
    vm_write32(GCM_CONTROL_GUEST_ADDR, put);
    vm_write32(GCM_CONTROL_GUEST_ADDR + 4, PARK_IO);
    s_fifo_getoff = PARK_IO;
    observed_label_ea = ppu_hle_inject_base + 0x850;
    g_ww_lo = observed_label_ea;
    g_ww_hi = observed_label_ea + 4;

    cellGcm_rsx_process_fifo();
    const u32 first_get = vm_read32(GCM_CONTROL_GUEST_ADDR + 4);
    const u32 first_label = vm_read32(observed_label_ea);
    int ok = check(first_get == (normal_jump ? PUBLISHED_PUT : PARK_IO),
        normal_jump ? "normal JUMP must follow the release stream to PUT" :
                      "a self-JUMP must preserve GET until the guest patches that word");
    ok &= check(first_label == (normal_jump ? 56 : 0) &&
                observed_label_writes == (normal_jump ? 1u : 0u),
                "first drain must execute only an already reachable label release");
    ok &= check(cellGcm_take_flip_pending() == 0 && cellGcm_flip_request_count() == 0,
                "parking or label release must not publish a present");
    fprintf(stderr, "gcm-flip-wrap park: first_get=%08X put=%08X label=%u writes=%u\n",
            first_get, put, first_label, observed_label_writes);

    if (!put_at_park && !normal_jump) {
        int stayed_parked = first_get == PARK_IO && first_label == 0 && observed_label_writes == 0;
        for (unsigned drain = 1; drain < parked_drains; ++drain) {
            cellGcm_rsx_process_fifo();
            stayed_parked &= vm_read32(GCM_CONTROL_GUEST_ADDR + 4) == PARK_IO &&
                vm_read32(GCM_CONTROL_GUEST_ADDR) == PUBLISHED_PUT &&
                vm_read32(observed_label_ea) == 0 && observed_label_writes == 0 &&
                cellGcm_take_flip_pending() == 0 && cellGcm_flip_request_count() == 0;
        }
        ok &= check(stayed_parked,
                    "every unpatched self-JUMP drain must retain GET without a label or present");
        fprintf(stderr, "gcm-flip-wrap park: unpatched_drains=%u get=%08X label=%u writes=%u\n",
                parked_drains, vm_read32(GCM_CONTROL_GUEST_ADDR + 4),
                vm_read32(observed_label_ea), observed_label_writes);
        /* The producer replaces the parked word. PUT stays in the main ring;
         * preserving GET is what lets the next drain discover this patch. */
        vm_write32(PARK_EA, 0x20535A94);
        cellGcm_rsx_process_fifo();
        ok &= check(vm_read32(GCM_CONTROL_GUEST_ADDR + 4) == PUBLISHED_PUT &&
                    vm_read32(observed_label_ea) == 56 && observed_label_writes == 1,
                    "next drain must follow the patched JUMP and release ticket 56 exactly once");
    }
    cellGcm_rsx_process_fifo();
    ok &= check(vm_read32(GCM_CONTROL_GUEST_ADDR + 4) == put &&
                vm_read32(observed_label_ea) == (put_at_park ? 0 : 56) &&
                observed_label_writes == (put_at_park ? 0u : 1u),
                "idle drain must retain GET and must not replay the label release");
    ok &= check(cellGcm_take_flip_pending() == 0 && cellGcm_flip_request_count() == 0,
                "patched JUMP and idle drain must not publish a present");
    fprintf(stderr, "gcm-flip-wrap park: final_get=%08X put=%08X label=%u writes=%u\n",
            vm_read32(GCM_CONTROL_GUEST_ADDR + 4), put,
            vm_read32(observed_label_ea), observed_label_writes);
    return ok ? 0 : 1;
}

static int budget_jump(int circle) {
    _putenv_s("GCM_FIFO_CIRCLE_PASSES", "1");
    if (!setup(0x80000, 1)) return 2;
    if (!check(cellGcmMapEaIoAddress(0x400000, 0x1000000, 0x1000000) == CELL_OK,
               "budget stream must have a real 16 MB IO mapping")) return 2;
    enum {
        BEGIN_EA = 0x400000, BEGIN_IO = 0x1000000,
        LAST_EA = 0x7FFFFC, LAST_IO = 0x13FFFFC,
        TAIL_EA = 0x800100, PUBLISHED_PUT = 0x1005C,
    };
    /* Exactly 0x100000 command headers consume the walker's real budget:
     * 0xFFFFF zero-count NOPs followed by a self-JUMP or a genuine cycle.
     * Neither the budget nor any watchdog counter is changed by the test. */
    for (u32 index = 0; index < 0xFFFFF; ++index)
        vm_write32(BEGIN_EA + index * 4, 0);
    vm_write32(LAST_EA, circle ? 0x21000000 : 0x213FFFFC);
    vm_write32(TAIL_EA, 0x00041D6C);
    vm_write32(TAIL_EA + 4, 0x850);
    vm_write32(TAIL_EA + 8, 0x00041D74);
    vm_write32(TAIL_EA + 12, 56);
    vm_write32(TAIL_EA + 16, 0x2001005C);
    vm_write32(GCM_CONTROL_GUEST_ADDR, PUBLISHED_PUT);
    vm_write32(GCM_CONTROL_GUEST_ADDR + 4, BEGIN_IO);
    s_fifo_getoff = BEGIN_IO;
    observed_label_ea = ppu_hle_inject_base + 0x850;
    g_ww_lo = observed_label_ea;
    g_ww_hi = observed_label_ea + 4;

    cellGcm_rsx_process_fifo();
    const u32 first_get = vm_read32(GCM_CONTROL_GUEST_ADDR + 4);
    int ok = check(first_get == (circle ? PUBLISHED_PUT : LAST_IO),
        circle ? "a genuine full-budget cycle must retain watchdog recovery to PUT" :
                 "self-JUMP on the last budget iteration must retain GET at the park");
    ok &= check(vm_read32(observed_label_ea) == 0 && observed_label_writes == 0,
                "full-budget drain must not release the unvisited ticket");
    ok &= check(cellGcm_take_flip_pending() == 0 && cellGcm_flip_request_count() == 0,
                "full-budget recovery or park must not publish a present");
    fprintf(stderr, "gcm-flip-wrap budget: circle=%d first_get=%08X expected=%08X label=%u writes=%u\n",
            circle, first_get, circle ? PUBLISHED_PUT : LAST_IO,
            vm_read32(observed_label_ea), observed_label_writes);
    if (!circle) {
        vm_write32(LAST_EA, 0x21400100);
        cellGcm_rsx_process_fifo();
        ok &= check(vm_read32(GCM_CONTROL_GUEST_ADDR + 4) == PUBLISHED_PUT &&
                    vm_read32(observed_label_ea) == 56 && observed_label_writes == 1,
                    "next drain must observe the last-word patch and release ticket 56 once");
    }
    cellGcm_rsx_process_fifo();
    ok &= check(vm_read32(GCM_CONTROL_GUEST_ADDR + 4) == PUBLISHED_PUT &&
                vm_read32(observed_label_ea) == (circle ? 0 : 56) &&
                observed_label_writes == (circle ? 0u : 1u),
                "idle drain after full-budget work must not replay a release");
    ok &= check(cellGcm_take_flip_pending() == 0 && cellGcm_flip_request_count() == 0,
                "post-budget patch and idle drain must not publish a present");
    fprintf(stderr, "gcm-flip-wrap budget: final_get=%08X label=%u writes=%u\n",
            vm_read32(GCM_CONTROL_GUEST_ADDR + 4), vm_read32(observed_label_ea), observed_label_writes);
    return ok ? 0 : 1;
}

int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "title-context";
    _putenv_s("GCM_FLIP_BT", "");
    _putenv_s("GCM_GET_EQ_PUT", "");
    _putenv_s("GCM_SEMA_ACQUIRE", "");
    _putenv_s("GCM_FIFO_CIRCLE_PASSES", "");
    int result;
    if (!strcmp(mode, "runtime-ring")) result = runtime_ring();
    else if (!strcmp(mode, "title-context")) result = title_context(0, 0);
    else if (!strcmp(mode, "zero-callback")) result = title_context(0, 1);
    else if (!strcmp(mode, "title-tail")) result = title_tail();
    else if (!strcmp(mode, "ample-room")) result = title_context(1, 0);
    else if (!strcmp(mode, "rejected")) result = rejected();
    else if (!strcmp(mode, "small-title")) result = small_slice(0, 0);
    else if (!strcmp(mode, "small-null")) result = small_slice(1, 0);
    else if (!strcmp(mode, "exact-fit")) result = small_slice(1, 1);
    else if (!strcmp(mode, "park-retry")) result = parked_jump(0, 0, 1);
    else if (!strcmp(mode, "park-long-retry")) result = parked_jump(0, 0, 12);
    else if (!strcmp(mode, "park-at-put")) result = parked_jump(1, 0, 1);
    else if (!strcmp(mode, "normal-jump")) result = parked_jump(0, 1, 1);
    else if (!strcmp(mode, "budget-park")) result = budget_jump(0);
    else if (!strcmp(mode, "budget-circle")) result = budget_jump(1);
    else return 2;
    fprintf(stderr, "gcm-flip-wrap %s %s\n", mode, result ? "FAIL" : "PASS");
    return result;
}
