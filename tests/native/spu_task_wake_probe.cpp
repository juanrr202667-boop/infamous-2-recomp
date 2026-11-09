/* Real SPU channels and WAIT_SIGNAL, synthetic guest memory only.
 * A committed task signal must wake the parked task without waiting for the
 * one-second predicate poll. Failed or irrelevant commits must not resume it.
 */
#include "ppu_recomp.h"
#include "spu_context.h"
#include <windows.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>

extern "C" {
void spu_wrch(spu_context*, uint32_t, u128);
u128 spu_rdch(spu_context*, uint32_t);
void spu_mfc_release(spu_context*);
void spu_taskset_parked_add(uint32_t, uint32_t, uint32_t);
void spu_taskset_parked_del(uint32_t, uint32_t);
int spu_taskset_wait_signal(uint32_t, uint32_t);
void spu_taskset_signal_task(uint32_t, uint32_t);
}

static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

namespace {
constexpr uint32_t taskset = 0x10000;
constexpr uint32_t other_taskset = 0x11000;
constexpr uint32_t task_id = 67;
constexpr uint32_t local_line = 0x1000;
constexpr uint32_t signal_byte = 0x48; // SIGNALLED + task 67's byte, BE bitset.
constexpr uint8_t task_mask = 0x10;
constexpr uint8_t other_task_mask = 0x08; // Task 68, same byte.
constexpr DWORD prompt_ms = 300;
constexpr DWORD safety_ms = 2500;

HANDLE wait_entered;
using SleepConditionFn = BOOL (WINAPI*)(PCONDITION_VARIABLE, PSRWLOCK, DWORD, ULONG);
SleepConditionFn real_sleep;

// Observe the real OS wait boundary while the runtime still owns its signal
// lock. The producer may now commit; a correct notifier takes that same lock
// and cannot run before SleepConditionVariableSRW atomically releases it.
// No wait, predicate, return value, or guest state is replaced by the probe.
BOOL WINAPI observe_sleep(PCONDITION_VARIABLE cv, PSRWLOCK lock, DWORD timeout, ULONG flags) {
    SetEvent(wait_entered);
    return real_sleep(cv, lock, timeout, flags);
}

void write_channel(spu_context* ctx, uint32_t channel, uint32_t value) {
    u128 argument{};
    argument._u32[0] = value;
    spu_wrch(ctx, channel, argument);
}

uint32_t atomic_line(spu_context* ctx, uint32_t ea, uint32_t command) {
    write_channel(ctx, MFC_LSA, local_line);
    write_channel(ctx, MFC_EAH, 0);
    write_channel(ctx, MFC_EAL, ea);
    write_channel(ctx, MFC_Cmd, command);
    return spu_rdch(ctx, MFC_RdAtomicStat)._u32[0];
}

struct Waiter {
    HANDLE done = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    HANDLE thread = nullptr;
    int result = -1;

    static DWORD WINAPI run(void* opaque) {
        auto* waiter = static_cast<Waiter*>(opaque);
        spu_taskset_parked_add(taskset, task_id, 0);
        waiter->result = spu_taskset_wait_signal(taskset, task_id);
        spu_taskset_parked_del(taskset, task_id);
        SetEvent(waiter->done);
        return 0;
    }

    bool start() {
        ResetEvent(wait_entered);
        thread = CreateThread(nullptr, 0, run, this, 0, nullptr);
        return done && thread;
    }

    bool entered_wait() const {
        HANDLE events[] = {wait_entered, done};
        return WaitForMultipleObjects(2, events, FALSE, safety_ms) == WAIT_OBJECT_0;
    }

    bool completed(DWORD timeout) const {
        return WaitForSingleObject(done, timeout) == WAIT_OBJECT_0;
    }

    ~Waiter() {
        if (thread) {
            if (!completed(0)) spu_taskset_signal_task(taskset, task_id);
            if (WaitForSingleObject(thread, safety_ms) != WAIT_OBJECT_0) {
                std::fprintf(stderr, "task-wake ERROR: waiter cleanup stalled\n");
                std::exit(2);
            }
            CloseHandle(thread);
        }
        if (done) CloseHandle(done);
    }
};

int fail(const char* mode, const char* message) {
    std::fprintf(stderr, "task-wake %s FAIL: %s\n", mode, message);
    return 1;
}
}

// Supply the import slot for the statically linked runtime's real Windows
// call. This is test-only observation; the forward target is Kernel32's API.
extern "C" SleepConditionFn __imp_SleepConditionVariableSRW = observe_sleep;

int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "putllc";
    const bool queued_unconditional = std::strcmp(mode, "putqlluc") == 0;
    const bool unconditional = queued_unconditional || std::strstr(mode, "putlluc") != nullptr;
    const bool early = std::strncmp(mode, "early-", 6) == 0;
    const bool failed = std::strcmp(mode, "failed-putllc") == 0;
    const bool unrelated = std::strncmp(mode, "unrelated-", 10) == 0;
    const bool no_target = std::strncmp(mode, "no-target-", 10) == 0;
    if (std::strcmp(mode, "putllc") != 0 && std::strcmp(mode, "putlluc") != 0 && !queued_unconditional &&
        std::strcmp(mode, "early-putllc") != 0 && std::strcmp(mode, "early-putlluc") != 0 &&
        !failed && std::strcmp(mode, "unrelated-putllc") != 0 &&
        std::strcmp(mode, "unrelated-putlluc") != 0 &&
        std::strcmp(mode, "no-target-putllc") != 0 &&
        std::strcmp(mode, "no-target-putlluc") != 0) return 2;

    _putenv_s("LBP_WS_DRAIN", "");
    _putenv_s("LBP_SYNC_ACK", "");
    _putenv_s("LBP_SPU_SERIAL", "");
    real_sleep = reinterpret_cast<SleepConditionFn>(GetProcAddress(
        GetModuleHandleW(L"kernel32.dll"), "SleepConditionVariableSRW"));
    wait_entered = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!real_sleep || !wait_entered) return 2;

    auto producer = std::make_unique<spu_context>();
    spu_context_init(producer.get(), 60);
    producer->image_id = 60;
    producer->pc = 0x12340; // Synthetic identity for opt-in wake diagnostics.
    const uint32_t ea = unrelated ? other_taskset : taskset;
    if (atomic_line(producer.get(), ea, MFC_GETLLAR_CMD) != 4)
        return fail(mode, "GETLLAR did not establish a real reservation");
    producer->ls[local_line + signal_byte] |= no_target ? other_task_mask : task_mask;
    if (!unrelated && !failed) producer->ls[local_line + signal_byte] |= other_task_mask;

    if (failed) {
        auto interferer = std::make_unique<spu_context>();
        spu_context_init(interferer.get(), 61);
        std::memcpy(interferer->ls + local_line, memory + ea, 128);
        interferer->ls[local_line + 0x7F] = 0xA5;
        if (atomic_line(interferer.get(), ea, MFC_PUTLLUC_CMD) != 2)
            return fail(mode, "interfering PUTLLUC did not commit");
        spu_mfc_release(interferer.get());
    }

    const uint32_t command = queued_unconditional ? MFC_PUTQLLUC_CMD :
        unconditional ? MFC_PUTLLUC_CMD : MFC_PUTLLC_CMD;
    const uint32_t expected_status = failed ? 1 : unconditional ? 2 : 0;
    if (early && atomic_line(producer.get(), ea, command) != expected_status)
        return fail(mode, "early atomic write returned the wrong status");
    // A peer reservation over the pre-latched signal must be invalidated when
    // WAIT_SIGNAL consumes its bit; otherwise a subsequent PUTLLC can revive it.
    if (early && atomic_line(producer.get(), ea, MFC_GETLLAR_CMD) != 4)
        return fail(mode, "early signal could not be reserved before consumption");

    Waiter waiter;
    if (!waiter.start()) return 2;
    if (!early && !waiter.entered_wait())
        return fail(mode, "waiter did not reach the real condition wait");

    const auto start = std::chrono::steady_clock::now();
    if (!early && atomic_line(producer.get(), ea, command) != expected_status)
        return fail(mode, "atomic write returned the wrong status");
    const bool prompt = waiter.completed(prompt_ms);
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - start).count();
    const bool control = failed || unrelated || no_target;
    if (control) {
        if (prompt) return fail(mode, "irrelevant or failed commit resumed the task");
        if (memory[taskset + signal_byte] & task_mask)
            return fail(mode, "control fabricated the target signal bit");
        if (failed && memory[taskset + 0x7F] != 0xA5)
            return fail(mode, "failed PUTLLC overwrote the interfering commit");
        if (unrelated && memory[other_taskset + signal_byte] != task_mask)
            return fail(mode, "unrelated line did not receive its real signal");
        if (no_target && memory[taskset + signal_byte] != other_task_mask)
            return fail(mode, "commit did not preserve the other task's signal");
        spu_taskset_signal_task(taskset, task_id); // Real cleanup signal, after assertions.
        if (!waiter.completed(safety_ms) || waiter.result != 0)
            return fail(mode, "explicit cleanup signal did not resume the task");
    } else {
        if (!prompt) {
            // Keep the RED observation: after the short wake deadline, wait for
            // the existing poll to prove the committed bit eventually resumes.
            const bool eventually = waiter.completed(safety_ms);
            const auto poll_elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - start).count();
            std::fprintf(stderr, "task-wake %s FAIL: committed signal missed %lums deadline; "
                "eventual=%d elapsed=%lldms result=%d\n", mode,
                static_cast<unsigned long>(prompt_ms), eventually,
                static_cast<long long>(poll_elapsed), waiter.result);
            return 1;
        }
        if (waiter.result != 0 || memory[taskset + signal_byte] != other_task_mask)
            return fail(mode, "WAIT_SIGNAL did not consume only its own signal bit");
        if (early && producer->resv_valid)
            return fail(mode, "signal consumption left a stale peer reservation valid");
        if (early && WaitForSingleObject(wait_entered, 0) == WAIT_OBJECT_0)
            return fail(mode, "early signal took the blocking condition path");
    }

    if (early) {
        // A latched bit buys exactly one return; the next wait must park again.
        Waiter second;
        if (!second.start() || !second.entered_wait())
            return fail(mode, "second wait reused the consumed early signal");
        if (second.completed(prompt_ms))
            return fail(mode, "consumed early signal resumed a second wait");
        spu_taskset_signal_task(taskset, task_id);
        if (!second.completed(safety_ms) || second.result != 0)
            return fail(mode, "second wait did not accept its own signal");
    }
    spu_mfc_release(producer.get());
    std::fprintf(stderr, "task-wake %s PASS: elapsed=%lldms, real atomic status=%u\n",
        mode, static_cast<long long>(elapsed), expected_status);
    // Waiters finish before the import observer's event is closed at process exit.
    return 0;
}
