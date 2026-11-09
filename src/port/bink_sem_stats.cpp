/* Optional BCUS98125 semaphore observation. Calls the installed LV2 handlers
 * once, without guest writes, synthetic tokens, or scheduling changes. */
#include "ppu_recomp.h"
#define PPU_CONTEXT_H
#include "../../tools/ps3recomp/runtime/syscalls/lv2_syscall_table.h"
#include "win32_compat.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <mutex>

extern "C" unsigned long long ppu_thread_cpu_us(unsigned tid);
extern "C" uint8_t* vm_base;
extern "C" uint32_t ppu_vm_size;

namespace {
constexpr uint32_t create_lr = 0x006DE064;
constexpr uint32_t wait_lr = 0x006D9E60;
constexpr uint32_t open_post_lr = 0x006DDD94;
lv2_syscall_fn original_create, original_destroy, original_wait, original_post;
LARGE_INTEGER frequency;
std::mutex stats_mutex;
bool buffer_stats = false;

struct Window {
    uint64_t waits = 0, ok = 0, timeout = 0, error = 0;
    uint64_t total_us = 0, max_us = 0;
    uint64_t requested_min_us = UINT64_MAX, requested_max_us = 0;
    uint64_t posts = 0, open_posts = 0, other_posts = 0, post_ok = 0, post_error = 0;
    int64_t requested_count = 0;
    uint64_t accepted_count = 0;
};
struct State {
    bool active = false;
    uint32_t id = 0;
    uint64_t generation = 0, window_start_us = 0, last_post_us = 0;
    uint32_t last_post_lr = 0, last_post_result = 0;
    uint32_t snd_tid = 0;
    uint64_t cpu_start_us = 0, cpu_window_start_us = 0;
    uint64_t buffer_snapshot_us = 0;
    Window window;
} state;
uint64_t next_generation = 0;
uint64_t lifecycle_epoch = 0, ambiguous_lifecycles = 0;
unsigned lifecycle_in_flight = 0;

uint64_t qpc_us() {
    LARGE_INTEGER ticks;
    QueryPerformanceCounter(&ticks);
    return uint64_t(ticks.QuadPart / frequency.QuadPart) * 1000000ULL +
        uint64_t(ticks.QuadPart % frequency.QuadPart) * 1000000ULL / uint64_t(frequency.QuadPart);
}

// Called under the diagnostic mutex; no LV2 handler runs under this mutex.
void report(uint64_t now, bool final) {
    const uint64_t elapsed = now - state.window_start_us;
    if (!state.active || (!final && elapsed < 5000000)) return;
    const Window& w = state.window;
    const uint64_t cpu_now = state.snd_tid ? ppu_thread_cpu_us(state.snd_tid) : 0;
    const int64_t cpu_delta = cpu_now && state.cpu_start_us && cpu_now >= state.cpu_start_us ?
        int64_t(cpu_now - state.cpu_start_us) : -1;
    const uint64_t cpu_wall = state.cpu_window_start_us ? now - state.cpu_window_start_us : 0;
    std::fprintf(stderr, "[i2-bink-sem] t_us=%llu gen=%llu id=%u final=%d window_us=%llu "
        "waits=%llu ok=%llu timeout=%llu error=%llu total_wait_us=%llu max_wait_us=%llu "
        "requested_min_us=%llu requested_max_us=%llu posts=%llu open_posts=%llu other_posts=%llu "
        "post_ok=%llu post_error=%llu requested_count=%lld accepted_count=%llu "
        "last_post_lr=%08X last_post_result=%08X last_post_age_us=%lld "
        "snd_tid=%u snd_cpu_us=%lld cpu_wall_us=%llu cpu_pct=%.1f (cpu=-1 unavailable)\n",
        (unsigned long long)now, (unsigned long long)state.generation, state.id, final,
        (unsigned long long)elapsed, (unsigned long long)w.waits, (unsigned long long)w.ok,
        (unsigned long long)w.timeout, (unsigned long long)w.error,
        (unsigned long long)w.total_us, (unsigned long long)w.max_us,
        (unsigned long long)(w.waits ? w.requested_min_us : 0), (unsigned long long)w.requested_max_us,
        (unsigned long long)w.posts, (unsigned long long)w.open_posts, (unsigned long long)w.other_posts,
        (unsigned long long)w.post_ok, (unsigned long long)w.post_error,
        (long long)w.requested_count, (unsigned long long)w.accepted_count,
        state.last_post_lr, state.last_post_result,
        state.last_post_us ? (long long)(now - state.last_post_us) : -1LL,
        state.snd_tid, (long long)cpu_delta, (unsigned long long)cpu_wall,
        cpu_delta >= 0 && cpu_wall ? 100.0 * cpu_delta / cpu_wall : -1.0);
    state.window = Window{};
    state.window_start_us = now;
    state.cpu_start_us = cpu_now;
    state.cpu_window_start_us = state.snd_tid ? now : 0;
}

struct Lifetime {
    bool selected;
    uint32_t id;
    uint64_t generation;
};
Lifetime capture(uint32_t id, uint32_t observed_tid = 0) {
    std::lock_guard<std::mutex> guard(stats_mutex);
    if (lifecycle_in_flight) return {false, id, state.generation};
    if (state.active && state.id == id && observed_tid && !state.snd_tid) {
        state.snd_tid = observed_tid;
        state.cpu_start_us = ppu_thread_cpu_us(observed_tid);
        state.cpu_window_start_us = qpc_us();
    }
    return {state.active && state.id == id, id, state.generation};
}
bool current(const Lifetime& lifetime) {
    return !lifecycle_in_flight && lifetime.selected && state.active && state.id == lifetime.id &&
        state.generation == lifetime.generation;
}

bool read_buffer_word(uint64_t base, uint32_t offset, uint32_t& value) {
    const uint64_t limit = ppu_vm_size ? ppu_vm_size : 0x100000000ULL;
    if (!vm_base || !base || (base & 3) || base >= limit || uint64_t(offset) + 4 > limit - base)
        return false;
#ifdef _WIN32
    uint8_t bytes[4];
    SIZE_T copied = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), vm_base + base + offset, bytes, 4, &copied) || copied != 4)
        return false;
    value = (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) |
        (uint32_t(bytes[2]) << 8) | bytes[3];
    return true;
#else
    // No fault-safe read primitive is installed for the non-Windows port.
    return false;
#endif
}

// Called under stats_mutex, after the original wait and before guest execution
// resumes. Other guest threads keep running: fields are NOT an atomic snapshot.
void report_buffers(ppu_context* ctx, uint64_t now) {
    if (!buffer_stats || (state.buffer_snapshot_us && now - state.buffer_snapshot_us < 100000)) return;
    state.buffer_snapshot_us = now;
#ifdef _WIN32
    const DWORD saved_error = GetLastError();
#endif
    uint32_t h = 0, audio = 0, driver = 0, read_ea = 0, mask = 0;
    uint32_t values[12]{};
    const uint64_t global = ctx->gpr[27];
    if (read_buffer_word(global, 0x5C0, h)) {
        const uint32_t offsets[] = {0xC, 0x460, 0x118, 0x188, 0x11C, 0x4D8};
        for (unsigned i = 0; i < 6; ++i)
            if (read_buffer_word(h, offsets[i], values[i])) mask |= 1u << i;
        if (read_buffer_word(h, 0x154, audio)) {
            const uint32_t audio_offsets[] = {0, 0x18, 8, 0x4C};
            for (unsigned i = 0; i < 4; ++i)
                if (read_buffer_word(audio, audio_offsets[i], values[i + 6])) mask |= 1u << (i + 6);
        }
    }
    if (ctx->gpr[2] == 0x00973528 && read_buffer_word(ctx->gpr[2] - 0x5C68, 0, driver)) {
        if (read_buffer_word(driver, 0xC, values[10])) mask |= 1u << 10;
        if (read_buffer_word(driver, 8, read_ea) && read_buffer_word(read_ea, 0, values[11])) mask |= 1u << 11;
    }
    std::fprintf(stderr, "[i2-bink-buffer] t_us=%llu gen=%llu id=%u global=%08llX toc=%08llX "
        "h=%08X audio=%08X driver=%08X read_ea=%08X valid_mask=%03X "
        "h_C=%08X h_460=%08X h_118=%08X h_188=%08X h_11C=%08X h_4D8=%08X "
        "a_0=%08X a_18=%08X a_8=%08X a_4C=%08X driver_C=%08X read_index_low32=%08X concurrent=1\n",
        (unsigned long long)now, (unsigned long long)state.generation, state.id,
        (unsigned long long)global, (unsigned long long)ctx->gpr[2], h, audio, driver, read_ea, mask,
        values[0], values[1], values[2], values[3], values[4], values[5],
        values[6], values[7], values[8], values[9], values[10], values[11]);
#ifdef _WIN32
    SetLastError(saved_error);
#endif
}

void omit_lifecycle(const char* reason) {
    if (state.active) { report(qpc_us(), true); state.active = false; }
    if (++ambiguous_lifecycles <= 16)
        std::fprintf(stderr, "[i2-bink-sem] lifecycle_omitted reason=%s epoch=%llu in_flight=%u count=%llu\n",
            reason, (unsigned long long)lifecycle_epoch, lifecycle_in_flight,
            (unsigned long long)ambiguous_lifecycles);
}

// Only diagnostic bookkeeping is locked. RAII also retires an entry when the
// original handler throws, without swallowing or replacing that exception.
struct Lifecycle {
    uint64_t epoch;
    bool overlap, finished = false;
    Lifetime lifetime{false, 0, 0};
    Lifecycle(uint32_t id = 0, bool select = false) {
        std::lock_guard<std::mutex> guard(stats_mutex);
        // DESTROY captures its generation atomically with entering lifecycle.
        if (select && !lifecycle_in_flight)
            lifetime = {state.active && state.id == id, id, state.generation};
        overlap = lifecycle_in_flight++ != 0;
        epoch = ++lifecycle_epoch;
        if (overlap) omit_lifecycle("overlap");
    }
    // Caller holds stats_mutex. A return order cannot establish handle lifetime.
    bool finish(bool success) {
        const bool unambiguous = !overlap && lifecycle_epoch == epoch && lifecycle_in_flight == 1;
        --lifecycle_in_flight;
        if (success) ++lifecycle_epoch;
        finished = true;
        if (!unambiguous) omit_lifecycle("concurrent_lifecycle");
        return unambiguous;
    }
    ~Lifecycle() {
        if (!finished) {
            std::lock_guard<std::mutex> guard(stats_mutex);
            finish(false);
            omit_lifecycle("exception");
        }
    }
};

int64_t observe_create(ppu_context* ctx) {
    const uint32_t caller = uint32_t(ctx->lr);
    const uint32_t output = uint32_t(ctx->gpr[3]);
    Lifecycle lifecycle;
    const int64_t result = original_create(ctx);
    std::lock_guard<std::mutex> guard(stats_mutex);
    const bool unambiguous = lifecycle.finish(result == 0);
    if (result != 0 || !unambiguous) return result;
    // The original accepts a null lower32 output without writing a handle.
    // Read only for the selected caller or to detect reuse of a known lifetime.
    if (!output) {
        if (caller == create_lr) omit_lifecycle("null_output");
        return result;
    }
    if (caller != create_lr && !state.active) return result;
    const uint32_t id = vm_read32(output);
    const uint64_t now = qpc_us();
    // A successful reuse also ends an old observation if its destroy was missed.
    if (state.active && (state.id == id || caller == create_lr)) {
        report(now, true);
        state.active = false;
    }
    if (caller == create_lr) {
        state = State{};
        state.active = true;
        state.id = id;
        state.generation = ++next_generation;
        state.window_start_us = now;
        std::fprintf(stderr, "[i2-bink-sem] begin t_us=%llu gen=%llu id=%u create_lr=%08X out_ea=%08X\n",
            (unsigned long long)now, (unsigned long long)state.generation, id, caller,
            output);
    }
    return result;
}

int64_t observe_destroy(ppu_context* ctx) {
    Lifecycle lifecycle(uint32_t(ctx->gpr[3]), true);
    const int64_t result = original_destroy(ctx);
    std::lock_guard<std::mutex> guard(stats_mutex);
    const bool unambiguous = lifecycle.finish(result == 0);
    if (result == 0 && unambiguous && current(lifecycle.lifetime)) {
        report(qpc_us(), true);
        state.active = false;
    }
    return result;
}

int64_t observe_wait(ppu_context* ctx) {
    const uint32_t caller = uint32_t(ctx->lr);
    const uint64_t requested = ctx->gpr[4];
    // Capture id and generation BEFORE the original call can park or mutate r3.
    const Lifetime lifetime = capture(uint32_t(ctx->gpr[3]), caller == wait_lr ? uint32_t(ctx->thread_id) : 0);
    if (caller != wait_lr || !lifetime.selected) return original_wait(ctx);
    const uint64_t start = qpc_us();
    const int64_t result = original_wait(ctx);
    const uint64_t finish = qpc_us();
    std::lock_guard<std::mutex> guard(stats_mutex);
    if (current(lifetime)) {
        Window& w = state.window;
        w.waits++;
        if (result == 0) w.ok++;
        else if (uint32_t(result) == CELL_ETIMEDOUT) w.timeout++;
        else w.error++;
        const uint64_t elapsed = finish - start;
        w.total_us += elapsed;
        if (elapsed > w.max_us) w.max_us = elapsed;
        if (requested < w.requested_min_us) w.requested_min_us = requested;
        if (requested > w.requested_max_us) w.requested_max_us = requested;
        // A post may have advanced the window after this wait finished but
        // before this lock was acquired. Use an observation time under lock.
        const uint64_t now = qpc_us();
        report_buffers(ctx, now);
        report(qpc_us(), false);
    }
    return result;
}

int64_t observe_post(ppu_context* ctx) {
    const uint32_t caller = uint32_t(ctx->lr);
    const int32_t count = int32_t(ctx->gpr[4]);
    const Lifetime lifetime = capture(uint32_t(ctx->gpr[3]));
    const int64_t result = original_post(ctx);
    if (lifetime.selected) {
        std::lock_guard<std::mutex> guard(stats_mutex);
        if (current(lifetime)) {
            const uint64_t now = qpc_us();
            Window& w = state.window;
            w.posts++;
            if (caller == open_post_lr) w.open_posts++; else w.other_posts++;
            w.requested_count += count;
            if (result == 0) { w.post_ok++; if (count > 0) w.accepted_count += uint32_t(count); }
            else w.post_error++;
            state.last_post_us = now;
            state.last_post_lr = caller;
            state.last_post_result = uint32_t(result);
            report(now, false);
        }
    }
    return result;
}
}

extern "C" void i2_register_bink_sem_stats(void) {
    const char* enabled = std::getenv("I2_BINK_SEM_STATS");
    if (!enabled || !*enabled || std::strcmp(enabled, "0") == 0) return;
    if (g_lv2_syscalls.handlers[SYS_SEMAPHORE_WAIT] == observe_wait) return;
    if (!QueryPerformanceFrequency(&frequency) || frequency.QuadPart <= 0) return;
    const char* buffers = std::getenv("I2_BINK_BUFFER_STATS");
    buffer_stats = buffers && *buffers && std::strcmp(buffers, "0") != 0;
    original_create = g_lv2_syscalls.handlers[SYS_SEMAPHORE_CREATE];
    original_destroy = g_lv2_syscalls.handlers[SYS_SEMAPHORE_DESTROY];
    original_wait = g_lv2_syscalls.handlers[SYS_SEMAPHORE_WAIT];
    original_post = g_lv2_syscalls.handlers[SYS_SEMAPHORE_POST];
    lv2_syscall_register(&g_lv2_syscalls, SYS_SEMAPHORE_CREATE, observe_create);
    lv2_syscall_register(&g_lv2_syscalls, SYS_SEMAPHORE_DESTROY, observe_destroy);
    lv2_syscall_register(&g_lv2_syscalls, SYS_SEMAPHORE_WAIT, observe_wait);
    lv2_syscall_register(&g_lv2_syscalls, SYS_SEMAPHORE_POST, observe_post);
}
