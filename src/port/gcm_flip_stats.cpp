/* Optional flip observation. Reads do not change guest memory, and the installed
 * generic handler runs once with the bridge's original argument/return ABI. */
#include "ppu_recomp.h"
#include "win32_compat.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>

#ifdef _WIN32
extern "C" uint8_t* vm_base;
extern "C" uint32_t ppu_vm_size;
extern "C" void* ps3_hle_find_handler(uint32_t nid);
extern "C" void ps3_hle_register_ctx(uint32_t nid, const char* name,
                                     void (*handler)(ppu_context*));

namespace {
constexpr uint32_t flip_nid = 0x21397818u;
constexpr unsigned entry_limit = 8;
constexpr unsigned rewind_limit = 8;
constexpr uint64_t fifo_control_ea = 0x20002000ULL;
using GenericHandler = uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t,
                                    uint64_t, uint64_t, uint64_t, uint64_t);
GenericHandler original_flip = nullptr;
SRWLOCK observation_lock = SRWLOCK_INIT;
LARGE_INTEGER frequency;
uint64_t entry_count = 0;
unsigned rewind_count = 0;

struct Snapshot {
    uint32_t begin = 0, end = 0, current = 0, callback = 0, callback_entry = 0;
    uint32_t valid = 0;
};

bool read_word(uint64_t base, uint32_t offset, uint32_t& value) {
    const uint64_t limit = ppu_vm_size ? ppu_vm_size : 0x100000000ULL;
    if (!vm_base || !base || (base & 3) || base >= limit || uint64_t(offset) + 4 > limit - base)
        return false;
    uint8_t bytes[4];
    SIZE_T copied = 0;
    if (!ReadProcessMemory(GetCurrentProcess(), vm_base + base + offset,
                           bytes, sizeof bytes, &copied) || copied != sizeof bytes)
        return false;
    value = (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) |
            (uint32_t(bytes[2]) << 8) | bytes[3];
    return true;
}

Snapshot read_context(uint64_t context) {
    Snapshot snapshot;
    if (read_word(context, 0, snapshot.begin)) snapshot.valid |= 1u;
    if (read_word(context, 4, snapshot.end)) snapshot.valid |= 2u;
    if (read_word(context, 8, snapshot.current)) snapshot.valid |= 4u;
    if (read_word(context, 12, snapshot.callback)) snapshot.valid |= 8u;
    if ((snapshot.valid & 8u) && read_word(snapshot.callback, 0, snapshot.callback_entry))
        snapshot.valid |= 16u;
    return snapshot;
}

uint64_t time_us() {
    LARGE_INTEGER ticks;
    if (!QueryPerformanceCounter(&ticks)) return 0;
    return uint64_t(ticks.QuadPart / frequency.QuadPart) * 1000000ULL +
           uint64_t(ticks.QuadPart % frequency.QuadPart) * 1000000ULL /
               uint64_t(frequency.QuadPart);
}

uint64_t call_original(ppu_context* context) {
    return original_flip(context->gpr[3], context->gpr[4], context->gpr[5], context->gpr[6],
                         context->gpr[7], context->gpr[8], context->gpr[9], context->gpr[10]);
}

void observe_flip(ppu_context* context) {
    const DWORD incoming_error = GetLastError();
    AcquireSRWLockExclusive(&observation_lock);
    const uint64_t sequence = ++entry_count;
    const bool first_entry = sequence <= entry_limit;
    const bool observe = first_entry || rewind_count < rewind_limit;
    ReleaseSRWLockExclusive(&observation_lock);
    if (!observe) {
        SetLastError(incoming_error);
        context->gpr[3] = call_original(context);
        return;
    }

    const uint64_t guest_context = context->gpr[3];
    const uint64_t buffer = context->gpr[4];
    const uint64_t caller = context->lr;
    const uint64_t thread = context->thread_id;
    const uint64_t start = time_us();
    const Snapshot before = read_context(guest_context);
    SetLastError(incoming_error);
    // No diagnostic lock spans the original handler or a guest callback.
    const uint64_t result = call_original(context);
    context->gpr[3] = result;
    const DWORD original_error = GetLastError();
    const uint64_t finish = time_us();
    uint32_t current_after = 0, put = 0, get = 0;
    uint32_t after_valid = 0;
    if (read_word(guest_context, 8, current_after)) after_valid |= 1u;
    if (read_word(fifo_control_ea, 0, put)) after_valid |= 2u;
    if (read_word(fifo_control_ea, 4, get)) after_valid |= 4u;
    const bool rewind = (before.valid & 4u) && (after_valid & 1u) &&
                        current_after < before.current;

    AcquireSRWLockExclusive(&observation_lock);
    const bool additional_rewind = !first_entry && rewind && rewind_count < rewind_limit;
    if (additional_rewind) ++rewind_count;
    const unsigned rewind_number = rewind_count;
    ReleaseSRWLockExclusive(&observation_lock);
    if (first_entry || additional_rewind) {
        // Fields can change concurrently; a decrease alone does not identify its writer.
        std::fprintf(stderr, "[i2-gcm-flip] seq=%llu start_us=%llu finish_us=%llu "
            "ctx=%016llX buffer=%llu lr=%016llX tid=%llu result=%016llX "
            "before_valid=%02X begin=%08X end=%08X current_before=%08X "
            "callback_opd=%08X callback_entry=%08X after_valid=%X current_after=%08X "
            "put=%08X get=%08X rewind=%d additional_rewind=%u concurrent=1\n",
            (unsigned long long)sequence, (unsigned long long)start, (unsigned long long)finish,
            (unsigned long long)guest_context, (unsigned long long)buffer,
            (unsigned long long)caller, (unsigned long long)thread, (unsigned long long)result,
            before.valid, before.begin, before.end, before.current,
            before.callback, before.callback_entry, after_valid, current_after, put, get,
            rewind, additional_rewind ? rewind_number : 0u);
        if (sequence == entry_limit)
            std::fprintf(stderr, "[i2-gcm-flip] initial_cap=%u additional_rewind_limit=%u\n",
                         entry_limit, rewind_limit);
        if (additional_rewind && rewind_number == rewind_limit)
            std::fprintf(stderr, "[i2-gcm-flip] additional_rewind_cap=%u observation_stopped=1\n",
                         rewind_limit);
    }
    SetLastError(original_error);
}
}
#endif

extern "C" void i2_register_gcm_flip_stats(void) {
#ifdef _WIN32
    const char* enabled = std::getenv("I2_GCM_FLIP_STATS");
    if (!enabled || !*enabled || std::strcmp(enabled, "0") == 0 || original_flip) return;
    const DWORD saved_error = GetLastError();
    void* installed = ps3_hle_find_handler(flip_nid);
    if (installed && QueryPerformanceFrequency(&frequency) && frequency.QuadPart > 0) {
        original_flip = reinterpret_cast<GenericHandler>(installed);
        ps3_hle_register_ctx(flip_nid, "_cellGcmSetFlipCommand", observe_flip);
        std::fprintf(stderr, "[i2-gcm-flip] enabled nid=%08X initial_limit=%u "
            "additional_rewind_limit=%u readonly=1\n", flip_nid, entry_limit, rewind_limit);
    } else {
        std::fprintf(stderr, "[i2-gcm-flip] unavailable nid=%08X handler=%d\n",
                     flip_nid, installed != nullptr);
    }
    SetLastError(saved_error);
#endif
}
