/* Real SPURS chain/queue regression; synthetic memory, no game code. */
#include "ppu_recomp.h"
#include "cellSpurs.h"
#define PPU_CONTEXT_H // Use the ABI-compatible generated context already above.
#include "sys_event.h"
#include "spu_workload.h"
#include <cstdio>
#include <cstring>
#include <atomic>

extern "C" {
uint32_t sys_event_queue_create_direct(uint64_t, int32_t);
int32_t cellSpursCreateJobChain(uint64_t, uint64_t, uint64_t, uint32_t,
                              uint32_t, uint64_t, uint32_t, uint32_t);
int32_t cellSpursCreateJobChainWithAttribute(uint64_t, uint64_t, uint64_t);
int32_t cellSpursRunJobChain(uint64_t);
int32_t cellSpursJoinJobChain(uint64_t);
void lv2_init_syscalls(void);
void spu_wrch(spu_context*, uint32_t, u128);
}
static std::atomic<unsigned> silent_runs{}, event_runs{};

static void silent_job(spu_context*) { ++silent_runs; }

static void event_job(spu_context* ctx)
{
    ++event_runs;
    // Read user data from the real job descriptor staged in local store.
    const uint32_t descriptor = ctx->gpr[4]._u32[0];
    u128 user = spu_ls_read128(ctx, descriptor + 0x30);
    u128 data{}, request{};
    data._u32[0] = user._u32[3];
    request._u32[0] = 0x40000000u | (user._u32[1] << 24);
    spu_wrch(ctx, SPU_WrOutMbox, data);
    spu_wrch(ctx, SPU_WrOutIntrMbox, request);
}

static unsigned drain(uint32_t queue, uint32_t output)
{
    ppu_context receive{};
    receive.gpr[3] = queue; receive.gpr[4] = output;
    receive.gpr[5] = 8; receive.gpr[6] = output + 0x100;
    if (sys_event_queue_tryreceive(&receive)) return 99;
    return vm_read32(output + 0x100);
}
static uint8_t memory[0x80000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

int main(int argc, char**)
{
    lv2_init_syscalls(); // Installs the actual SPU user-event delivery hook.
    constexpr uint32_t first = 0x20000;
    if (cellSpursInitialize(reinterpret_cast<CellSpurs*>(first), 1, 100, 100, 0)) return 2;
    uint32_t queues[2] = {sys_event_queue_create_direct(0, 8),
                          sys_event_queue_create_direct(0, 8)};
    if (!queues[0] || !queues[1]) return 2;
    memory[0x18000] = 1; memory[0x18001] = 2;
    // Both queues belong to the same instance. Only port 2 emits an event.
    if (cellSpursAttachLv2EventQueue(reinterpret_cast<CellSpurs*>(first), queues[0],
                                   reinterpret_cast<uint8_t*>(0x18000), 0) ||
        cellSpursAttachLv2EventQueue(reinterpret_cast<CellSpurs*>(first), queues[1],
                                   reinterpret_cast<uint8_t*>(0x18001), 0)) return 2;
    constexpr uint32_t chain = 0x28000, commands = 0x19000;
    constexpr uint32_t silent_desc = 0x1C000, event_desc = 0x1C080;
    constexpr uint32_t silent_bin = 0x1D000, event_bin = 0x1D010;
    memory[silent_bin] = 1; memory[event_bin] = 2;
    spu_workload_register_img(spu_workload_fingerprint(memory + silent_bin, 16),
                              silent_job, 10, "synthetic-silent");
    spu_workload_register_img(spu_workload_fingerprint(memory + event_bin, 16),
                              event_job, 11, "synthetic-event");
    vm_write64(silent_desc, silent_bin); vm_write16(silent_desc + 8, 1);
    vm_write64(event_desc, event_bin); vm_write16(event_desc + 8, 1);
    vm_write64(event_desc + 0x30, 2); // SPU port, as user data in the real descriptor.
    vm_write64(event_desc + 0x38, 0xC0CAFEEDu);
    vm_write64(commands, silent_desc);
    vm_write64(commands + 8, event_desc);
    vm_write64(commands + 16, 127); // END carries no user-event command.
    int32_t created;
    if (argc > 1) {
        constexpr uint32_t attribute = 0x1B000;
        vm_write32(attribute + 8, commands);
        vm_write32(attribute + 12, (128u << 16) | 1u);
        created = cellSpursCreateJobChainWithAttribute(first, chain, attribute);
    } else {
        created = cellSpursCreateJobChain(first, chain, commands, 128, 1, 0, 1, 0);
    }
    if (created || cellSpursRunJobChain(chain)) return 2;
    cellSpursJoinJobChain(chain);
    unsigned wrong = drain(queues[0], 0x30000), audio = drain(queues[1], 0x31000);
    bool payload = vm_read64(0x31000) == 0xFFFFFFFF53505501ull &&
        vm_read64(0x31008) == 0 && vm_read64(0x31010) == (2ull << 32) &&
        vm_read64(0x31018) == 0xC0CAFEEDu;
    std::fprintf(stderr, "silent runs=%u event runs=%u wrong queue=%u audio queue=%u payload=%d\n",
                 silent_runs.load(), event_runs.load(), wrong, audio, payload);
    return silent_runs == 1 && event_runs == 1 && wrong == 0 && audio == 1 && payload ? 0 : 1;
}
