/* Real NV3089 copy with synthetic guest memory. Missing diagnostics, gate/cap
 * violations, and instrumentation that changes pixels must fail here. */
#define main unused_flip_fixture_main
#include "gcm_flip_wrap_probe.c"
#undef main
#include <io.h>

/* Capture for the "target" mode: a lookup the SDK must call for the event's own
 * source, and an observer that records what reached it. */
static unsigned probe_saw_events;
static unsigned probe_saw_origin;
static cellGcmTransferProbe probe_seen;
static unsigned probe_lookup_calls, probe_lookup_location, probe_lookup_offset, probe_lookup_pitch;

static void probe_capture(const cellGcmTransferProbe* event)
{
    ++probe_saw_events;
    probe_seen = *event;
    probe_saw_origin = event->source_origin;
}

static int probe_fake_target(u32 location, u32 offset, u32 pitch, cellGcmTransferProbeTarget* out)
{
    ++probe_lookup_calls;
    probe_lookup_location = location;
    probe_lookup_offset = offset;
    probe_lookup_pitch = pitch;
    out->location = 1; out->offset = 0xC0E3C000u; out->pitch = 1024;
    out->width = 1280; out->height = 720;
    out->epoch = 42; out->argb8 = 1;
    return 1;
}

static unsigned probe_writeback_calls;
static u32 probe_writeback_location, probe_writeback_offset, probe_writeback_pitch;

static int probe_test_writeback(u32 location, u32 offset, u32 pitch)
{
    ++probe_writeback_calls;
    probe_writeback_location = location;
    probe_writeback_offset = offset;
    probe_writeback_pitch = pitch;
    const u32 src_ea = cellGcmResolveLocated(location == 1 ? 1 : 0, offset);
    for (u32 y = 0; y < 2; ++y) {
        for (u32 x = 0; x < 2; ++x) {
            vm_write32(src_ea + y * pitch + x * 4, 0x41424344 + y * 2 + x);
        }
    }
    return 1;
}

static int probe_no_target(u32 location, u32 offset, u32 pitch, cellGcmTransferProbeTarget* out)
{
    (void)location; (void)offset; (void)pitch; (void)out;
    return 0;
}

int main(int argc, char** argv)
{
    const char* mode = argc > 1 ? argv[1] : "copy";
    if (!strcmp(mode, "layout")) {
        cellGcmTransferProbe event = {0};
        event.source_location = 1; event.source_offset = 0x1020;
        event.source_pitch = 32; event.width = 4; event.height = 2;
        int ok = check(cellGcmTransferProbeFits(&event, 1, 0x1000, 32, 4, 4),
                       "producer must include a copy starting on a later row");
        event.source_offset = 0x1014;
        ok &= check(!cellGcmTransferProbeFits(&event, 1, 0x1000, 32, 4, 4),
                    "row padding cannot identify a producer");
        event.source_offset = 0x100C;
        ok &= check(!cellGcmTransferProbeFits(&event, 1, 0x1000, 32, 4, 4),
                    "copy crossing the source width must not identify a producer");
        event.source_offset = 0x1060;
        ok &= check(!cellGcmTransferProbeFits(&event, 1, 0x1000, 32, 4, 4),
                    "copy crossing the source height must not identify a producer");
        event.source_offset = 0x1000;
        ok &= check(!cellGcmTransferProbeFits(&event, 2, 0x1000, 32, 4, 4),
                    "overlapping MAIN and LOCAL offsets are distinct domains");
        ok &= check(!cellGcmTransferProbeFits(&event, 1, 0x1000, 16, 4, 4),
                    "different pitches cannot identify the same copy rectangle");
        return ok ? 0 : 1;
    }
    if (!strcmp(mode, "origin")) {
        /* The NV3089 source origin is 12.4 fixed point. A copy must start at
         * the pixel the origin names; counting it 256x too small silently
         * copies row/column zero of the surface instead. */
        _putenv_s("NV3089_PROBE_GATE", "");
        _putenv_s("NV3089_GATE", "");
        _putenv_s("NV3089_DBG", "");
        s_config.localAddress = 0x100000;
        s_ea_address_table[2] = 8;
        s_nv3089.src_dma = 0xFEED0000;          /* LOCAL */
        s_nv3089.in_fmt = 32;
        s_nv3089.in_off = 0x100;
        s_nv3089.in_uv = (0x0020u << 16) | 0x0020u;   /* 2.0 px, 2.0 px */
        s_nv3089.ds_dx = s_nv3089.dt_dy = 0x100000;   /* 1 px per step */
        s_gcm2d.dst_dma = 0xFEED0001;           /* MAIN */
        s_gcm2d.dst_offset = 0x200000;
        s_gcm2d.pitch = 32u << 16;              /* 32 B = 8 words per row */
        s_gcm2d.color_fmt = 0xA;
        const u32 src_ea = cellGcmResolveLocated(1, s_nv3089.in_off);
        const u32 dst_ea = cellGcmResolveLocated(0, s_gcm2d.dst_offset);
        for (u32 y = 0; y < 8; ++y)
            for (u32 x = 0; x < 8; ++x)
                vm_write32(src_ea + y * 32 + x * 4, 0x01020304 + y * 8 + x);

        /* Linear destination: 2x2 out of the 12.4 origin. */
        s_nv3089.fmt = 7;                       /* A8R8G8B8 */
        s_nv3089.out_sz = s_nv3089.in_sz = (2u << 16) | 2u;
        s_nv3089.out_pt = 0;
        vm_write32(dst_ea, 0xDEADBEEF);
        vm_write32(dst_ea + 4, 0xDEADBEEF);
        vm_write32(dst_ea + 32, 0xDEADBEEF);
        vm_write32(dst_ea + 36, 0xDEADBEEF);
        nv3089_blit();
        int ok = check(vm_read32(dst_ea) == 0x01020304 + 2 * 8 + 2,
                       "copy must start at the 12.4 source origin");
        ok &= check(vm_read32(dst_ea + 4) == 0x01020304 + 2 * 8 + 3,
                    "copy must advance one source pixel per destination pixel");
        ok &= check(vm_read32(dst_ea + 32) == 0x01020304 + 3 * 8 + 2,
                    "copy must advance one source row per destination row");

        /* NV309E swizzled mip/blur pyramid: same origin, different layout. */
        s_nv3089.fmt = 3;
        s_nv309e.active = 1;
        s_nv309e.fmt = (1u << 24) | (1u << 16); /* 2x2 in log2 form */
        s_nv309e.offset = 0x400;
        const u32 swz_ea = cellGcmResolveLocated(1, s_nv309e.offset);
        for (u32 i = 0; i < 4; ++i) vm_write32(swz_ea + i * 4, 0xDEADBEEF);
        nv3089_blit();
        ok &= check(vm_read32(swz_ea + nv309e_swz(0, 0, 1, 1) * 4) == 0x01020304 + 2 * 8 + 2,
                    "swizzled copy must start at the 12.4 source origin");
        fprintf(stderr, "nv3089-probe %s %s\n", mode, ok ? "PASS" : "FAIL");
        return ok ? 0 : 1;
    }
    if (!strcmp(mode, "writeback")) {
        _putenv_s("NV3089_PROBE_GATE", "");
        _putenv_s("NV3089_GATE", "");
        _putenv_s("NV3089_DBG", "");
        s_config.localAddress = 0x100000;
        s_ea_address_table[2] = 8;
        s_nv3089.src_dma = 0xFEED0000;          /* LOCAL */
        s_nv3089.in_fmt = 32;
        s_nv3089.in_off = 0x100;
        s_nv3089.in_uv = 0;
        s_nv3089.ds_dx = s_nv3089.dt_dy = 0x100000;
        s_gcm2d.dst_dma = 0xFEED0001;           /* MAIN */
        s_gcm2d.dst_offset = 0x200000;
        s_gcm2d.pitch = 32u << 16;
        s_gcm2d.color_fmt = 0xA;
        const u32 src_ea = cellGcmResolveLocated(1, s_nv3089.in_off);
        const u32 dst_ea = cellGcmResolveLocated(0, s_gcm2d.dst_offset);

        /* Guest source memory starts as zeros */
        for (u32 i = 0; i < 8; ++i) vm_write32(src_ea + i * 4, 0);
        for (u32 i = 0; i < 8; ++i) vm_write32(dst_ea + i * 4, 0xDEADBEEF);

        s_nv3089.fmt = 7;                       /* A8R8G8B8 */
        s_nv3089.out_sz = s_nv3089.in_sz = (2u << 16) | 2u;
        s_nv3089.out_pt = 0;

        cellGcmSetWritebackHook(probe_test_writeback);
        probe_writeback_calls = 0;

        nv3089_blit();

        int ok = check(probe_writeback_calls == 1,
                       "writeback hook must be called exactly once before pixel reading");
        ok &= check(probe_writeback_location == 1u,
                    "writeback hook must receive the correct source location");
        ok &= check(probe_writeback_offset == 0x100u,
                    "writeback hook must receive the correct source offset");
        ok &= check(probe_writeback_pitch == 32u,
                    "writeback hook must receive the correct source pitch");
        ok &= check(vm_read32(dst_ea) == 0x41424344,
                    "blit must copy the written back pixels (0,0)");
        ok &= check(vm_read32(dst_ea + 4) == 0x41424345,
                    "blit must copy the written back pixels (1,0)");
        ok &= check(vm_read32(dst_ea + 32) == 0x41424346,
                    "blit must copy the written back pixels (0,1)");
        ok &= check(vm_read32(dst_ea + 36) == 0x41424347,
                    "blit must copy the written back pixels (1,1)");

        /* Uninstall hook: must not call hook */
        cellGcmSetWritebackHook(NULL);
        probe_writeback_calls = 0;
        nv3089_blit();
        ok &= check(probe_writeback_calls == 0,
                    "uninstalled writeback hook must not be called");

        fprintf(stderr, "nv3089-probe %s %s\n", mode, ok ? "PASS" : "FAIL");
        return ok ? 0 : 1;
    }

    if (!strcmp(mode, "target")) {
        /* First-row producer selection, the rule the public macOS reference uses
         * before its write-back, plus the real gate path with a non-zero origin. */
        int ok = check(cellGcmTransferProbeFirstRow(1, 0x1008, 1, 0x1000, 32, 8),
                       "the first row must identify a producer at a later offset");
        ok &= check(!cellGcmTransferProbeFirstRow(1, 0x1020, 1, 0x1000, 32, 8),
                    "a later row must not identify a producer");
        ok &= check(!cellGcmTransferProbeFirstRow(2, 0x1008, 1, 0x1000, 32, 8),
                    "another location must not identify a producer");
        ok &= check(!cellGcmTransferProbeFirstRow(1, 0x1006, 1, 0x1000, 32, 8),
                    "a misaligned offset must not identify a producer");
        ok &= check(!cellGcmTransferProbeFirstRow(1, 0x1008, 1, 0x1000, 32, 1),
                    "an offset past the row's pixels must not identify a producer");
        ok &= check(!cellGcmTransferProbeFirstRow(1, 0x1000, 1, 0x1000, 0, 8),
                    "a surface without a pitch must not identify a producer");
        ok &= check(!cellGcmTransferProbeFirstRow(1, 0x0FFF, 1, 0x1000, 32, 8),
                    "an offset before the surface must not identify a producer");

        /* A real copy whose origin is non-zero, with a lookup and an observer. */
        _putenv_s("NV3089_PROBE_GATE", "nv3089-probe-target.ready");
        _putenv_s("NV3089_GATE", "");
        _putenv_s("NV3089_DBG", "");
        _putenv_s("NV3089_PROBE_MAX", "4");
        cellGcmSetTransferProbeTargetLookup(probe_fake_target);
        cellGcmSetTransferProbeObserver(probe_capture);
        s_config.localAddress = 0x100000;
        s_ea_address_table[2] = 8;
        s_nv3089.src_dma = 0xFEED0000;
        s_nv3089.fmt = 3;
        s_nv3089.out_sz = s_nv3089.in_sz = (2u << 16) | 2u;
        s_nv3089.in_fmt = 32;
        s_nv3089.in_off = 0x100;
        s_nv3089.in_uv = (0x0010u << 16) | 0x0010u;    /* 1.0 px, 1.0 px */
        s_nv3089.ds_dx = s_nv3089.dt_dy = 0x100000;
        s_gcm2d.dst_dma = 0xFEED0001;
        s_gcm2d.dst_offset = 0x200000;
        s_gcm2d.pitch = 32u << 16;
        s_gcm2d.color_fmt = 0xA;
        FILE* ready = fopen("nv3089-probe-target.ready", "wb");
        if (!ready) return 2;
        fclose(ready);
        nv3089_blit();
        remove("nv3089-probe-target.ready");
        ok &= check(probe_saw_events == 1,
                    "the observer must see the copy once the origin may be non-zero");
        ok &= check(probe_saw_origin == 0x00100010u, "the raw 12.4 origin must be recorded");
        ok &= check(probe_seen.target_found && probe_seen.target_offset == 0xC0E3C000u &&
                    probe_seen.target_width == 1280u && probe_seen.target_height == 720u,
                    "the installed lookup must fill the target fields");
        ok &= check(probe_seen.target_epoch == 42u && probe_seen.target_argb8 == 1,
                    "target epoch and 32-bit flag must reach the observer");
        ok &= check(probe_lookup_calls == 1 && probe_lookup_location == 1u &&
                    probe_lookup_offset == 0x100u && probe_lookup_pitch == 32u,
                    "the lookup must be asked about the event's own source");

        /* A copy with no candidate must report no target at all. The observer
         * reads a fresh event, so anything left over is stale or garbage and
         * must not be presented as metadata. */
        cellGcmSetTransferProbeTargetLookup(probe_no_target);
        probe_saw_events = 0;
        ready = fopen("nv3089-probe-target.ready", "wb");
        if (!ready) return 2;
        fclose(ready);
        nv3089_blit();
        remove("nv3089-probe-target.ready");
        ok &= check(probe_saw_events == 1 && probe_seen.target_found == 0,
                    "a copy without a candidate must report no target");
        ok &= check(probe_seen.target_location == 0 && probe_seen.target_offset == 0 &&
                    probe_seen.target_pitch == 0 && probe_seen.target_width == 0 &&
                    probe_seen.target_height == 0 && probe_seen.target_epoch == 0 &&
                    probe_seen.target_argb8 == 0,
                    "a copy without a candidate must not report stale target metadata");
        cellGcmSetTransferProbeTargetLookup(NULL);
        cellGcmSetTransferProbeObserver(NULL);
        fprintf(stderr, "nv3089-probe %s %s\n", mode, ok ? "PASS" : "FAIL");
        return ok ? 0 : 1;
    }
    char gate[MAX_PATH];
    snprintf(gate, sizeof gate, "nv3089-probe-%lu.ready", GetCurrentProcessId());
    remove(gate);
    _putenv_s("NV3089_PROBE_GATE", strcmp(mode, "disabled") ? gate : "");
    _putenv_s("NV3089_PROBE_MAX", "2");
    _putenv_s("NV3089_GATE", "");
    _putenv_s("NV3089_DBG", "");
    FILE* capture = tmpfile();
    if (!capture) return 2;
    fflush(stdout);
    int saved = _dup(_fileno(stdout));
    if (saved < 0 || _dup2(_fileno(capture), _fileno(stdout))) return 2;

    s_config.localAddress = 0x100000;
    s_ea_address_table[2] = 8;
    s_nv3089.src_dma = 0xFEED0000;
    s_nv3089.fmt = 3;
    s_nv3089.out_sz = s_nv3089.in_sz = (4u << 16) | 4u;
    s_nv3089.in_fmt = 32;
    s_nv3089.in_off = 0x100;
    s_nv3089.ds_dx = s_nv3089.dt_dy = 0x100000;
    s_gcm2d.dst_dma = 0xFEED0001;
    s_gcm2d.dst_offset = 0x200000;
    s_gcm2d.pitch = 32u << 16;
    s_gcm2d.color_fmt = 0xA;
    for (u32 y = 0; y < 4; ++y)
        for (u32 x = 0; x < 8; ++x) {
            vm_write32(0x100100 + y * 32 + x * 4, 0x01020304 + y * 4 + x);
            vm_write32(0x800000 + y * 32 + x * 4, 0xAABBCCDD);
        }
    /* A closed gate must neither log nor consume the cap. */
    nv3089_blit();
    for (u32 y = 0; y < 4; ++y)
        for (u32 x = 0; x < 4; ++x)
            vm_write32(0x800000 + y * 32 + x * 4, 0xAABBCCDD);
    FILE* ready = fopen(gate, "wb");
    if (!ready) return 2;
    fclose(ready);
    if (!strcmp(mode, "clip")) s_nv3089.clip_sz = (4u << 16) | 2u;
    for (int i = 0; i < (!strcmp(mode, "cap") ? 4 : 1); ++i) nv3089_blit();
    fflush(stdout);
    _dup2(saved, _fileno(stdout));
    _close(saved);
    rewind(capture);
    char output[16384];
    size_t n = fread(output, 1, sizeof output - 1, capture);
    output[n] = 0;
    fclose(capture);
    remove(gate);
    unsigned lines = 0;
    for (char* p = output; (p = strstr(p, "[NV3089P]")); ++p) ++lines;
    const unsigned wanted = (!strcmp(mode, "disabled") || !strcmp(mode, "clip")) ? 0 :
        (!strcmp(mode, "cap") ? 2 : 1);
    int ok = check(lines == wanted, "diagnostic gate and cap must bound real copy reports");
    if (wanted) {
        ok &= check(strstr(output, "src_first=01020304 src_last=01020313") != NULL,
                    "sample must read source words in big-endian order, excluding pitch padding");
        ok &= check(strstr(output, "before_first=AABBCCDD") != NULL,
                    "destination must be sampled before the real copy");
        ok &= check(strstr(output, "after_first=01020304 after_last=01020313") != NULL,
                    "destination must be sampled after the real copy");
    }
    for (u32 y = 0; y < 4; ++y)
        for (u32 x = 0; x < 8; ++x) {
            u32 expected = x < (!strcmp(mode, "clip") ? 2u : 4u) ?
                0x01020304 + y * 4 + x : 0xAABBCCDD;
            ok &= check(vm_read32(0x800000 + y * 32 + x * 4) == expected,
                        "probe must preserve real clipping, pixel bytes and row padding");
        }
    fprintf(stderr, "nv3089-probe %s reports=%u wanted=%u %s\n",
            mode, lines, wanted, ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
