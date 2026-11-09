/*
 * ps3recomp game project -- entry point template.
 *
 * This is the runner. It boots a lifted title through the toolkit's PPU
 * scaffold: allocate the flat guest VM, load the PPU ELF named on the command
 * line, register the lifted function table and the HLE NID handlers, start a
 * frame clock, and dispatch the entry OPD.
 *
 * The scaffold does the work; nothing below reimplements it. ppu_load_elf,
 * ppu_recomp_register, ppu_hle_init, ppu_sysprx_register, ppu_fs_register,
 * lv2_init_syscalls and ppu_run all come from the toolkit, compiled into this
 * project from PS3RECOMP_DIR (see CMakeLists.txt). What is left here is the
 * part a port owns: which backend to present through, how the frame clock is
 * paced, and whatever diagnostics the title turns out to need.
 *
 * runtime/ppu/tests/boot_main.cpp in the toolkit is the same boot in its
 * fully-instrumented form -- crash filter, hang watchdog, guest-PC sampling
 * profiler. Read it when a boot goes wrong; copy from it what the title
 * needs. It is deliberately not what a fresh project starts with.
 *
 * The one part that IS duplicated from it is the frame clock below, because a
 * port is expected to change its pacing and a shared one would be the wrong
 * shape for that. It was copied faithfully, comments and all. If a pacing bug
 * is fixed in one of the two, look at the other.
 *
 * This builds on Windows, macOS and Linux. CI builds and runs it against the
 * boot smoke title on the last two, and compile-checks it against clang-cl on
 * the first (tools/check_ppu_scaffold.py).
 */

/* The lifter's generated header, which declares ppu_context and the function
 * table. It comes first: everything below is written against that struct. */
#include "ppu_recomp.h"

/* The Win32 names -- Sleep, GetTickCount64, CreateThread, InterlockedIncrement,
 * VirtualAlloc, the scalar typedefs -- from one place. On Windows this is a
 * passthrough to <windows.h>; off Windows the toolkit's shim supplies the same
 * names over pthreads and mmap, so the call sites below are written once. */
#include "win32_compat.h"
#include "rsx_commands.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef I2_REQUIRE_BINK_SSE41
#ifdef _MSC_VER
#include <intrin.h>
#else
#include <cpuid.h>
#endif
/* This TU keeps baseline ISA flags. Only the Bink TU can use SSE4.1. */
static bool bink_sse41_supported(void)
{
#ifdef _MSC_VER
    int regs[4];
    __cpuid(regs, 0);
    if (regs[0] < 1) return false;
    __cpuid(regs, 1);
    return (uint32_t(regs[2]) & (1u << 19)) != 0;
#else
    unsigned eax, ebx, ecx, edx;
    return __get_cpuid(1, &eax, &ebx, &ecx, &edx) && (ecx & (1u << 19));
#endif
}
#endif

void ps3_sampler_start(void);                 /* SDK C++ profiler entry */

#ifdef _WIN32
/* timeBeginPeriod: <windows.h> arrives with WIN32_LEAN_AND_MEAN set, which
 * excludes the multimedia timer API, so it is asked for by name -- and after
 * windows.h, since timeapi.h uses UINT. No shim exists off Windows because
 * there is nothing to shim: the 15.6 ms default timer granularity this works
 * around is a Windows problem. */
#include <timeapi.h>
#endif

/* ---------------------------------------------------------------------------
 * What the scaffold gives this file
 * -----------------------------------------------------------------------*/
extern "C" {
uint32_t ppu_load_elf(const char* path);      /* ELF -> guest RAM, returns entry OPD */
void     ppu_recomp_register(void);           /* generated: lifted table -> address map */
void     ppu_hle_init(void);                  /* firmware import NID -> HLE handler */
void     ppu_sysprx_register(void);           /* boot-critical CRT (sys_initialize_tls, ...) */
void     ppu_fs_register(void);               /* cellFs over the real game directory */
void     lv2_init_syscalls(void);             /* the lv2 syscall table */
void     i2_register_bink_sem_stats(void);     /* optional title semaphore observations */
void     i2_register_gcm_flip_stats(void);     /* optional flip context observations */
void     cellGame_init_from_paramsfo(const char* path);
int      ppu_run(uint32_t entry_opd, uint32_t stack_top);

/* This project's own: load whatever real system PRX modules the title needs
 * into guest RAM and register their exports. stubs.cpp has the empty version
 * a game with no lifted PRX wants. */
void     ps3_load_prx_modules(void);

extern const char* ppu_vfs_root;              /* host dir the PS3 mount points map into */

/* The status the guest handed to sys_process_exit. A title that exits that way
 * never returns through ppu_run, so this is only read on the path where the
 * entry function unwound instead. */
extern int     g_sys_process_exit_called;
extern int32_t g_sys_process_exit_code;

/* Host-provided symbols the runtime and the HLE libraries link against. */
uint8_t* vm_base = nullptr;
extern uint32_t ppu_vm_size;                  /* ppu_loader.cpp: the OOB guard */

/* Guest-callback dispatch. g_ps3_guest_caller is the hook the HLE runtime
 * calls back into recompiled code through -- cellSysutil events and the GCM
 * vblank/flip handlers. ppu_guest_call does the OPD -> dispatch. */
typedef void (*ps3_guest_caller_fn)(uint32_t, uint64_t, uint64_t, uint64_t, uint64_t,
                                    uint64_t, uint64_t, uint64_t, uint64_t);
extern ps3_guest_caller_fn g_ps3_guest_caller;
uint64_t ppu_guest_call(uint32_t, uint64_t, uint64_t, uint64_t, uint64_t,
                        uint64_t, uint64_t, uint64_t, uint64_t);

/* The GCM half of the frame clock. */
void     cellGcmTickVBlank(void);
void     cellGcmTickFlip(void);
int      cellGcm_take_flip_pending(void);
void     cellGcm_rsx_process_fifo(void);      /* drain get -> put */
unsigned cellGcm_flip_request_count(void);
}

/* ---------------------------------------------------------------------------
 * RSX present backend
 * -----------------------------------------------------------------------*/
/* D3D12 on Windows, Metal on Apple, and the null backend's headless software
 * path anywhere else -- the same selection runtime/host/host_posix.c and the
 * toolkit's boot harness make. All three expose the same three entry points,
 * so the frame clock below is backend-agnostic. */
#if defined(_WIN32)
extern "C" int  rsx_d3d12_backend_init(uint32_t w, uint32_t h, const char* title);
extern "C" void rsx_d3d12_backend_present(void);
extern "C" int  rsx_d3d12_backend_pump_messages(void);
#  define rsx_backend_init    rsx_d3d12_backend_init
#  define rsx_backend_present rsx_d3d12_backend_present
#  define rsx_backend_pump    rsx_d3d12_backend_pump_messages
#  define RSX_BACKEND_NAME    "D3D12"
#elif defined(__APPLE__)
extern "C" int  rsx_metal_backend_init(uint32_t w, uint32_t h, const char* title);
extern "C" void rsx_metal_backend_present(void);
extern "C" int  rsx_metal_backend_pump_messages(void);
#  define rsx_backend_init    rsx_metal_backend_init
#  define rsx_backend_present rsx_metal_backend_present
#  define rsx_backend_pump    rsx_metal_backend_pump_messages
#  define RSX_BACKEND_NAME    "Metal"
#else
extern "C" int  rsx_null_backend_init(uint32_t w, uint32_t h, const char* title);
extern "C" void rsx_null_backend_present(void);
extern "C" int  rsx_null_backend_pump_messages(void);
#  define rsx_backend_init    rsx_null_backend_init
#  define rsx_backend_present rsx_null_backend_present
#  define rsx_backend_pump    rsx_null_backend_pump_messages
#  define RSX_BACKEND_NAME    "null (headless software)"
#endif

/* ---------------------------------------------------------------------------
 * Configuration
 * -----------------------------------------------------------------------*/
/* The flat VM must span every region the PS3 memory map uses, not just the
 * game image: sys_ppu_thread_create puts thread stacks at 0xD0000000, so a
 * smaller arena makes every spawned thread's stack access out of bounds. */
#define VM_SIZE       0x100010000ull   /* the full 32-bit guest space + a 64K guard */
#define STACK_TOP     0x0FF00000u      /* main-thread stack, below the 0x10000000 segment */
#define WINDOW_WIDTH  1280
#define WINDOW_HEIGHT 720

/* ---------------------------------------------------------------------------
 * Frame clock
 * -----------------------------------------------------------------------*/
/* On real hardware the RSX raises a vblank interrupt about 60 times a second
 * and that is what drives the game's frame loop. With no RSX it is synthesized
 * here: a host thread calls cellGcmTickVBlank/TickFlip, which invoke the
 * handlers the guest registered, and drains the GCM FIFO. Without this the
 * title initialises, registers its handlers and then waits forever.
 *
 * The ticks are driven off real elapsed time rather than off how long present()
 * took. A hidden or occluded window makes present block hard, and pacing the
 * ticks behind it paces the whole game behind it. */
static volatile LONG g_frames_presented = 0;
static bool g_headless = false;
static volatile LONG g_headless_draws = 0;
static volatile LONG g_headless_presents = 0;
static rsx_backend g_headless_backend = {};
static bool g_opt_unlock_fps = false;

#include "wws_stats.h"
#include "light_kicker_stats.h"
#include "spin_dump.h"

extern "C" void cellPad_inject_pulse(uint32_t port_no, uint16_t button_mask, int duration_polls);
extern "C" void cellPad_inject_stick(uint32_t port_no, uint8_t lx, uint8_t ly, uint8_t rx, uint8_t ry, int duration_polls);

static inline void guest_write32(uint64_t addr, uint32_t val)
{
    if (!vm_base) return;
    uint8_t* p = vm_base + addr;
    p[0] = (uint8_t)(val >> 24);
    p[1] = (uint8_t)(val >> 16);
    p[2] = (uint8_t)(val >> 8);
    p[3] = (uint8_t)(val);
}

static inline void guest_write8(uint64_t addr, uint8_t val)
{
    if (!vm_base) return;
    vm_base[addr] = val;
}

static inline void guest_write16(uint64_t addr, uint16_t val)
{
    if (!vm_base) return;
    uint8_t* p = vm_base + addr;
    p[0] = (uint8_t)(val >> 8);
    p[1] = (uint8_t)(val);
}

static inline uint32_t guest_read32(uint64_t addr)
{
    if (!vm_base) return 0;
    const uint8_t* p = vm_base + addr;
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  | ((uint32_t)p[3]);
}

static inline uint16_t guest_read16(uint64_t addr)
{
    if (!vm_base) return 0;
    const uint8_t* p = vm_base + addr;
    return ((uint16_t)p[0] << 8) | ((uint16_t)p[1]);
}

static inline float guest_read_float(uint64_t addr)
{
    uint32_t u = guest_read32(addr);
    float f;
    memcpy(&f, &u, sizeof(f));
    return f;
}

static inline bool is_valid_guest_addr(uint32_t ea, uint32_t size = 4)
{
    if (!vm_base || ea < 0x10000) return false;
    uint64_t limit = ppu_vm_size ? ppu_vm_size : 0x100000000ULL;
    return ((uint64_t)ea + size) < limit;
}

extern "C" void ppu_register_function(uint64_t addr, void (*fn)(ppu_context*));

struct sys_mem_alloc_info {
    int      active;
    uint32_t addr;
    uint32_t size;
    int32_t  container_id;
    uint32_t page_size;
};
#define SYS_MEMORY_ALLOC_MAX 1024
extern "C" sys_mem_alloc_info g_sys_mem_allocs[SYS_MEMORY_ALLOC_MAX];

extern "C" {
volatile uint32_t g_observed_hero_ea = 0;
volatile uint32_t g_observed_state_mgr = 0;
volatile uint32_t g_observed_state = 0;
volatile uint32_t g_observed_camera_ea = 0;
}

extern "C" {
void func_0004A160(ppu_context* ctx);
void func_006BCF80(ppu_context* ctx);
void func_004859E4(ppu_context* ctx);
void func_006C99D0(ppu_context* ctx);
void func_004858A4(ppu_context* ctx);
}

static void hook_func_0004A160(ppu_context* ctx)
{
    uint32_t hero = (uint32_t)ctx->gpr[3];
    g_observed_hero_ea = hero;
    fprintf(stderr, "[CHERO_SPAWN] CHero::CHero() entered for EA: 0x%08X (lr=0x%08X)\n", hero, (uint32_t)ctx->lr);
    func_0004A160(ctx);
    fprintf(stderr, "[CHERO_SPAWN] CHero::CHero() completed. Hero at 0x%08X: pos=(%.2f, %.2f, %.2f)\n",
            hero, guest_read_float(hero + 0x340), guest_read_float(hero + 0x344), guest_read_float(hero + 0x348));
}

static void hook_func_006BCF80(ppu_context* ctx)
{
    fprintf(stderr, "[CHERO_FACTORY] func_006BCF80 entered (lr=0x%08X)\n", (uint32_t)ctx->lr);
    func_006BCF80(ctx);
    uint32_t hero = (uint32_t)ctx->gpr[3];
    g_observed_hero_ea = hero;
    fprintf(stderr, "[CHERO_FACTORY] func_006BCF80 returned hero EA: 0x%08X\n", hero);
}

static void hook_func_004859E4(ppu_context* ctx)
{
    uint32_t mgr = (uint32_t)ctx->gpr[3];
    uint32_t target_state = (uint32_t)ctx->gpr[4];
    uint32_t old_state = is_valid_guest_addr(mgr, 0x200) ? guest_read32(mgr + 0x1F0) : 0xFFFFFFFF;
    g_observed_state_mgr = mgr;
    g_observed_state = target_state;
    fprintf(stderr, "[STATE_CHANGE] func_004859E4: mgr=0x%08X transition %u -> %u (lr=0x%08X)\n",
            mgr, old_state, target_state, (uint32_t)ctx->lr);
    func_004859E4(ctx);
}

static void hook_func_006C99D0(ppu_context* ctx)
{
    fprintf(stderr, "[CAMERA_FACTORY] CCameraDriver factory func_006C99D0 entered\n");
    func_006C99D0(ctx);
    uint32_t cam = (uint32_t)ctx->gpr[3];
    g_observed_camera_ea = cam;
    fprintf(stderr, "[CAMERA_FACTORY] CCameraDriver instance EA: 0x%08X\n", cam);
}

static void hook_func_004858A4(ppu_context* ctx)
{
    static bool s_logged = false;
    if (!s_logged) {
        s_logged = true;
        fprintf(stderr, "[STATE2_LOOP] func_004858A4 entered (State 2 / New Game loader active!)\n");
    }
    func_004858A4(ctx);
}

static inline uint32_t get_game_state(void)
{
    if (!vm_base) return 0xFFFFFFFF;
    if (g_observed_state_mgr && is_valid_guest_addr(g_observed_state_mgr, 0x200)) {
        return guest_read32(g_observed_state_mgr + 0x1F0);
    }
    // 1. Direct read from State Manager (+0x1F0)
    // Slot 0x0096B530 holds ptr to State Manager (default: 0x00B2DF78)
    uint32_t pMgr = guest_read32(0x0096B530);
    if (!pMgr || pMgr == 0xFFFFFFFF) pMgr = 0x00B2DF78;
    if (is_valid_guest_addr(pMgr, 0x200)) {
        uint32_t st = guest_read32(pMgr + 0x1F0);
        if (st <= 10 && st != 0) return st;
    }
    uint32_t st2 = guest_read32(0x00B2E168);
    if (st2 <= 10 && st2 != 0) return st2;
    uint32_t st3 = guest_read32(0x00AC003C);
    if (st3 <= 10 && st3 != 0) return st3;
    uint32_t st4 = guest_read32(0x00ABFE50);
    if (st4 <= 10 && st4 != 0) return st4;
    return 0;
}

/* ---------------------------------------------------------------------------
 * 1. Skip Bink Continuo e Rearmavel (AG-LIVE-PROBE-MONITOR)
 * -----------------------------------------------------------------------*/
static bool s_intro_aborted_or_done = false;

static void i2_poll_skip_intro(void)
{
    static int s_skip = -1;
    if (s_skip < 0) {
        const char* e = getenv("I2_SKIP_INTRO");
        s_skip = (e && strcmp(e, "1") == 0) ? 1 : 0;
    }
    if (!s_skip || !vm_base) return;

    // If the state is already at the catalog, menu, or gameplay (>= 2), the intro videos are over.
    uint32_t state = get_game_state();
    if (state >= 2 && state != 0xFFFFFFFF) {
        s_intro_aborted_or_done = true;
        return;
    }

    // Monitor active Bink pointers: 0x00946498 (Gen 1 / stream ref) and 0x00B2BEE0 (Gen 2 / buffer global)
    uint32_t bink_candidates[4];
    int n_cands = 0;

    uint32_t p1 = guest_read32(0x00946498);
    if (is_valid_guest_addr(p1, 0x100)) bink_candidates[n_cands++] = p1;

    uint32_t p2 = guest_read32(0x00B2BEE0);
    if (is_valid_guest_addr(p2, 0x100) && p2 != p1) bink_candidates[n_cands++] = p2;

    uint32_t p3 = guest_read32(0x00B2BEE0 + 0x5C0);
    if (is_valid_guest_addr(p3, 0x100) && p3 != p1 && p3 != p2) bink_candidates[n_cands++] = p3;

    if (is_valid_guest_addr(0x00B2BEE0, 0x100) && 0x00B2BEE0 != p1 && 0x00B2BEE0 != p2) {
        bink_candidates[n_cands++] = 0x00B2BEE0;
    }

    static uint32_t s_last_signaled_ptr = 0;

    for (int i = 0; i < n_cands; i++) {
        uint32_t ptr = bink_candidates[i];
        if (!is_valid_guest_addr(ptr, 0x100)) continue;

        uint32_t total_frames = guest_read32(ptr + 0x08);
        uint32_t cur_frame = guest_read32(ptr + 0x0C);
        uint32_t played_frame = guest_read32(ptr + 0x460);
        uint32_t flag_20 = guest_read32(ptr + 0x20);
        uint32_t flag_b0 = guest_read32(ptr + 0xB0);

        // If the video (including 30-frame short-intro) has finished all frames,
        // mark s_intro_aborted_or_done = true immediately.
        if (total_frames > 0 && (cur_frame >= total_frames || played_frame >= total_frames)) {
            s_intro_aborted_or_done = true;
        }

        // Only movies with valid headers and >500 frames (v_00_intro_montage_g.bik = 3273 frames)
        // should be aborted. Short videos (splash_america = 352 frames) or unloaded structs (total_frames <= 500)
        // must NEVER be aborted, allowing catalogs and saves to load without hangs.
        if (total_frames <= 500) {
            continue;
        }

        // v_00_intro_montage_g.bik has 3273 frames. It MUST be aborted with EOF signals
        // (+0x20 = 1, +0xB0 = 1) and frame fast-forward once it reaches at least 30 frames
        // (cur_frame >= 30 || played_frame >= 30), releasing transition into
        // States 2 and 3 (Cole MacGrath at the Empire City docks).
        bool ready_to_abort = (cur_frame >= 30 || played_frame >= 30);
        if (!ready_to_abort) {
            continue;
        }

        // Avoid re-signaling repeatedly in the per-frame polling loop
        static uint32_t s_signaled_ptr = 0;
        if (s_signaled_ptr == ptr) {
            continue;
        }
        s_signaled_ptr = ptr;
        s_intro_aborted_or_done = true;

        // 1. Signal EOF and fast-forward directly on the HBINK structure
        guest_write32(ptr + 0x0C, total_frames);
        guest_write32(ptr + 0x460, total_frames);
        guest_write32(ptr + 0x20, 1);
        guest_write32(ptr + 0xB0, 1);

        // 2. Locate and signal active player nodes in manager (SP Movie Player at 0x00A087F0)
        uint32_t mgr_cands[2] = { 0x00A087F0, guest_read32(0x00A087F0) };
        for (int m = 0; m < 2; m++) {
            uint32_t mgr = mgr_cands[m];
            if (!is_valid_guest_addr(mgr, 0x100)) continue;

            // 2a. Movie nodes array in mgr + 0x40 (nodes_ptr) and mgr + 0x44 (nodes_count)
            uint32_t nodes_ptr = guest_read32(mgr + 0x40);
            uint32_t nodes_count = guest_read32(mgr + 0x44);
            if (is_valid_guest_addr(nodes_ptr, 4) && nodes_count > 0 && nodes_count < 64) {
                for (uint32_t c = 0; c < nodes_count; c++) {
                    uint32_t node = guest_read32(nodes_ptr + (c * 4));
                    if (is_valid_guest_addr(node, 0x60)) {
                        uint32_t nhbink = guest_read32(node + 0x44);
                        if (nhbink == ptr) {
                            guest_write8(node + 0x37, 1); // abort flag (func_001D18D8)
                            guest_write8(node + 0x31, 1); // stop flag
                            guest_write32(node + 0x28, 3); // movie state: finishing (func_001D25C0)
                            fprintf(stderr, "[I2_SKIP_INTRO] Signaled abort to movie node array: 0x%08X (hbink=0x%08X)\n", node, nhbink);
                        }
                    }
                }
            }

            // 2b. Linked lists in mgr + 0x50, 0x58, 0x60, 0x68, 0x70
            const uint32_t list_offsets[] = { 0x50, 0x58, 0x60, 0x68, 0x70 };
            for (uint32_t off : list_offsets) {
                uint32_t node = guest_read32(mgr + off);
                int guard = 0;
                while (node && is_valid_guest_addr(node, 0x60) && guard++ < 32) {
                    uint32_t nhbink = guest_read32(node + 0x44);
                    if (nhbink == ptr) {
                        guest_write8(node + 0x37, 1); // abort flag
                        guest_write8(node + 0x31, 1); // stop flag
                        guest_write32(node + 0x28, 3); // movie state: finishing
                        fprintf(stderr, "[I2_SKIP_INTRO] Signaled abort to movie node list: 0x%08X (hbink=0x%08X)\n", node, nhbink);
                    }
                    node = guest_read32(node + 0x14);
                }
            }
        }

        fprintf(stderr, "[I2_SKIP_INTRO] Signaled EOF/abort to active Bink Movie at 0x%08X (total_frames=%u, cur_frame=%u, played=%u)\n",
                ptr, total_frames, cur_frame, played_frame);
    }
}

/* ---------------------------------------------------------------------------
 * Active Telemetry for Cole MacGrath and Camera (AG-COLE-TELEMETRY)
 * -----------------------------------------------------------------------*/
struct ColeTelemetryData {
    bool valid;
    float x, y, z;
    float qx, qy, qz, qw;
    float hp, max_hp;
    float energy;
    float cam_x, cam_y, cam_z;
};
static ColeTelemetryData g_cole_telemetry = {false};

/* ---------------------------------------------------------------------------
 * 2. Automated 'New Game' Injector (State 2 -> State 3, AG-AUTO-TRANSITION)
 * -----------------------------------------------------------------------*/
static void i2_poll_auto_newgame(void)
{
    static int s_auto_start = -1;
    static uint32_t s_btn_mask = 0x4008; // START (0x0008)
    static uint64_t s_delay_ms = 500;    // Debounce / settling window (~500ms / 30 frames)
    static ULONGLONG s_boot_time = 0;
    static ULONGLONG s_intro_done_time = 0;
    static ULONGLONG s_step_time = 0;
    static int s_nav_step = 0;
    static int s_retry_count = 0;
    static bool s_transition_completed = false;

    if (s_auto_start < 0) {
        const char* e = getenv("I2_AUTO_START");
        if (!e) e = getenv("I2_AUTO_NEWGAME");
        s_auto_start = (e && strcmp(e, "1") == 0) ? 1 : 0;
        const char* btn = getenv("I2_AUTO_START_BTN");
        if (btn) s_btn_mask = (uint32_t)strtoul(btn, NULL, 0);
        const char* del = getenv("I2_AUTO_START_DELAY_MS");
        if (del) s_delay_ms = (uint64_t)strtoull(del, NULL, 0);
    }

    if (!s_auto_start || !vm_base || s_transition_completed) return;

    ULONGLONG now = GetTickCount64();
    if (s_boot_time == 0) s_boot_time = now;

    uint32_t current_state = get_game_state();

    // When Cole is spawned or State transitions to 3+ (Campaign active)
    if (g_cole_telemetry.valid || (current_state >= 3 && current_state != 0xFFFFFFFF)) {
        if (!s_transition_completed) {
            s_transition_completed = true;
            fprintf(stderr, "[PORT_TRANSITION] Campaign active (Cole valid=%d, state=%u) -> New Game running\n",
                    g_cole_telemetry.valid ? 1 : 0, current_state);
        }
        return;
    }

    // Intro finishes when:
    // (1) current_state >= 2 (Interactive Main Menu in func_004858A4)
    // (2) Elapsed time >= 230s from boot (boot init ~217s + Bink teardown at ~220s, Title Screen active)
    bool intro_finished = (current_state >= 2 && current_state != 0xFFFFFFFF) ||
                          (now - s_boot_time >= 230000);

    if (intro_finished) {
        if (s_intro_done_time == 0) {
            s_intro_done_time = now;
            s_step_time = now;
            s_nav_step = 1;
            fprintf(stderr, "[PORT_TRANSITION] Intro finished (state=%u, t=%.1fs) -> Entering Menu Navigation FSM\n",
                    current_state, (double)(now - s_boot_time) / 1000.0);
        }

        switch (s_nav_step) {
            case 1: { // Step 1: Clear Title Screen ("Press START button")
                if (now - s_step_time >= s_delay_ms) {
                    fprintf(stderr, "[PORT_TRANSITION] Step 1: Clearing Title Screen with START (mask=0x%04X, t=%.1fs)\n",
                            s_btn_mask, (double)(now - s_boot_time) / 1000.0);
                    cellPad_inject_pulse(0, s_btn_mask, 30);
                    s_step_time = now;
                    s_nav_step = 2;
                }
                break;
            }
            case 2: { // Step 2: Await Main Menu animation and select "New Game" with CROSS
                if (now - s_step_time >= 3500) {
                    fprintf(stderr, "[PORT_TRANSITION] Step 2: Selecting 'New Game' with CROSS (0x4000, t=%.1fs)\n",
                            (double)(now - s_boot_time) / 1000.0);
                    cellPad_inject_pulse(0, 0x4000, 20);
                    s_step_time = now;
                    s_nav_step = 3;
                }
                break;
            }
            case 3: { // Step 3: Await Difficulty prompt ("Normal") and confirm with CROSS
                if (now - s_step_time >= 2500) {
                    fprintf(stderr, "[PORT_TRANSITION] Step 3: Confirming Difficulty with CROSS (0x4000, t=%.1fs)\n",
                            (double)(now - s_boot_time) / 1000.0);
                    cellPad_inject_pulse(0, 0x4000, 20);
                    s_step_time = now;
                    s_nav_step = 4;
                }
                break;
            }
            case 4: { // Step 4: Confirm secondary prologue disclaimer with CROSS
                if (now - s_step_time >= 2500) {
                    fprintf(stderr, "[PORT_TRANSITION] Step 4: Confirming secondary prompt with CROSS (0x4000, t=%.1fs)\n",
                            (double)(now - s_boot_time) / 1000.0);
                    cellPad_inject_pulse(0, 0x4000, 20);
                    s_step_time = now;
                    s_nav_step = 5;
                }
                break;
            }
            case 5: { // Step 5: Advance tutorial / cutscene until Cole spawns
                if (now - s_step_time >= 1500) {
                    s_step_time = now;
                    s_retry_count++;

                    int tut_phase = s_retry_count % 8;
                    uint8_t lx = 128, ly = 128, rx = 128, ry = 128;
                    uint16_t btn = 0x4000;

                    switch (tut_phase) {
                        case 0: rx = 128; ry = 0;   btn = 0x4000; break;
                        case 1: rx = 0;   ry = 128; btn = 0x4000; break;
                        case 2: rx = 255; ry = 128; btn = 0x4000; break;
                        case 3: rx = 128; ry = 255; btn = 0x4000; break;
                        case 4: ly = 0;             btn = 0x4000; break;
                        case 5: ly = 0;             btn = 0x0400; break;
                        case 6: ly = 0; rx = 128; ry = 0; btn = 0x0800; break;
                        case 7: ly = 0;             btn = 0x4000; break;
                    }
                    cellPad_inject_stick(0, lx, ly, rx, ry, 30);
                    cellPad_inject_pulse(0, btn, 25);
                    if ((s_retry_count % 10) == 0) {
                        fprintf(stderr, "[PORT_TRANSITION] Step 5: Tutorial/Campaign advance (retry=%d, phase=%d, btn=0x%04X, lx=%u ly=%u rx=%u ry=%u, t=%.1fs)\n",
                                s_retry_count, tut_phase, btn, lx, ly, rx, ry, (double)(now - s_boot_time) / 1000.0);
                    }
                }
                break;
            }
            default:
                break;
        }
    }
}

/* ---------------------------------------------------------------------------
 * 3. Active Telemetry for Cole MacGrath and Camera (AG-COLE-TELEMETRY)
 * -----------------------------------------------------------------------*/
static void i2_poll_cole_telemetry(void)
{
    static ULONGLONG s_last_poll = 0;
    ULONGLONG now = GetTickCount64();

    // Poll every 1 second (~60 frames)
    if (now - s_last_poll < 1000 || !vm_base) return;
    s_last_poll = now;

    uint32_t state = get_game_state();

    // 1. Resolve Cole MacGrath pointer (CHero primary vtable: 0x008D6418, LE host: 0x18648D00, typeId: 0x0B)
    auto is_hero_cand = [](uint32_t cand) -> bool {
        if (!is_valid_guest_addr(cand, 0x350)) return false;
        uint32_t vt = guest_read32(cand);
        if (vt == 0x008D6418 || vt == 0x008D63E0 || vt == 0x008D65D8 || vt == 0x008D65BC) return true;
        if (guest_read32(cand + 0x04) == 0x0B) return true;
        return false;
    };

    static uint32_t s_cached_hero_ea = 0;
    uint32_t hero_ea = 0;

    if (g_observed_hero_ea && is_valid_guest_addr(g_observed_hero_ea, 0x350)) {
        hero_ea = g_observed_hero_ea;
        s_cached_hero_ea = g_observed_hero_ea;
    }

    if (hero_ea == 0 && s_cached_hero_ea && is_valid_guest_addr(s_cached_hero_ea, 0x350)) {
        if (is_hero_cand(s_cached_hero_ea)) {
            hero_ea = s_cached_hero_ea;
        } else {
            s_cached_hero_ea = 0;
        }
    }

    uint32_t pCameraDriver = guest_read32(0x00ABFC64);
    uint32_t pWorld = guest_read32(0x00ABFC68);
    uint32_t pWorld2 = guest_read32(0x0096B528);

    // 2. Chain A: CWorld (0x009BD600 / 0x0096B528 / 0x00B006B8) or CWorld Context (+0x0C)
    const uint32_t world_ptrs[] = { 0x009BD600, pWorld2, 0x00B006B8, pWorld };
    for (int w = 0; w < 4 && hero_ea == 0; ++w) {
        uint32_t pW = world_ptrs[w];
        if (!is_valid_guest_addr(pW, 0x400)) continue;

        // Direct scan of first 0x400 bytes of CWorld
        for (uint32_t off = 0; off <= 0x3F0; off += 4) {
            uint32_t cand = guest_read32(pW + off);
            if (is_hero_cand(cand)) {
                hero_ea = cand;
                s_cached_hero_ea = cand;
                fprintf(stderr, "[COLE_TELEMETRY] Resolved CHero via CWorld (0x%08X + 0x%X -> 0x%08X)\n", pW, off, cand);
                break;
            }
        }

        // If not found in body, check context at +0x0C
        if (hero_ea == 0) {
            uint32_t pWorldCtx = guest_read32(pW + 0x0C);
            if (is_valid_guest_addr(pWorldCtx, 0x400)) {
                for (uint32_t off = 0; off <= 0x3FF0; off += 4) {
                    if (!is_valid_guest_addr(pWorldCtx + off, 4)) break;
                    uint32_t ctx_cand = guest_read32(pWorldCtx + off);
                    if (is_hero_cand(ctx_cand)) {
                        hero_ea = ctx_cand;
                        s_cached_hero_ea = ctx_cand;
                        fprintf(stderr, "[COLE_TELEMETRY] Resolved CHero via CWorld Context (0x%08X + 0x%X -> 0x%08X)\n",
                                pWorldCtx, off, ctx_cand);
                        break;
                    }
                }
            }
        }
    }

    // 3. Chain B: CCameraDriver (0x00ABFC64) -> +0x2E0
    if (hero_ea == 0 && is_valid_guest_addr(pCameraDriver, 0x2E4)) {
        uint32_t cand = guest_read32(pCameraDriver + 0x2E0);
        if (is_hero_cand(cand)) {
            hero_ea = cand;
            s_cached_hero_ea = cand;
            fprintf(stderr, "[COLE_TELEMETRY] Resolved CHero via CCameraDriver (0x00ABFC64 -> 0x%08X)\n", cand);
        }
    }

    // 4. Chain C: Scan game memory allocation blocks
    if (hero_ea == 0) {
        for (int i = 0; i < SYS_MEMORY_ALLOC_MAX && hero_ea == 0; ++i) {
            if (!g_sys_mem_allocs[i].active) continue;
            uint32_t base = g_sys_mem_allocs[i].addr;
            uint32_t size = g_sys_mem_allocs[i].size;
            if (size > 0x400000) size = 0x400000;
            for (uint32_t off = 0; off + 0x350 <= size; off += 0x10) {
                uint32_t cand = base + off;
                if (guest_read32(cand) == 0x008D6418) {
                    hero_ea = cand;
                    s_cached_hero_ea = cand;
                    fprintf(stderr, "[COLE_TELEMETRY] Resolved CHero via Memory Scan (0x%08X + 0x%X -> 0x%08X)\n", base, off, cand);
                    break;
                }
            }
        }
    }



    // Read 3rd person camera CView (0x00ABFC6C -> +0xE0)
    float cam_x = 0.0f, cam_y = 0.0f, cam_z = 0.0f;
    uint32_t pView = guest_read32(0x00ABFC6C);
    if (is_valid_guest_addr(pView, 0xF0) && guest_read32(pView + 0x04) == 0x000000F0) {
        cam_x = guest_read_float(pView + 0xE0);
        cam_y = guest_read_float(pView + 0xE4);
        cam_z = guest_read_float(pView + 0xE8);
    }
    g_cole_telemetry.cam_x = cam_x;
    g_cole_telemetry.cam_y = cam_y;
    g_cole_telemetry.cam_z = cam_z;

    if (hero_ea == 0) {
        g_cole_telemetry.valid = false;
        static ULONGLONG s_last_spawn_log = 0;
        if (now - s_last_spawn_log >= 10000) {
            fprintf(stderr, "[COLE_TELEMETRY] state=%u pCameraDriver=0x%08X pWorld=0x%08X pView=0x%08X hero_ea=0x%08X (spawning hero entity...)\n",
                    state, pCameraDriver, pWorld, pView, hero_ea);
            s_last_spawn_log = now;
        }
        return;
    }

    // Read Cole coordinates (+0x340, +0x344, +0x348)
    float x = guest_read_float(hero_ea + 0x340);
    float y = guest_read_float(hero_ea + 0x344);
    float z = guest_read_float(hero_ea + 0x348);

    g_cole_telemetry.x = x;
    g_cole_telemetry.y = y;
    g_cole_telemetry.z = z;
    g_cole_telemetry.qx = guest_read_float(hero_ea + 0x360);
    g_cole_telemetry.qy = guest_read_float(hero_ea + 0x364);
    g_cole_telemetry.qz = guest_read_float(hero_ea + 0x368);
    g_cole_telemetry.qw = guest_read_float(hero_ea + 0x36C);

    // Read HUD meters (Health and Energy)
    uint32_t pHp = guest_read32(hero_ea + 0x30C);
    if (is_valid_guest_addr(pHp, 0x350) && guest_read32(pHp + 0x04) == 0x51) {
        g_cole_telemetry.hp = guest_read_float(pHp + 0x340);
        g_cole_telemetry.max_hp = guest_read_float(pHp + 0x300);
    } else {
        g_cole_telemetry.hp = 100.0f;
        g_cole_telemetry.max_hp = 100.0f;
    }

    uint32_t pNrg = guest_read32(hero_ea + 0x1ED8);
    if (is_valid_guest_addr(pNrg, 0x350) && guest_read32(pNrg + 0x04) == 0x52) {
        g_cole_telemetry.energy = guest_read_float(pNrg + 0x340);
    } else {
        g_cole_telemetry.energy = 0.0f;
    }

    g_cole_telemetry.valid = true;

    // Explicit print per specification: [COLE_POS] X=%.3f Y=%.3f Z=%.3f
    printf("[COLE_POS] X=%.3f Y=%.3f Z=%.3f\n", x, y, z);
    fflush(stdout);
    fprintf(stderr, "[COLE_POS] X=%.3f Y=%.3f Z=%.3f\n", x, y, z);

    // Active movement of Cole MacGrath in 3D level
    cellPad_inject_stick(0, 128, 0, 128, 128, 40);
    static int s_jump_counter = 0;
    if (++s_jump_counter % 4 == 0) {
        cellPad_inject_pulse(0, 0x4000, 15);
    }
}

/* ---------------------------------------------------------------------------
 * 4. WWS / Light Kicker queue resolution and synchronization (AG-WWS-BARRIER-PUMP)
 * -----------------------------------------------------------------------*/
static void i2_poll_wws_sync(void)
{
    if (!vm_base) return;

    // 2. SPU Light Task Kicker Fence Resolution (0x00A25700 / 0x00A25800)
    // When the kicker is in state 3 with an active fence (total > 0) and all
    // reserved SPU tasks have completed, debounce FIFO draining, close the
    // fence with 0xFFFF, and advance to state 4.
    static int s_st3_spins = 0;
    if (is_valid_guest_addr(0x00A25A18, 4) && is_valid_guest_addr(0x00A25800, 8)) {
        uint32_t lk_st = guest_read32(0x00A25A18);
        if (lk_st == 3) {
            uint16_t phase = guest_read16(0x00A25800);
            uint16_t total = guest_read16(0x00A25806);
            uint16_t reserved = guest_read16(0x00A25802);
            uint16_t completed = guest_read16(0x00A25804);
            if (phase != 0xFFFF && total > 0 && completed >= reserved) {
                if (++s_st3_spins > 32) {
                    guest_write16(0x00A25800, 0xFFFF);
                    guest_write32(0x00A25A18, 4);
                    s_st3_spins = 0;
                }
            }
        } else {
            s_st3_spins = 0;
        }
    }
    // Diagnostic A/B only: leave the Light Kicker fence logic above intact.
    // Exact value 1 disables this runner's guest lane writes; default unchanged.
    static const bool disable_wws_lane_pump = [] {
        const char* option = getenv("I2_DISABLE_WWS_LANE_PUMP");
        const bool disabled = option && strcmp(option, "1") == 0;
        if (disabled) fprintf(stderr, "[i2-wws] runner lane pump disabled for diagnostic\n");
        return disabled;
    }();
    if (disable_wws_lane_pump) return;

    static const uint32_t s_wws_queues[] = {
        0x00A93900, 0x00A93A80, 0x00A93C00, 0x00A93D80,
        0x00A93F00, 0x00A94080, 0x00A94200, 0x00A94980,
        0x00A94B00, 0x00A94C80
    };

    for (uint32_t q : s_wws_queues) {
        if (!is_valid_guest_addr(q, 0x100)) continue;

        for (uint32_t buf = 0; buf < 2; buf++) {
            uint32_t ticket_ea = q + 0x40 + buf * 16;
            if (!is_valid_guest_addr(ticket_ea, 16)) continue;

            uint16_t ticket = guest_read16(ticket_ea);
            uint32_t list = guest_read32(q + 0x30 + buf * 4);

            if (is_valid_guest_addr(list, 8) && ticket != 0xFFFF) {
                uint16_t count = 0;
                while (count < 32 && is_valid_guest_addr(list + (uint32_t)(ticket + count) * 8 + 2, 2)) {
                    uint16_t entry = guest_read16(list + (uint32_t)(ticket + count) * 8 + 2);
                    if (entry == 0) break;
                    count++;
                }
                uint16_t target = (uint16_t)(ticket + count);
                bool any_lane_active = false;
                for (int l = 0; l < 7; l++) {
                    uint16_t lane_val = guest_read16(ticket_ea + 2 + l * 2);
                    if (lane_val != 0xFFFF) {
                        any_lane_active = true;
                        if (lane_val < target) {
                            guest_write16(ticket_ea + 2 + l * 2, target);
                        }
                    }
                }
                if (!any_lane_active && count > 0) {
                    guest_write16(ticket_ea + 2, target);
                }
            }
        }
    }


}

static void headless_draw_arrays(void*, uint32_t, uint32_t, uint32_t)
{
    LONG count = InterlockedIncrement(&g_headless_draws);
    if (count <= 10 || count % 100 == 0)
        fprintf(stderr, "[headless] draw #%ld\n", (long)count);
}

static void headless_draw_indexed(void*, uint32_t, uint32_t, uint32_t)
{
    LONG count = InterlockedIncrement(&g_headless_draws);
    if (count <= 10 || count % 100 == 0)
        fprintf(stderr, "[headless] draw #%ld\n", (long)count);
}

static void headless_present(void*, uint32_t)
{
    LONG count = InterlockedIncrement(&g_headless_presents);
    if (count <= 10) fprintf(stderr, "[headless] present #%ld\n", (long)count);
}

static void backend_present(void)
{
    if (g_headless) headless_present(nullptr, 0);
    else rsx_backend_present();
}

static void write_telemetry(int backend_ready)
{
    const char* path = getenv("I2_TELEMETRY_PATH");
    if (!path || !*path) return;
    char pending[1200];
    if (snprintf(pending, sizeof pending, "%s.tmp", path) >= (int)sizeof pending)
        return;
    FILE* out = fopen(pending, "wb");
    if (!out) return;
    uint32_t current_state = get_game_state();
    fprintf(out,
            "{\"headless\":%s,\"backend_ready\":%s,\"draws\":%ld,"
            "\"guest_frames\":%ld,\"backend_presents\":%ld,"
            "\"state\":%u,\"hero\":{\"valid\":%s,\"x\":%.3f,\"y\":%.3f,\"z\":%.3f,"
            "\"hp\":%.1f,\"energy\":%.1f},"
            "\"camera\":{\"x\":%.3f,\"y\":%.3f,\"z\":%.3f}}\n",
            g_headless ? "true" : "false", backend_ready ? "true" : "false",
            (long)g_headless_draws, (long)g_frames_presented,
            (long)g_headless_presents, current_state,
            g_cole_telemetry.valid ? "true" : "false",
            g_cole_telemetry.x, g_cole_telemetry.y, g_cole_telemetry.z,
            g_cole_telemetry.hp, g_cole_telemetry.energy,
            g_cole_telemetry.cam_x, g_cole_telemetry.cam_y, g_cole_telemetry.cam_z);
    fclose(out);
#ifdef _WIN32
    MoveFileExA(pending, path, MOVEFILE_REPLACE_EXISTING);
#else
    rename(pending, path);
#endif
}

/* Frames handed to the backend at a guest FLIP boundary -- one per frame the
 * guest actually finished. The presents made before the guest's first flip, so
 * a fresh window is not left blank through a long boot, carry no guest frame
 * and are deliberately not counted. */
extern "C" unsigned ppu_boot_frames_presented(void)
{
    return (unsigned)g_frames_presented;
}

static void present_guest_frame(void)
{
    backend_present();
    /* This thread increments and guest threads read, so interlocked rather
     * than a volatile ++, which on arm64 is neither atomic nor a fence. */
    LONG count = InterlockedIncrement(&g_frames_presented);
    if (g_headless && (count <= 10 || count % 100 == 0))
        fprintf(stderr, "[headless] guest frame #%ld\n", (long)count);
}

static DWORD WINAPI frame_clock(LPVOID)
{
    const char* title = getenv("PS3_TITLE");
    if (!title || !*title) title = "ps3recomp";

    int rsx_ok;
    if (g_headless) {
        g_headless_backend.draw_arrays = headless_draw_arrays;
        g_headless_backend.draw_indexed = headless_draw_indexed;
        g_headless_backend.present = headless_present;
        rsx_set_backend(&g_headless_backend);
        rsx_ok = 1;
        fprintf(stderr, "[rsx] headless diagnostic backend init OK -- no window\n");
    } else {
        rsx_ok = (rsx_backend_init(WINDOW_WIDTH, WINDOW_HEIGHT, title) == 0);
        fprintf(stderr, "[rsx] %s backend init %s\n", RSX_BACKEND_NAME,
                rsx_ok ? "OK -- window open" : "FAILED");
    }

    ULONGLONG next_tick = GetTickCount64();
    ULONGLONG last_telemetry = 0;
    const char* fifo_backoff = getenv("I2_FIFO_BACKOFF_MS");
    const bool experiment_fifo_backoff = fifo_backoff && strcmp(fifo_backoff, "1") == 0;
    write_telemetry(rsx_ok);

    for (;;) {
        uint32_t cur_st = get_game_state();

        /* Monitora o SPU Light Task Kicker (0x00A25A18) e Fences RSX GCM */
        uint32_t lk_state = is_valid_guest_addr(0x00A25A18) ? guest_read32(0x00A25A18) : 0;
        uint32_t counter0_ea = is_valid_guest_addr(0x00A25884) ? guest_read32(0x00A25884) : 0;
        uint32_t ticket0 = is_valid_guest_addr(0x00A25890) ? guest_read32(0x00A25890) : 0;
        uint32_t counter0 = (counter0_ea && is_valid_guest_addr(counter0_ea)) ? guest_read32(counter0_ea) : 0;

        bool kicker_fence_wait = (counter0_ea != 0) && (counter0 < ticket0);
        bool kicker_busy = (lk_state == 3) || kicker_fence_wait;

        /* Poll busy FIFO/fences promptly; this affects drain cadence only. */
        bool fast_pacing = g_opt_unlock_fps || cur_st < 2 || cur_st == 0xFFFFFFFF || kicker_busy;
        if (fast_pacing) {
            if (experiment_fifo_backoff) Sleep(1);
            else SwitchToThread();
        } else {
            Sleep(4);
        }

        /* Drain busy FIFO/fences without waiting for the next video tick. */
        if (rsx_ok && kicker_busy) {
            /* A FIFO drain stops at a completed flip. Deliver that batch
             * before the next drain can append commands from another frame,
             * including a flip already pending when the burst starts. */
            if (cellGcm_take_flip_pending()) present_guest_frame();
            int drain_count = 0;
            while (drain_count++ < 16) {
                cellGcm_rsx_process_fifo();
                if (cellGcm_take_flip_pending()) present_guest_frame();
                lk_state = is_valid_guest_addr(0x00A25A18) ? guest_read32(0x00A25A18) : 0;
                counter0 = (counter0_ea && is_valid_guest_addr(counter0_ea)) ? guest_read32(counter0_ea) : 0;
                if (lk_state == 4 || (counter0_ea && counter0 >= ticket0)) break;
                SwitchToThread();
            }
        }

        i2_poll_skip_intro();
        i2_poll_auto_newgame();
        i2_poll_cole_telemetry();
        i2_poll_wws_sync();
        ULONGLONG now = GetTickCount64();

        int fired = 0;
        while ((long long)(now - next_tick) >= 0 && fired < 240) {
            cellGcmTickVBlank();
            cellGcmTickFlip();
            /* Present a pending flip BEFORE draining any further. The flip
             * fires at a get==put frame boundary on the guest thread, so the
             * batch held right now is exactly the completed frame; presenting
             * after the drain races the guest's next-frame writes and shows a
             * mixed one. */
            if (rsx_ok && cellGcm_take_flip_pending()) {
                present_guest_frame();
            }
            /* Drain the FIFO every tick. This is what writes the RSX sync-fence
             * labels the game's per-frame logic blocks on, so it has to keep
             * advancing at 60 Hz even while present() throttles. */
            if (rsx_ok) cellGcm_rsx_process_fifo();
            next_tick += 16;             /* ~60 Hz */
            fired++;
        }
        if (fired >= 240) next_tick = now + 16; /* resync after bounded catch-up */

        /* Drain at the outer cadence too, not only on the 16 ms tick: titles
         * fence every render pass on a label the drain writes, and one wave of
         * those at 16 ms apiece paces the guest into single-figure frame rates.
         * The real RSX writes them in microseconds. */
        if (rsx_ok) {
            if (cellGcm_take_flip_pending()) {
                present_guest_frame();
            }
            cellGcm_rsx_process_fifo();

            if (!g_headless && rsx_backend_pump() != 0) {
                rsx_ok = 0;              /* window closed */
                continue;
            }
            /* Present on a guest flip. A present on a fixed clock can catch the
             * drain mid-frame and flash a partial one. Before the first flip
             * present freely, so the window is not blank during boot. */
            /* The FIFO decoder increments the request count and sets pending
             * together. Consume pending here too: presenting from the count
             * alone would leave that same flip to be counted again later. */
            if (cellGcm_take_flip_pending()) {
                present_guest_frame();
            } else if (cellGcm_flip_request_count() == 0) {
                backend_present();
            }
        }
        if (now - last_telemetry >= 500) {
            write_telemetry(rsx_ok);
            last_telemetry = now;
        }
    }
    return 0;
}

/* ---------------------------------------------------------------------------
 * VFS root
 * -----------------------------------------------------------------------*/
/* Derive the directory holding PS3_GAME from the EBOOT path
 * <root>/PS3_GAME/USRDIR/EBOOT.elf -> <root>. $PS3_VFS_ROOT overrides. */
static char s_vfs_root[1024];

static void derive_vfs_root(const char* eboot)
{
    const char* env = getenv("PS3_VFS_ROOT");
    if (env && *env) { ppu_vfs_root = env; return; }

    strncpy(s_vfs_root, eboot, sizeof s_vfs_root - 1);
    for (char* p = s_vfs_root; *p; p++) if (*p == '\\') *p = '/';
    /* strip EBOOT.elf, USRDIR and PS3_GAME */
    for (int i = 0; i < 3; i++) { char* s = strrchr(s_vfs_root, '/'); if (s) *s = 0; }
    if (!s_vfs_root[0]) strcpy(s_vfs_root, ".");
    ppu_vfs_root = s_vfs_root;
}

/* ---------------------------------------------------------------------------
 * Guest VM
 * -----------------------------------------------------------------------*/
#ifdef _WIN32
/* Demand-paging for the flat VM: reserve the whole 4 GB guest space, which
 * costs no commit, and commit each 64 KB page on first access. Every 32-bit
 * guest offset is then valid, so a garbage guest pointer reads zero instead of
 * killing the process. Faults outside the arena fall through untouched. */
static LONG WINAPI vm_commit_veh(EXCEPTION_POINTERS* ep)
{
    if (ep->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        ULONG_PTR fault = ep->ExceptionRecord->ExceptionInformation[1];
        uintptr_t base  = (uintptr_t)vm_base;
        if (vm_base && fault >= base && fault < base + VM_SIZE) {
            void* page = (void*)(fault & ~(uintptr_t)0xFFFF);
            if (VirtualAlloc(page, 0x10000, MEM_COMMIT, PAGE_READWRITE))
                return EXCEPTION_CONTINUE_EXECUTION;
        }
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif

static bool alloc_guest_vm(void)
{
#ifdef _WIN32
    AddVectoredExceptionHandler(1, vm_commit_veh);
    vm_base = (uint8_t*)VirtualAlloc(NULL, VM_SIZE, MEM_RESERVE, PAGE_READWRITE);
    ppu_vm_size = 0;              /* the whole space is backed; no OOB guard needed */
#else
    /* No vectored exception handlers off Windows, so the arena is committed up
     * front and lazily backed by the OS: only pages the title touches cost
     * anything. It stops below the 0xE0000000 mark, so ppu_vm_size arms the
     * loader's bounds check for what is left. */
    vm_base = (uint8_t*)calloc(1, 0xE0000000u);
    ppu_vm_size = 0xE0000000u;
#endif
    return vm_base != nullptr;
}

/* ---------------------------------------------------------------------------
 * Main
 * -----------------------------------------------------------------------*/
static void harness_guest_caller(uint32_t opd, uint64_t a0, uint64_t a1,
                                 uint64_t a2, uint64_t a3, uint64_t a4,
                                 uint64_t a5, uint64_t a6, uint64_t a7)
{
    ppu_guest_call(opd, a0, a1, a2, a3, a4, a5, a6, a7);
}

int main(int argc, char** argv)
{
    if (argc < 2) {
        printf("usage: %s <PPU ELF>\n", argv[0]);
        return 2;
    }

#ifdef I2_REQUIRE_BINK_SSE41
    if (!bink_sse41_supported()) {
        fprintf(stderr, "ERROR: this experimental Bink build requires SSE4.1; rebuild with PORT_BINK_SSE41=OFF\n");
        return 1;
    }
#endif

#ifdef _WIN32
#pragma comment(lib, "winmm.lib")
    /* 1 ms timer resolution. The default granularity is about 15.6 ms, which
     * inflates every shorter wait the title makes and throttles the whole
     * thing. POSIX timers are already fine-grained. */
    timeBeginPeriod(1);
    setvbuf(stdout, NULL, _IONBF, 0);   /* unbuffered: do not lose prints on a kill */
#endif

    printf("=== inFAMOUS 2 BCUS98125 native port experiment ===\n");
    const char* headless = getenv("I2_HEADLESS");
    g_headless = headless && strcmp(headless, "1") == 0;

    if (!alloc_guest_vm()) {
        fprintf(stderr, "ERROR: could not allocate the guest address space\n");
        return 1;
    }

    uint32_t entry = ppu_load_elf(argv[1]);
    if (!entry) {
        fprintf(stderr, "ERROR: could not load %s\n", argv[1]);
        return 1;
    }

    /* Optional EBOOT patches and test acceleration */
    const char* env_skip_intro = getenv("I2_SKIP_INTRO");
    bool opt_skip_intro = env_skip_intro && strcmp(env_skip_intro, "1") == 0;
    const char* env_mesh_trim = getenv("I2_DISABLE_MESH_TRIMMING");
    bool opt_mesh_trim = env_mesh_trim && strcmp(env_mesh_trim, "1") == 0;
    const char* env_unlock_fps = getenv("I2_UNLOCK_FPS");
    bool opt_unlock_fps = env_unlock_fps && strcmp(env_unlock_fps, "1") == 0;
    const char* env_auto_newgame = getenv("I2_AUTO_START");
    if (!env_auto_newgame) env_auto_newgame = getenv("I2_AUTO_NEWGAME");
    bool opt_auto_newgame = env_auto_newgame && strcmp(env_auto_newgame, "1") == 0;

    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--skip-intro") == 0) opt_skip_intro = true;
        if (strcmp(argv[i], "--disable-mesh-trimming") == 0) opt_mesh_trim = true;
        if (strcmp(argv[i], "--unlock-fps") == 0) opt_unlock_fps = true;
        if (strcmp(argv[i], "--auto-start") == 0 || strcmp(argv[i], "--auto-newgame") == 0) opt_auto_newgame = true;
        if (strcmp(argv[i], "--no-auto-start") == 0 || strcmp(argv[i], "--no-auto-newgame") == 0) opt_auto_newgame = false;
    }

    if (opt_skip_intro) {
        _putenv("I2_SKIP_INTRO=1");
        guest_write32(0x0078B4F8, 0x48000010);
        printf("[patch] Skip Intro EOF ativado via func_001D5AE4 e 0x0078B4F8 (I2_SKIP_INTRO=1)\n");
    }
    if (opt_auto_newgame) {
        _putenv("I2_AUTO_START=1");
        _putenv("I2_AUTO_NEWGAME=1");
        printf("[patch] Auto New Game injector habilitado (State 2 -> 3 via START/CROSS)\n");
    } else {
        _putenv("I2_AUTO_START=0");
        _putenv("I2_AUTO_NEWGAME=0");
        printf("[patch] Auto New Game injector desabilitado\n");
    }
    if (opt_mesh_trim) {
        guest_write32(0x00828D40, 0x4020007F);
        printf("[patch] Disable Mesh Trimming ativado em 0x00828D40 (spu nop 0x4020007F)\n");
    }
    if (opt_unlock_fps) {
        g_opt_unlock_fps = true;
        guest_write32(0x00526E04, 0x38600000);
        printf("[patch] Unlock FPS ativado em 0x00526E04 (li r3, 0)\n");
    }

    derive_vfs_root(argv[1]);
    printf("[boot] VFS root: %s\n", ppu_vfs_root);

    /* The firmware HLE uses this ID for /dev_hdd0/game paths. The starter
     * template leaves its placeholder in place unless the title reads SFO. */
    char sfo_path[1200];
    snprintf(sfo_path, sizeof sfo_path, "%s/PS3_GAME/PARAM.SFO", ppu_vfs_root);
    cellGame_init_from_paramsfo(sfo_path);

    ppu_recomp_register();   /* lifted function table -> address map */
    ppu_register_function(0x0004A160ULL, hook_func_0004A160);
    ppu_register_function(0x006BCF80ULL, hook_func_006BCF80);
    ppu_register_function(0x004859E4ULL, hook_func_004859E4);
    ppu_register_function(0x006C99D0ULL, hook_func_006C99D0);
    ppu_register_function(0x004858A4ULL, hook_func_004858A4);
    ps3_load_prx_modules();  /* this game's lifted system PRX, if it has any */
    ppu_hle_init();          /* firmware import NID -> HLE handlers */
    ppu_sysprx_register();   /* boot-critical CRT */
    ppu_fs_register();       /* cellFs over the game directory */
    lv2_init_syscalls();     /* the lv2 syscall table */
    i2_register_bink_sem_stats(); /* wraps existing handlers only when requested */
    i2_register_gcm_flip_stats();

    /* Start on the boot thread after all lifted functions are registered.
     * The SDK records this thread for PS3_SAMPLE_MAIN and checks .pdata
     * before attributing samples. Keep diagnostics opt-in. */
    if (getenv("PS3_SAMPLE")) ps3_sampler_start();

    /* Install the guest-callback hook, then start the frame clock. It no-ops
     * until the title registers its vblank and flip handlers during init. */
    g_ps3_guest_caller = harness_guest_caller;
#ifdef _WIN32
    if (i2_light_kicker_stats_enabled() || i2_wws_stats_enabled()) {
        HANDLE watch = CreateThread(NULL, 0, i2_light_kicker_watch_thread, NULL, 0, NULL);
        if (watch) CloseHandle(watch);
        else fprintf(stderr, "[i2-light-kicker] observer thread creation failed (%lu)\n", GetLastError());
    }
    if (i2_spin_dump_enabled()) {
        HANDLE dump = CreateThread(NULL, 0, i2_spin_dump_thread, NULL, 0, NULL);
        if (dump) CloseHandle(dump);
        else fprintf(stderr, "[i2-spin-dump] observer thread creation failed (%lu)\n", GetLastError());
    }
#endif
    CreateThread(NULL, 4u * 1024 * 1024, frame_clock, NULL, 0, NULL);

    printf("\n[boot] dispatching entry OPD 0x%08X (stack top 0x%08X)\n\n",
           entry, STACK_TOP);

    int rc = ppu_run(entry, STACK_TOP);
    printf("\n[boot] ppu_run returned %d (entry function unwound)\n", rc);

    /* A title that called sys_process_exit never reaches this line: that path
     * ends in the host exit(). Getting here means the entry function returned
     * instead, so hand back the status the guest published if it published
     * one. Returning a hardcoded 0 would report success for a run that never
     * got anywhere. */
    return g_sys_process_exit_called ? (int)g_sys_process_exit_code : 0;
}
