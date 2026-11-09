/* Real PortOpen and mixer, synthetic BE guest memory, no device or threads. */
#include "cellAudio.c"

uint8_t* vm_base;
uint32_t ppu_vm_size;
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

static void store_be32(unsigned char* dst, uint32_t bits)
{
    for (unsigned i = 0; i < 4; ++i) dst[i] = (unsigned char)(bits >> (24 - i * 8));
}

static void store_be64(unsigned char* dst, uint64_t value)
{
    for (unsigned i = 0; i < 8; ++i) dst[i] = (unsigned char)(value >> (56 - i * 8));
}

static uint32_t float_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof bits);
    return bits;
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    _putenv_s("AUDIO_AMPLITUDE_STATS", "1");
    _putenv_s("AUDIO_PORT_STATS", "0");
    _putenv_s("AUDIO_PEAK", "");
    if (!freopen(argv[1], "w", stderr)) return 2;

    /* Reserve the address space only; PortOpen uses the real inline vm_commit. */
    vm_base = (uint8_t*)VirtualAlloc(NULL, (SIZE_T)VM_TOTAL_SIZE, MEM_RESERVE, PAGE_NOACCESS);
    if (!vm_base || vm_commit(0x10000, 0x1000) != CELL_OK) return 2;
    ppu_vm_size = UINT32_MAX;
    mutex_init(&s_audio_mutex);
    s_audio_initialized = 1;

    static const struct {
        const char* name;
        uint64_t attr;
        uint32_t level_bits, left_bits, right_bits;
        double gain;
    } cases[] = {
        {"default-zero", 0, 0x00000000, 0x3E800000, 0xBF000000, 1.0},
        {"default-half", 0, 0x3F000000, 0x3E800000, 0xBF000000, 1.0},
        {"default-nan", 0, 0x7FC12345, 0x3E800000, 0xBF000000, 1.0},
        {"initial-zero", 0x1000, 0x00000000, 0x00000000, 0x00000000, 0.0},
        {"initial-half", 0x1000, 0x3F000000, 0x3E000000, 0xBE800000, 0.5},
        {"initial-one", 0x1000, 0x3F800000, 0x3E800000, 0xBF000000, 1.0},
    };

    int ok = 1;
    for (unsigned c = 0; c < sizeof cases / sizeof cases[0]; ++c) {
        unsigned char* param = vm_base + 0x10000;
        memset(param, 0xA5, 32); /* Poison padding after the f32 at offset 24. */
        store_be64(param, 2);
        store_be64(param + 8, 8);
        store_be64(param + 16, cases[c].attr);
        store_be32(param + 24, cases[c].level_bits);
        vm_write32(0x10100, UINT32_MAX);
        if (cellAudioPortOpen((const CellAudioPortParam*)(uintptr_t)0x10000,
                              (u32*)(uintptr_t)0x10100) != CELL_OK) return 2;
        const u32 port_num = vm_read32(0x10100);
        if (port_num != 0) return 2;
        AudioPortSlot* port = &s_ports[port_num];
        int passed = port->param.nChannel == 2 && port->param.nBlock == 8 &&
            port->param.attr == cases[c].attr && float_bits(port->param.level) == cases[c].level_bits;
        if (cellAudioPortStart(port_num) != CELL_OK) return 2;
        for (unsigned s = 0; s < CELL_AUDIO_BLOCK_SAMPLES; ++s) {
            store_be32((unsigned char*)(port->buffer + s * 2), 0x3E800000); /* +0.25 */
            store_be32((unsigned char*)(port->buffer + s * 2 + 1), 0xBF000000); /* -0.5 */
        }
        audio_mix_one_block();
        passed &= float_bits(port->param.level) == cases[c].level_bits;
        for (unsigned s = 0; s < CELL_AUDIO_BLOCK_SAMPLES; ++s) {
            passed &= float_bits(s_mix_buffer[s * 2]) == cases[c].left_bits &&
                float_bits(s_mix_buffer[s * 2 + 1]) == cases[c].right_bits;
            passed &= port->buffer[s * 2] == 0 && port->buffer[s * 2 + 1] == 0;
        }
        passed &= port->read_index == 1 && vm_read64((u32)port->read_idx_addr) == 1;
        printf("audio port level %s: %s left=%08X right=%08X\n", cases[c].name,
               passed ? "PASS" : "FAIL", float_bits(s_mix_buffer[0]), float_bits(s_mix_buffer[1]));
        ok &= passed;
        if (cellAudioPortClose(port_num) != CELL_OK) return 2;
    }

    fflush(stderr);
    FILE* log = fopen(argv[1], "r");
    if (!log) return 2;
    char line[2048];
    unsigned reports = 0;
    while (fgets(line, sizeof line, log)) {
        if (!strstr(line, "[audio-gain]")) continue;
        unsigned port, channels, bits;
        unsigned long long attr;
        double raw, applied;
        int fields = sscanf(line, "[audio-gain] port=%u channels=%u attr=%llX level_bits=%X level=%lf applied=%lf",
                            &port, &channels, &attr, &bits, &raw, &applied);
        if (reports >= sizeof cases / sizeof cases[0]) { ok = 0; continue; }
        ok &= fields == 6 && port == 0 && channels == 2 && attr == cases[reports].attr &&
            bits == cases[reports].level_bits && applied == cases[reports].gain;
        reports++;
    }
    fclose(log);
    ok &= reports == sizeof cases / sizeof cases[0];
    printf("audio port level diagnostics: %s records=%u\n", ok ? "PASS" : "FAIL", reports);
    s_audio_initialized = 0;
    mutex_destroy(&s_audio_mutex);
    VirtualFree(vm_base, 0, MEM_RELEASE);
    vm_base = NULL;
    return ok ? 0 : 1;
}
