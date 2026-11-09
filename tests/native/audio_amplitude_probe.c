/* Real mixer, synthetic BE PCM. No host device, game data or threads. */
#include "cellAudio.c"
#include <stdlib.h>
#include <stdio.h>

static unsigned char memory[4096];
uint8_t* vm_base = memory;
uint32_t ppu_vm_size = sizeof memory;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
void ppu_resv_break_store(uint64_t ea) { (void)ea; abort(); }
void ps3_ww_report_inline(uint32_t a, uint64_t v, int w) { (void)a; (void)v; (void)w; abort(); }
int spu_coh_is_reserved(uint32_t a) { (void)a; return 0; }
void spu_coh_notify_write(uint32_t a) { (void)a; abort(); }
void spu_lockline_lock(void) { abort(); }
void spu_lockline_unlock(void) { abort(); }
uint32_t sys_event_queue_create_direct(uint64_t key, int32_t size) { (void)key; (void)size; abort(); }
uint32_t sys_event_find_queue_by_key(uint64_t key) { (void)key; abort(); }
int sys_event_queue_push_by_id(uint32_t id, uint64_t source, uint64_t a, uint64_t b, uint64_t c) {
    (void)id; (void)source; (void)a; (void)b; (void)c; abort();
}

static int check(int ok, const char* why) {
    if (!ok) printf("FAIL: %s\n", why);
    return ok;
}
static void store_be(float* dst, float value) {
    uint32_t bits; memcpy(&bits, &value, 4);
    bits = ps3_bswap32(bits); memcpy(dst, &bits, 4);
}
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    int enabled = strstr(argv[1], "disabled") == NULL;
    int eight = strstr(argv[1], "eight") != NULL;
    int nonfinite = strstr(argv[1], "nonfinite") != NULL;
    _putenv_s("AUDIO_AMPLITUDE_STATS", enabled ? "1" : "0");
    _putenv_s("AUDIO_PORT_STATS", "0");
    _putenv_s("AUDIO_PEAK", "");
    if (!freopen(argv[2], "w", stderr)) return 2;
    static float samples[2][CELL_AUDIO_BLOCK_SAMPLES * 8];
    mutex_init(&s_audio_mutex);
    for (int p = 0; p < 2; ++p) {
        s_ports[p].in_use = s_ports[p].running = 1;
        s_ports[p].buffer = samples[p];
        s_ports[p].param.nChannel = p == 0 && eight ? 8 : 2;
        s_ports[p].param.nBlock = 1;
        s_ports[p].param.attr = 0x1000;
        s_ports[p].param.level = p ? 1.0f : 0.5f;
    }
    int ok = 1;
    uint64_t checksum = 14695981039346656037ULL;
    for (unsigned b = 0; b < 938; ++b) {
        for (unsigned i = 0; i < CELL_AUDIO_BLOCK_SAMPLES * 2; ++i) {
            store_be(samples[1] + i, i % 2 ? -0.75f : 0.75f);
        }
        for (unsigned s = 0; s < CELL_AUDIO_BLOCK_SAMPLES; ++s) {
            unsigned nch = eight ? 8 : 2;
            store_be(samples[0] + s * nch, nonfinite ? NAN : 1.0f);
            store_be(samples[0] + s * nch + 1, nonfinite ? INFINITY : -1.0f);
            for (unsigned ch = 2; ch < nch; ++ch) store_be(samples[0] + s * nch + ch, 0);
        }
        audio_mix_one_block();
        for (unsigned i = 0; i < CELL_AUDIO_BLOCK_SAMPLES * 2; ++i) {
            if (nonfinite) ok &= check(i % 2 ? s_mix_buffer[i] == 1.0f : isnan(s_mix_buffer[i]), "observer changed nonfinite PCM");
            else if (eight) ok &= check(fabsf(s_mix_buffer[i] - (i % 2 ? -0.91627872f : 0.91627872f)) < 0.000001f, "observer changed sub-clamp PCM");
            else ok &= check(s_mix_buffer[i] == (i % 2 ? -1.0f : 1.0f), "observer changed mix/clamp");
            ok &= check(samples[1][i] == 0, "consumed stereo source not cleared");
            uint32_t bits; memcpy(&bits, s_mix_buffer + i, sizeof bits);
            checksum = (checksum ^ bits) * 1099511628211ULL;
        }
        for (unsigned i = 0; i < CELL_AUDIO_BLOCK_SAMPLES * (eight ? 8 : 2); ++i)
            ok &= check(samples[0][i] == 0, "consumed source not cleared");
        if (!ok) break;
    }
    ok &= check(s_ports[0].read_index == 938 && s_ports[1].read_index == 938, "read index changed");
    mutex_destroy(&s_audio_mutex);
    fflush(stderr);
    FILE* log = fopen(argv[2], "r");
    if (!log) return 2;
    char line[2048]; unsigned reports = 0, seen = 0;
    while (fgets(line, sizeof(line), log)) {
        if (!strstr(line, "[audio-amplitude]")) continue;
        reports++;
        char stage[24]; int port; unsigned blocks;
        unsigned long long count, finite, invalid, rails, over;
        double peak, mean, rms;
        int fields = sscanf(line, "[audio-amplitude] stage=%23s port=%d samples=%llu finite=%llu nonfinite=%llu rails=%llu over=%llu peak=%lf mean=%lf rms=%lf blocks=%u",
            stage, &port, &count, &finite, &invalid, &rails, &over, &peak, &mean, &rms, &blocks);
        ok &= check(fields == 11, "malformed diagnostic record");
        if (fields != 11) continue;
        int index = -1;
        if (!strcmp(stage, "source") && port >= 0 && port < 2) index = port * 2;
        if (!strcmp(stage, "contribution") && port >= 0 && port < 2) index = port * 2 + 1;
        if (!strcmp(stage, "mix") && port == -1) index = 4;
        ok &= check(index >= 0 && !(seen & (1u << index)), "unexpected/duplicate stage");
        if (index < 0) continue;
        seen |= 1u << index;
        unsigned long long expected_count = eight && index == 0 ? 1921024 : 480256;
        int invalid_stage = nonfinite && (index == 0 || index == 1 || index == 4);
        double expected_peak = index >= 2 && index < 4 ? 0.75 :
            index == 0 ? 1 : index == 1 ? (eight ? 0.16627872 : 0.5) : (eight ? 0.91627872 : 1.25);
        double expected_rms = eight && index == 0 ? 0.5 : expected_peak;
        if (invalid_stage) expected_peak = expected_rms = 0;
        ok &= check(count == expected_count && finite == (invalid_stage ? 0 : expected_count) &&
            invalid == (invalid_stage ? expected_count : 0) && blocks == 938, "sample accounting mismatch");
        ok &= check(rails == (!nonfinite && index == 0 ? 480256 : 0) &&
            over == (!nonfinite && !eight && index == 4 ? 480256 : 0), "rail/overshoot mismatch");
        ok &= check(fabs(peak - expected_peak) < 0.000001 && fabs(mean) < 0.000001 &&
            fabs(rms - expected_rms) < 0.000001, "amplitude summary mismatch");
    }
    fclose(log);
    ok &= check(enabled ? reports == 5 && seen == 31 : reports == 0, "missing/wrong amplitude diagnostics");
    printf("audio amplitude %s: %s checksum=%016llX\n", argv[1], ok ? "PASS" : "FAIL", (unsigned long long)checksum);
    return ok ? 0 : 1;
}
