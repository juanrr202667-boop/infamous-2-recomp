// Native contract probe for LFQueue push notification semantics.
// It invokes the real port implementation; no game image or runtime is loaded.
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "spu_coherency.h"
#include "cellSpurs.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

extern "C" void ps3_load_prx_modules(void);
extern "C" void ps3_hle_call(uint32_t, ppu_context*);
extern "C" void ps3_hle_register(uint32_t, const char*, void*);
extern "C" void i2_register_lfqueue(void);
extern "C" int32_t _cellSpursLFQueueInitialize(uint64_t, uint64_t, uint64_t,
                                                uint32_t, uint32_t, uint32_t);

namespace {
constexpr uint32_t queue_ea = 0x11000;
constexpr uint32_t queue_buffer_ea = 0x12000;
constexpr uint32_t taskset_ea = 0x20000;
constexpr uint32_t spurs_ea = 0x24000;
constexpr uint32_t push_data_ea = 0x28000;
uint8_t memory[0x40000]{};

bool push(uint16_t waiting_count, uint32_t signal_before,
          uint32_t expected_signal_after, const char* label)
{
    vm_write16(queue_ea + 4, waiting_count);
    vm_write16(queue_ea + 6, 0);
    vm_write16(queue_ea + 8, vm_read16(queue_ea + 14));
    vm_write16(queue_ea + 0x0A, 0);
    vm_write16(queue_ea + 0x32, 0); // waiter is task 0 in workload 0
    vm_write32(taskset_ea + 0x40, signal_before);
    std::fprintf(stderr, "pre %s: ctl=%04X pop_reserved=%u owner_hi=%08X owner_lo=%08X "
        "taskset_spurs=%08X taskset_workload=%08X enabled=%08X\n", label,
        vm_read16(queue_ea + 4), vm_read16(queue_ea + 6), vm_read32(queue_ea + 0x70),
        vm_read32(queue_ea + 0x74), vm_read32(taskset_ea + 0x64),
        vm_read32(taskset_ea + 0x74), vm_read32(taskset_ea + 0x30));
    std::memset(memory + push_data_ea, 0x5A, 32);

    ppu_context ctx{};
    ctx.gpr[3] = queue_ea;
    ctx.gpr[4] = push_data_ea;
    ctx.gpr[5] = 3;
    ps3_hle_call(0x8A85674Du, &ctx);

    const uint32_t signal_after = vm_read32(taskset_ea + 0x40);
    const bool ok = int32_t(ctx.gpr[3]) == 0 && signal_after == expected_signal_after;
    std::fprintf(stderr,
        "%s: result=0x%08X waiter_count=%u signal_before=0x%08X "
        "signal_after=0x%08X expected=0x%08X %s\n",
        label, uint32_t(ctx.gpr[3]), waiting_count, signal_before,
        signal_after, expected_signal_after, ok ? "PASS" : "FAIL");
    return ok;
}
}

extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

int main()
{
    ps3_load_prx_modules();
    i2_register_lfqueue();
    ps3_hle_register(0x011EE38Bu, "_cellSpursLFQueueInitialize",
        reinterpret_cast<void*>(_cellSpursLFQueueInitialize));

    if (cellSpursInitialize(reinterpret_cast<CellSpurs*>(uintptr_t(spurs_ea)),
                            1, 100, 100, 0) != 0) {
        std::fprintf(stderr, "cellSpursInitialize failed\n");
        return 2;
    }
    ppu_context ctx{};
    ctx.gpr[3] = spurs_ea;
    ctx.gpr[4] = taskset_ea;
    ctx.gpr[5] = 0;
    ctx.gpr[6] = 0;
    ctx.gpr[7] = 1;
    ps3_hle_call(0x52CC6C82u, &ctx);
    if (ctx.gpr[3] != 0) {
        std::fprintf(stderr, "taskset creation failed: 0x%08X\n", uint32_t(ctx.gpr[3]));
        return 2;
    }
    vm_write32(taskset_ea + 0x30, 0x80000000u); // enable task 0 for signal validation

    ctx = {};
    ctx.gpr[1] = 0x30000;
    ctx.gpr[3] = spurs_ea | 1u; // HLE workload-owner token for workload 0
    ctx.gpr[4] = queue_ea;
    ctx.gpr[5] = queue_buffer_ea;
    ctx.gpr[6] = 32;
    ctx.gpr[7] = 12;
    ctx.gpr[8] = 3;
    ps3_hle_call(0x011EE38Bu, &ctx);
    if (ctx.gpr[3] != 0) {
        std::fprintf(stderr, "queue initialization failed: 0x%08X\n", uint32_t(ctx.gpr[3]));
        return 2;
    }

    // Per the reference's LFQueuePushBody, only completed items with registered
    // pop waiters are signalled; nwake==0 has no taskset-wide task-0 broadcast.
    const bool no_waiter = push(0, 0, 0, "push-without-waiter");
    // Model the prior item being consumed and fully completed before registering
    // a new pop waiter, so the second push is a valid one-item wake transition.
    vm_write16(queue_ea, 1);
    vm_write16(queue_ea + 6, 1);
    // With one waiting task 0 and one newly completed item, one latched signal
    // bit is expected, matching the local waiter path and peer protocol.
    const bool one_waiter = push(1, 0, 0x80000000u, "push-with-one-waiter");
    return no_waiter && one_waiter ? 0 : 1;
}
