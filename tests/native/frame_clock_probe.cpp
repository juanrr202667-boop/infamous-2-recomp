/* Exercise the real runner with a synthetic FIFO flip. No game code runs. */
#define main static unused_port_main
#include "../../src/port/main.cpp"
#undef main
#include <setjmp.h>

/* This fixture has no guest VM or loader. */
extern "C" {
    uint32_t ppu_vm_size = 0;
    sys_mem_alloc_info g_sys_mem_allocs[SYS_MEMORY_ALLOC_MAX] = {};
}

static jmp_buf clock_done;
static unsigned requests = 0;
static int pending = 0;
static int outer_drains = 0;
static bool tick_drain = false;

extern "C" void cellGcmTickVBlank(void) { tick_drain = true; }
extern "C" void cellGcmTickFlip(void) {}
extern "C" int cellGcm_take_flip_pending(void)
{
    int result = pending;
    pending = 0;
    return result;
}
extern "C" unsigned cellGcm_flip_request_count(void) { return requests; }
extern "C" void cellGcm_rsx_process_fifo(void)
{
    if (!tick_drain) {
        if (++outer_drains == 1) {
            ++requests;
            pending = 1;
        }
        if (outer_drains == 3) longjmp(clock_done, 1);
    }
    tick_drain = false;
}
extern "C" int rsx_d3d12_backend_init(uint32_t, uint32_t, const char*) { return 0; }
extern "C" void rsx_d3d12_backend_present(void) {}
extern "C" int rsx_d3d12_backend_pump_messages(void) { return 0; }
extern "C" void rsx_set_backend(rsx_backend*) {}
extern "C" {
void func_0004A160(ppu_context*) {}
void func_006BCF80(ppu_context*) {}
void func_004859E4(ppu_context*) {}
void func_006C99D0(ppu_context*) {}
void func_004858A4(ppu_context*) {}
void cellPad_inject_stick(uint32_t, uint8_t, uint8_t, uint8_t, uint8_t, int) {}
}

/* No VM or input is available in this clock-only fixture. */
extern "C" void cellPad_inject_pulse(uint32_t, uint16_t, int)
{
    fprintf(stderr, "unexpected input pulse in clock-only fixture\n");
    exit(2);
}

int main()
{
    g_headless = true;
    _putenv_s("I2_TELEMETRY_PATH", "");
    _putenv_s("I2_LIGHT_KICKER_STATS", "0");
    if (setjmp(clock_done) == 0) frame_clock(nullptr);
    unsigned frames = ppu_boot_frames_presented();
    fprintf(stderr, "synthetic FIFO flips=%u counted guest frames=%u\n", requests, frames);
    return frames == 1 ? 0 : 1;
}
