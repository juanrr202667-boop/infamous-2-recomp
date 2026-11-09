/* Guest-memory LFQueue producer derived from the BCUS98125 SPU consumer.
 * Supports sequential PPU producers and direction 3. Other producer protocols
 * must be derived before enabling them; never report success without a push. */
#include "ppu_recomp.h"
#include "cellSpurs.h"
#include "spu_coherency.h"
#include "win32_compat.h"
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <unordered_set>

extern "C" uint8_t* vm_base;
extern "C" void ps3_hle_register_ctx(uint32_t, const char*, void (*)(ppu_context*));
extern "C" void spu_taskset_signal_task(uint32_t, uint32_t);

static bool i2_fast_catalog_enabled(void)
{
    static const bool enabled = [] {
        const char* value = std::getenv("I2_FAST_CATALOG");
        return value && (std::strcmp(value, "1") == 0 || std::strcmp(value, "true") == 0);
    }();
    return enabled;
}

namespace {
struct TasksetRecord { uint32_t ea = 0, spurs = 0, workload = 0; };
TasksetRecord taskset_records[128];
std::mutex taskset_mutex;
// Hold through signal delivery, so destruction cannot retire a resolved
// taskset between queue publication and its pending notification.
std::mutex taskset_lifecycle_mutex;
std::unordered_set<uint32_t> retired_tasksets;
uint16_t read16(uint32_t a) { return uint16_t((vm_base[a] << 8) | vm_base[a + 1]); }
uint32_t read32(uint32_t a)
{
    return (uint32_t(vm_base[a]) << 24) | (uint32_t(vm_base[a + 1]) << 16) |
           (uint32_t(vm_base[a + 2]) << 8) | vm_base[a + 3];
}
void write16(uint32_t a, uint16_t v)
{
    vm_base[a] = uint8_t(v >> 8); vm_base[a + 1] = uint8_t(v);
}
void result(ppu_context* ctx, int32_t value) { ctx->gpr[3] = uint64_t(int64_t(value)); }
uint32_t resolve_taskset(uint32_t owner, uint16_t id)
{
    if (!owner || (owner & 126) || (id & 255) >= 128) return 0;
    uint32_t task = id & 255;
    uint32_t taskset = owner;
    std::lock_guard<std::mutex> lock(taskset_mutex);
    if (owner & 1) {
        taskset = 0;
        for (const auto& record : taskset_records) {
            if (!record.ea || record.spurs != (owner & ~1u) || record.workload != (id >> 8)) continue;
            if (read32(record.ea + 0x60) || read32(record.ea + 0x64) != record.spurs ||
                read32(record.ea + 0x74) != record.workload) continue;
            if (!(read32(record.ea + 0x30 + 4 * (task / 32)) & (0x80000000u >> (task % 32)))) continue;
            if (taskset) return 0; // ambiguous workload IDs in the upstream HLE
            taskset = record.ea;
        }
    }
    return taskset && !retired_tasksets.count(taskset) &&
                      (read32(taskset + 0x30 + 4 * (task / 32)) &
                      (0x80000000u >> (task % 32))) ? taskset : 0;
}
void remember_taskset(uint32_t ea, uint32_t spurs)
{
    std::lock_guard<std::mutex> lock(taskset_mutex);
    retired_tasksets.erase(ea);
    for (auto& record : taskset_records) {
        if (record.ea == ea) {
            record = {ea, spurs, read32(ea + 0x74)};
            return;
        }
    }
    for (auto& record : taskset_records) {
        if (!record.ea) {
            record = {ea, spurs, read32(ea + 0x74)};
            return;
        }
    }
    std::fprintf(stderr, "[i2-lfqueue] taskset registry full; owner resolution unavailable\n");
}
void create_taskset(ppu_context* ctx)
{
    std::lock_guard<std::mutex> lifecycle(taskset_lifecycle_mutex);
    uint32_t spurs = uint32_t(ctx->gpr[3]), taskset = uint32_t(ctx->gpr[4]);
    int32_t rc = cellSpursCreateTaskset(reinterpret_cast<CellSpurs*>(uintptr_t(spurs)),
        reinterpret_cast<CellSpursTaskset*>(uintptr_t(taskset)), ctx->gpr[5],
        reinterpret_cast<const uint8_t*>(uintptr_t(ctx->gpr[6])), uint32_t(ctx->gpr[7]));
    if (!rc) remember_taskset(taskset, spurs);
    result(ctx, rc);
}
void create_taskset_attribute(ppu_context* ctx)
{
    std::lock_guard<std::mutex> lifecycle(taskset_lifecycle_mutex);
    uint32_t spurs = uint32_t(ctx->gpr[3]), taskset = uint32_t(ctx->gpr[4]);
    int32_t rc = cellSpursCreateTasksetWithAttribute(reinterpret_cast<CellSpurs*>(uintptr_t(spurs)),
        reinterpret_cast<CellSpursTaskset*>(uintptr_t(taskset)),
        reinterpret_cast<const CellSpursTasksetAttribute*>(uintptr_t(ctx->gpr[5])));
    if (!rc) remember_taskset(taskset, spurs);
    result(ctx, rc);
}
void retire_taskset(ppu_context* ctx, bool destroy)
{
    std::lock_guard<std::mutex> lifecycle(taskset_lifecycle_mutex);
    uint32_t ea = uint32_t(ctx->gpr[3]);
    // Same lock order as push: lock-line, then registry. The upstream
    // lifecycle calls currently write host fields without taking this lock.
    spu_lockline_lock();
    auto* taskset = reinterpret_cast<CellSpursTaskset*>(uintptr_t(ea));
    int32_t rc = destroy ? cellSpursDestroyTaskset(taskset) : cellSpursShutdownTaskset(taskset);
    if (!rc) {
        std::lock_guard<std::mutex> lock(taskset_mutex);
        for (auto& record : taskset_records) if (record.ea == ea) record = {};
        retired_tasksets.insert(ea);
        spu_coh_notify_write(ea);
    }
    spu_lockline_unlock();
    result(ctx, rc);
}
void destroy_taskset(ppu_context* ctx) { retire_taskset(ctx, true); }
void shutdown_taskset(ppu_context* ctx) { retire_taskset(ctx, false); }

void push(ppu_context* ctx)
{
    uint32_t queue = uint32_t(ctx->gpr[3]), data = uint32_t(ctx->gpr[4]);
    if (!queue || !data) { result(ctx, CELL_SPURS_TASK_ERROR_NULL_POINTER); return; }
    if ((queue & 127) || (data & 15)) { result(ctx, CELL_SPURS_TASK_ERROR_ALIGN); return; }
    if (ctx->gpr[5] != 3) { result(ctx, CELL_SPURS_TASK_ERROR_NOSYS); return; }

    // Descriptor decoding alone does not establish the consumer completion
    // protocol. Preserve the guest queue until that protocol is verified.
    if (queue == 0x47D7FD00u && i2_fast_catalog_enabled()) {
        static std::atomic_flag reported = ATOMIC_FLAG_INIT;
        if (!reported.test_and_set(std::memory_order_relaxed)) {
            std::fprintf(stderr, "[i2-lfqueue-fast] completion protocol unverified; using guest queue\n");
        }
    }

    for (;;) {
        std::unique_lock<std::mutex> lifecycle(taskset_lifecycle_mutex);
        spu_lockline_lock();
        uint32_t size = read32(queue + 0x10), depth = read32(queue + 0x14);
        uint32_t buffer = read32(queue + 0x1C);
        uint32_t modulus = depth * 2;
        if (read32(queue + 0x24) != 3 || !size || (size & 15) || size > 16384 ||
            !depth || depth > 32767 || !buffer || read32(queue + 0x18)) {
            spu_lockline_unlock(); result(ctx, CELL_SPURS_TASK_ERROR_INVAL); return;
        }
        uint16_t completed = read16(queue), ticket = read16(queue + 0x0E);
        if (completed >= modulus || ticket >= modulus || read16(queue + 8) != ticket ||
            read16(queue + 0x0A)) {
            spu_lockline_unlock(); result(ctx, CELL_SPURS_TASK_ERROR_NOSYS); return;
        }
        uint32_t occupied = (ticket + modulus - completed) % modulus;
        if (occupied >= depth) {
            // Signal delivery acquires the lock-line lock. Release it before
            // notifying, while lifecycle still protects the taskset records.
            spu_lockline_unlock();
            static std::atomic_flag full_reported = ATOMIC_FLAG_INIT;
            const char* trace = std::getenv("I2_LFQUEUE_TRACE_FULL");
            if (trace && std::strcmp(trace, "1") == 0 &&
                !full_reported.test_and_set(std::memory_order_relaxed)) {
                std::fprintf(stderr, "[i2-lfqueue-full] queue=%08X completed=%u ticket=%u depth=%u\n",
                             queue, completed, ticket, depth);
            }
            for (const auto& rec : taskset_records) {
                if (rec.ea) spu_taskset_signal_task(rec.ea, 0);
            }
            lifecycle.unlock();
            SwitchToThread();
            continue;
        }
        uint64_t destination = uint64_t(buffer) + uint64_t(ticket % depth) * size;
        if (destination + size > 0x100000000ull || uint64_t(data) + size > 0x100000000ull) {
            spu_lockline_unlock(); result(ctx, CELL_SPURS_TASK_ERROR_FAULT); return;
        }
        uint16_t next = uint16_t((ticket + 1) % modulus);
        uint16_t control = read16(queue + 4);
        uint32_t head = control & 31, mid = (control >> 5) & 31, tail = (control >> 10) & 31;
        uint16_t pop_reserved = read16(queue + 6);
        uint32_t pending = (head + 30 - tail) % 30;
        if (head >= 30 || mid >= 30 || tail >= 30 || pending > 15 || pop_reserved >= modulus) {
            spu_lockline_unlock(); result(ctx, CELL_SPURS_TASK_ERROR_INVAL); return;
        }
        uint32_t available = (next + modulus - pop_reserved) % modulus;
        uint32_t assigned = (tail + 30 - mid) % 30;
        uint32_t count = available > assigned ? available - assigned : 0;
        if (count > pending) count = pending;
        uint16_t ids[15]{};
        uint32_t tasksets[15]{};
        for (uint32_t i = 0; i < count; ++i) {
            ids[i] = read16(queue + 0x32 + 2 * (tail % 15));
            tasksets[i] = read32(queue + 0x70) ? 0 : resolve_taskset(read32(queue + 0x74), ids[i]);
            if (!tasksets[i]) {
                spu_lockline_unlock(); result(ctx, CELL_SPURS_TASK_ERROR_NOSYS); return;
            }
            tail = (tail + 1) % 30;
        }
        std::memcpy(vm_base + destination, vm_base + data, size);
        write16(queue + 0x0E, next);
        write16(queue + 8, next);
        write16(queue + 4, uint16_t((control & 0x83FF) | (tail << 10)));
        spu_coh_notify_write(queue);
        spu_lockline_unlock();
        static const bool trace_push = [] {
            const char* value = std::getenv("I2_LFQUEUE_TRACE_PUSH");
            return value && std::strcmp(value, "1") == 0;
        }();
        if (trace_push) {
            std::fprintf(stderr, "[i2-lfqueue-state] queue=%08X ticket=%u completed=%u pop_reserved=%u waiter=%04X wake=%u first_id=%04X taskset=%08X\n",
                         queue, ticket, completed, pop_reserved, control, count,
                         count ? ids[0] : 0, count ? tasksets[0] : 0);
        }
        for (uint32_t i = 0; i < count; ++i) spu_taskset_signal_task(tasksets[i], ids[i] & 255);
        if (count == 0) {
            std::lock_guard<std::mutex> lock(taskset_mutex);
            for (const auto& rec : taskset_records) {
                if (rec.ea) spu_taskset_signal_task(rec.ea, 0);
            }
        }
        std::fprintf(stderr, "[i2-lfqueue] push q=0x%08X ticket=%u bytes=%u\n", queue, ticket, size);
        result(ctx, 0); return;
    }
}
}

extern "C" void i2_register_lfqueue(void)
{
    ps3_hle_register_ctx(0x8A85674Du, "i2_cellSpursLFQueuePushBody", push);
    ps3_hle_register_ctx(0x52CC6C82u, "i2_cellSpursCreateTaskset", create_taskset);
    ps3_hle_register_ctx(0xC10931CBu, "i2_cellSpursCreateTasksetWithAttribute", create_taskset_attribute);
    ps3_hle_register_ctx(0x1EA5E4CFu, "i2_cellSpursDestroyTaskset", destroy_taskset);
    ps3_hle_register_ctx(0xA789E631u, "i2_cellSpursShutdownTaskset", shutdown_taskset);
}
