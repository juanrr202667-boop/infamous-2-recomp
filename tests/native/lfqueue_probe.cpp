/* Check the guest queue consumed by the title's SPU, using the real HLE. */
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include <cstdio>
#include <cstring>
#include <cstdint>
#include <atomic>
#include <chrono>
#include <thread>
#include "spu_coherency.h"
#include "cellSpurs.h"

extern "C" int32_t _cellSpursLFQueueInitialize(uint64_t, uint64_t, uint64_t,
                                              uint32_t, uint32_t, uint32_t);
extern "C" void ps3_load_prx_modules(void);
extern "C" void ps3_hle_register(uint32_t, const char*, void*);
extern "C" void ps3_hle_call(uint32_t, ppu_context*);
static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

int main(int argc, char** argv)
{
    constexpr uint32_t queue = 0x11000, buffer = 0x11080, owner = 0x20001;
    std::memset(memory + queue - 16, 0xCC, 128 + 32);
    ps3_load_prx_modules();
    ps3_hle_register(0x011EE38Bu, "_cellSpursLFQueueInitialize",
                    reinterpret_cast<void*>(_cellSpursLFQueueInitialize));
    ppu_context ctx{};
    ctx.gpr[1] = 0x30000;
    ctx.gpr[3] = owner; ctx.gpr[4] = queue; ctx.gpr[5] = buffer;
    ctx.gpr[6] = 32; ctx.gpr[7] = 12; ctx.gpr[8] = 3;
    ps3_hle_call(0x011EE38Bu, &ctx);
    bool empty = true;
    for (unsigned i = 0; i < 16; ++i) empty &= memory[queue + i] == 0;
    bool guards = true;
    for (unsigned i = 0; i < 16; ++i) guards &= memory[queue - 16 + i] == 0xCC;
    bool correct = ctx.gpr[3] == 0 && empty && guards &&
        vm_read32(queue + 0x10) == 32 && vm_read32(queue + 0x14) == 12 &&
        vm_read32(queue + 0x18) == 0 && vm_read32(queue + 0x1C) == buffer &&
        vm_read32(queue + 0x24) == 3 && vm_read32(queue + 0x70) == 0 &&
        vm_read32(queue + 0x74) == owner;
    std::fprintf(stderr, "LFQueue empty=%d direction=%u size=%u depth=%u\n",
        empty, vm_read32(queue + 0x24), vm_read32(queue + 0x10), vm_read32(queue + 0x14));
    if (!correct) return 1;
    if (argc > 1 && std::strcmp(argv[1], "invalid-init") == 0) {
        struct Invalid { uint64_t q, b, size, depth, direction; uint32_t error; };
        const Invalid cases[] = {
            {queue, buffer, 1, 12, 3, CELL_SPURS_TASK_ERROR_INVAL},
            {queue + 8, buffer, 32, 12, 3, CELL_SPURS_TASK_ERROR_ALIGN},
            {queue, buffer + 1, 32, 12, 3, CELL_SPURS_TASK_ERROR_ALIGN},
            {queue, buffer, 32, 0, 3, CELL_SPURS_TASK_ERROR_INVAL},
            {queue, buffer, 32, 32768, 3, CELL_SPURS_TASK_ERROR_INVAL},
            {queue, buffer, 16400, 12, 3, CELL_SPURS_TASK_ERROR_INVAL},
            {queue, 0xFFFFFFF0, 32, 12, 3, CELL_SPURS_TASK_ERROR_FAULT},
            {0x100011000ull, buffer, 32, 12, 3, CELL_SPURS_TASK_ERROR_FAULT},
            {queue, buffer, 32, 12, 2, CELL_SPURS_TASK_ERROR_NOSYS},
            {queue, queue, 32, 12, 3, CELL_SPURS_TASK_ERROR_INVAL}};
        uint8_t before[512]; std::memcpy(before, memory + queue, sizeof(before));
        for (const auto& c : cases) {
            ctx.gpr[3] = owner; ctx.gpr[4] = c.q; ctx.gpr[5] = c.b;
            ctx.gpr[6] = c.size; ctx.gpr[7] = c.depth; ctx.gpr[8] = c.direction;
            ps3_hle_call(0x011EE38Bu, &ctx);
            if (uint32_t(ctx.gpr[3]) != c.error || std::memcmp(before, memory + queue, sizeof(before))) {
                std::fprintf(stderr, "invalid init mutated memory or wrong result=0x%08X\n", uint32_t(ctx.gpr[3]));
                return 1;
            }
        }
        std::fprintf(stderr, "10 invalid initializations rejected without mutation\n");
        return 0;
    }
    if (argc > 1 && std::strcmp(argv[1], "unsupported") == 0) {
        for (int variant = 0; variant < 3; ++variant) {
            vm_write16(queue + 8, variant == 1 ? 1 : 0);
            vm_write16(queue + 0x0A, variant == 2 ? 1 : 0);
            uint8_t before[512]; std::memcpy(before, memory + queue, sizeof(before));
            ctx.gpr[3] = queue; ctx.gpr[4] = 0x18000; ctx.gpr[5] = variant == 0 ? 2 : 3;
            ps3_hle_call(0x8A85674Du, &ctx);
            if (int32_t(ctx.gpr[3]) != CELL_SPURS_TASK_ERROR_NOSYS ||
                std::memcmp(before, memory + queue, sizeof(before))) return 1;
        }
        std::fprintf(stderr, "unsupported mode and producer states rejected without mutation\n");
        return 0;
    }
    if (argc > 1 && std::strcmp(argv[1], "wrap") == 0) {
        vm_write16(queue, 23); vm_write16(queue + 6, 23);
        vm_write16(queue + 8, 23); vm_write16(queue + 14, 23);
        std::memset(memory + 0x18000, 0x5A, 32);
        ctx.gpr[3] = queue; ctx.gpr[4] = 0x18000; ctx.gpr[5] = 3;
        ps3_hle_call(0x8A85674Du, &ctx);
        bool wrapped = ctx.gpr[3] == 0 && vm_read16(queue + 8) == 0 &&
            vm_read16(queue + 14) == 0 && vm_read16(queue) == 23 &&
            std::memcmp(memory + buffer + 352, memory + 0x18000, 32) == 0 && memory[buffer] == 0;
        std::fprintf(stderr, "ticket 23 uses slot 11 then wraps to zero=%d\n", wrapped);
        return wrapped ? 0 : 1;
    }
    if (argc > 1 && (std::strcmp(argv[1], "full") == 0 ||
                     std::strcmp(argv[1], "full-taskset") == 0)) {
        // A registered taskset makes the full-queue retry deliver a real
        // signal. Signal delivery takes the lock-line lock itself; keeping
        // that lock in push prevents both the signal and the consumer pop.
        if (std::strcmp(argv[1], "full-taskset") == 0) {
            if (cellSpursInitialize(reinterpret_cast<CellSpurs*>(0x20000), 1, 100, 100, 0)) return 2;
            ctx.gpr[3] = 0x20000; ctx.gpr[4] = 0x24000; ctx.gpr[5] = 0;
            ctx.gpr[6] = 0; ctx.gpr[7] = 1;
            ps3_hle_call(0x52CC6C82u, &ctx);
            if (ctx.gpr[3]) return 2;
            vm_write32(0x24030, 0x80000000);
        }
        vm_write16(queue + 6, 1); vm_write16(queue + 8, 12); vm_write16(queue + 14, 12);
        std::memset(memory + buffer, 0xA5, 384); std::memset(memory + 0x18000, 0x5A, 32);
        std::atomic<bool> started{false}, done{false};
        ppu_context producer{};
        producer.gpr[3] = queue; producer.gpr[4] = 0x18000; producer.gpr[5] = 3;
        std::thread worker([&] { started.store(true); ps3_hle_call(0x8A85674Du, &producer); done.store(true); });
        while (!started.load()) std::this_thread::yield();
        std::this_thread::sleep_for(std::chrono::milliseconds(40));
        spu_lockline_lock();
        bool pending_intact = !done.load() && memory[buffer] == 0xA5 && vm_read16(queue + 8) == 12;
        // Another read reservation still does not release the first slot.
        memory[queue + 6] = 0; memory[queue + 7] = 2;
        spu_coh_notify_write(queue); spu_lockline_unlock();
        std::this_thread::sleep_for(std::chrono::milliseconds(40));
        spu_lockline_lock();
        pending_intact &= !done.load() && memory[buffer] == 0xA5;
        memory[queue] = 0; memory[queue + 1] = 1; // first pop completes
        spu_coh_notify_write(queue); spu_lockline_unlock();
        worker.join();
        bool released = producer.gpr[3] == 0 && vm_read16(queue + 8) == 13 &&
            vm_read16(queue + 14) == 13 && memory[buffer + 32] == 0xA5 &&
            std::memcmp(memory + buffer, memory + 0x18000, 32) == 0;
        std::fprintf(stderr, "full waits for completed pop=%d then reuses only released slot=%d\n", pending_intact, released);
        return pending_intact && released ? 0 : 1;
    }
    if (argc > 1 && (std::strcmp(argv[1], "destroy") == 0 ||
                     std::strcmp(argv[1], "shutdown") == 0)) {
        bool destroy = std::strcmp(argv[1], "destroy") == 0;
        constexpr uint32_t old_taskset = 0x24000, new_taskset = 0x25000, data = 0x18000;
        if (cellSpursInitialize(reinterpret_cast<CellSpurs*>(0x20000), 1, 100, 100, 0)) return 2;
        ps3_hle_register(0x1EA5E4CFu, "cellSpursDestroyTaskset", reinterpret_cast<void*>(cellSpursDestroyTaskset));
        ps3_hle_register(0xA789E631u, "cellSpursShutdownTaskset", reinterpret_cast<void*>(cellSpursShutdownTaskset));
        auto create = [&](uint32_t taskset) {
            ctx.gpr[3] = 0x20000; ctx.gpr[4] = taskset; ctx.gpr[5] = 0;
            ctx.gpr[6] = 0; ctx.gpr[7] = 1;
            ps3_hle_call(0x52CC6C82u, &ctx);
            vm_write32(taskset + 0x30, 0x80000000);
            return ctx.gpr[3] == 0;
        };
        if (!create(old_taskset)) return 2;
        vm_write16(queue + 4, 1); vm_write16(queue + 0x32, 0);
        ctx.gpr[3] = old_taskset;
        ps3_hle_call(destroy ? 0x1EA5E4CFu : 0xA789E631u, &ctx);
        if (ctx.gpr[3]) return 2;
        uint8_t before[512]; std::memcpy(before, memory + queue, sizeof(before));
        std::memset(memory + data, 0x5A, 32);
        ctx.gpr[3] = queue; ctx.gpr[4] = data; ctx.gpr[5] = 3;
        ps3_hle_call(0x8A85674Du, &ctx);
        bool rejected = int32_t(ctx.gpr[3]) == CELL_SPURS_TASK_ERROR_NOSYS &&
            std::memcmp(before, memory + queue, sizeof(before)) == 0 &&
            vm_read32(old_taskset + 0x40) == 0;
        std::fprintf(stderr, "retired taskset rejected without mutation=%d\n", rejected);
        if (!rejected) return 1;
        vm_write64(queue + 0x70, old_taskset); // direct owner must also retire
        std::memcpy(before, memory + queue, sizeof(before));
        ctx.gpr[3] = queue; ctx.gpr[4] = data; ctx.gpr[5] = 3;
        ps3_hle_call(0x8A85674Du, &ctx);
        bool direct_rejected = int32_t(ctx.gpr[3]) == CELL_SPURS_TASK_ERROR_NOSYS &&
            std::memcmp(before, memory + queue, sizeof(before)) == 0 &&
            vm_read32(old_taskset + 0x40) == 0;
        std::fprintf(stderr, "retired direct owner rejected=%d\n", direct_rejected);
        vm_write64(queue + 0x70, owner);
        if (!direct_rejected || !create(new_taskset)) return 1;
        ctx.gpr[3] = queue; ctx.gpr[4] = data; ctx.gpr[5] = 3;
        ps3_hle_call(0x8A85674Du, &ctx);
        bool replacement = ctx.gpr[3] == 0 && vm_read32(new_taskset + 0x40) == 0x80000000 &&
            vm_read32(old_taskset + 0x40) == 0 && vm_read16(queue + 8) == 1;
        std::fprintf(stderr, "replacement workload notified=%d\n", replacement);
        if (!replacement) return 1;
        ctx.gpr[3] = new_taskset;
        ps3_hle_call(destroy ? 0x1EA5E4CFu : 0xA789E631u, &ctx);
        if (ctx.gpr[3] || !create(old_taskset)) return 1;
        vm_write16(queue + 4, 1); // another waiter, re-created taskset at same EA
        vm_write16(queue + 6, 1);
        ctx.gpr[3] = queue; ctx.gpr[4] = data; ctx.gpr[5] = 3;
        ps3_hle_call(0x8A85674Du, &ctx);
        bool recreated = ctx.gpr[3] == 0 && vm_read32(old_taskset + 0x40) == 0x80000000;
        std::fprintf(stderr, "recreated taskset at same address notified=%d\n", recreated);
        return recreated ? 0 : 1;
    }
    bool tagged = argc > 1 && std::strcmp(argv[1], "tagged") == 0;
    bool notify = tagged || (argc > 1 && std::strcmp(argv[1], "notify") == 0);
    if (argc > 1 && (std::strcmp(argv[1], "push") == 0 || notify)) {
        constexpr uint32_t data = 0x18000;
        const uint8_t descriptor[32] = {
            0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,
            0x09,0x0A,0x0B,0x0C,0x0D,0x0E,0x0F,0x10,
            0x11,0x12,0x13,0x14,0x15,0x16,0x17,0x18,
            0x19,0x1A,0x1B,0x1C,0x1D,0x1E,0x1F,0x20};
        std::memcpy(memory + data, descriptor, sizeof(descriptor));
        if (notify) {
            uint32_t taskset = tagged ? 0x24000 : 0x20000;
            if (tagged) {
                if (cellSpursInitialize(reinterpret_cast<CellSpurs*>(0x20000), 1, 100, 100, 0) != 0) return 2;
                vm_write64(0x20B08, 0xDEADBEEF); // unrelated workload must remain intact
                ps3_hle_register(0x52CC6C82u, "cellSpursCreateTaskset",
                                reinterpret_cast<void*>(cellSpursCreateTaskset));
                ctx.gpr[3] = 0x20000; ctx.gpr[4] = taskset; ctx.gpr[5] = 0;
                ctx.gpr[6] = 0; ctx.gpr[7] = 1;
                ps3_hle_call(0x52CC6C82u, &ctx);
                if (ctx.gpr[3] != 0) return 2;
            }
            vm_write64(queue + 0x70, tagged ? owner : taskset);
            vm_write32(taskset + 0x30, 0x80000000); // task 0 enabled
            vm_write16(queue + 4, 1);       // one waiting consumer
            vm_write16(queue + 0x32, 0);    // workload 0, task 0
        }
        ctx.gpr[3] = queue; ctx.gpr[4] = data; ctx.gpr[5] = 3;
        ps3_hle_call(0x8A85674Du, &ctx);
        bool copied = std::memcmp(memory + buffer, descriptor, 32) == 0;
        bool published = vm_read16(queue + 8) == 1 && vm_read16(queue + 14) == 1;
        std::fprintf(stderr, "LFQueue push copied=%d published=%d result=0x%08X\n",
                     copied, published, (uint32_t)ctx.gpr[3]);
        bool signalled = !notify || (vm_read32(tagged ? 0x24040 : 0x20040) == 0x80000000 &&
                                    vm_read16(queue + 4) == 0x0401);
        if (tagged) signalled &= vm_read64(0x20B08) == 0xDEADBEEF;
        if (notify) std::fprintf(stderr, "LFQueue waiting task signalled=%d\n", signalled);
        return ctx.gpr[3] == 0 && copied && published && signalled ? 0 : 1;
    }
    return correct ? 0 : 1;
}
