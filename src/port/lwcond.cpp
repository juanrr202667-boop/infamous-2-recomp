/* Guest sys_lwcond_t is { be32 mutex_ea, be32 queue_id }. Host waiters are
 * registered before releasing the guest mutex, so a producer cannot signal
 * between the unlock and registration. No signal is retained for later waits. */
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "ps3emu/error_codes.h"
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <unordered_map>

extern "C" void ps3_hle_register_ctx(uint32_t, const char*, void (*)(ppu_context*));
extern "C" void ps3_hle_call(uint32_t, ppu_context*);

namespace {
struct Waiter {
    uint64_t thread;
    bool signaled = false;
    std::condition_variable wake;
};
struct Condition {
    uint32_t address, mutex, id;
    unsigned active = 0;
    std::deque<Waiter*> waiters;
};
std::mutex registry_lock;
std::unordered_map<uint32_t, std::shared_ptr<Condition>> conditions;
uint32_t next_id = 0x4C000000;

/* Optional boundary trace for a guest thread and producers signaling its
 * pending conditions. All callers hold registry_lock; no guest state changes. */
void trace(const char* event, ppu_context* ctx, const Condition& c, uint64_t value)
{
    static const char* setting = std::getenv("I2_LWCOND_TRACE");
    if (!setting) return;
    static const bool all = std::strcmp(setting, "all") == 0;
    static const uint64_t target = std::strtoull(setting, nullptr, 0);
    bool selected = all || ctx->thread_id == target;
    for (const auto* w : c.waiters) selected |= w->thread == target;
    static unsigned count = 0;
    if (!selected || count++ >= 2000) return;
    std::fprintf(stderr, "[i2-lwcond] %s tid=%llu cond=0x%08X mutex=0x%08X"
        " lr=0x%08X active=%u value=0x%llX\n", event,
        (unsigned long long)ctx->thread_id, c.address, c.mutex,
        (uint32_t)ctx->lr, c.active, (unsigned long long)value);
}

void result(ppu_context* ctx, uint32_t code)
{
    ctx->gpr[3] = uint64_t(int64_t(int32_t(code)));
}
bool address(uint64_t a, uint32_t bytes)
{
    return a != 0 && !(a & 3) && a <= 0x100000000ull - bytes;
}
std::shared_ptr<Condition> find(ppu_context* ctx)
{
    if (!address(ctx->gpr[3], 8)) return {};
    const uint32_t a = uint32_t(ctx->gpr[3]);
    auto it = conditions.find(a);
    if (it == conditions.end()) return {};
    auto c = it->second;
    if (vm_read32(a) != c->mutex || vm_read32(a + 4) != c->id) return {};
    return c;
}
uint32_t mutex_call(ppu_context* ctx, const char* name, uint32_t ea)
{
    ctx->gpr[3] = ea; ctx->gpr[4] = 0;
    ps3_hle_call(ps3_compute_nid(name), ctx);
    return uint32_t(ctx->gpr[3]);
}
void create(ppu_context* ctx)
{
    const uint64_t a = ctx->gpr[3], m = ctx->gpr[4];
    if (!address(a, 8) || !address(m, 24)) { result(ctx, CELL_EFAULT); return; }
    std::lock_guard lock(registry_lock);
    if (conditions.count(uint32_t(a))) { result(ctx, CELL_EBUSY); return; }
    auto c = std::make_shared<Condition>();
    c->address = uint32_t(a); c->mutex = uint32_t(m); c->id = ++next_id;
    conditions[c->address] = c;
    vm_write32(a, c->mutex); vm_write32(a + 4, c->id);
    result(ctx, 0);
}
void destroy(ppu_context* ctx)
{
    std::lock_guard lock(registry_lock);
    auto c = find(ctx);
    if (!c) { result(ctx, CELL_ESRCH); return; }
    if (c->active) { result(ctx, CELL_EBUSY); return; }
    conditions.erase(c->address);
    vm_write32(c->address, 0); vm_write32(c->address + 4, 0);
    result(ctx, 0);
}
void signal(ppu_context* ctx, bool all, bool targeted)
{
    std::lock_guard lock(registry_lock);
    auto c = find(ctx);
    if (!c) { result(ctx, CELL_ESRCH); return; }
    const uint64_t target = ctx->gpr[4];
    bool found = false;
    for (auto* w : c->waiters) {
        if (w->signaled || (targeted && w->thread != target)) continue;
        w->signaled = true; found = true; w->wake.notify_one();
        if (!all) break;
    }
    trace(all ? "signal-all" : targeted ? "signal-to" : "signal", ctx, *c, target);
    result(ctx, targeted && !found ? CELL_EPERM : 0);
}
void signal_one(ppu_context* ctx) { signal(ctx, false, false); }
void signal_all(ppu_context* ctx) { signal(ctx, true, false); }
void signal_to(ppu_context* ctx) { signal(ctx, false, true); }
void wait(ppu_context* ctx)
{
    const uint64_t timeout = ctx->gpr[4];
    std::unique_lock lock(registry_lock);
    auto c = find(ctx);
    if (!c) { result(ctx, CELL_ESRCH); return; }
    const uint32_t owner = vm_read32(c->mutex), depth = vm_read32(c->mutex + 12);
    if (!ctx->thread_id || owner != uint32_t(ctx->thread_id) || !depth) {
        result(ctx, CELL_EPERM); return;
    }
    Waiter w{ctx->thread_id};
    c->waiters.push_back(&w); ++c->active;
    trace("wait", ctx, *c, timeout);
    uint32_t code = 0;
    for (uint32_t i = 0; i < depth; ++i) {
        code = mutex_call(ctx, "sys_lwmutex_unlock", c->mutex);
        if (code) break;
    }
    if (!code) {
        if (!timeout) w.wake.wait(lock, [&] { return w.signaled; });
        else {
            const uint64_t maximum = uint64_t(std::chrono::microseconds::max().count());
            auto duration = std::chrono::microseconds(std::min(timeout, maximum));
            if (!w.wake.wait_for(lock, duration, [&] { return w.signaled; })) code = CELL_ETIMEDOUT;
        }
    }
    auto it = std::find(c->waiters.begin(), c->waiters.end(), &w);
    c->waiters.erase(it);
    lock.unlock();
    /* Dispatch through the existing handlers: semaphore, owner and recursion
     * bookkeeping must agree before the guest observes its protected state. */
    for (uint32_t i = 0; i < depth; ++i) {
        uint32_t acquired = mutex_call(ctx, "sys_lwmutex_lock", c->mutex);
        if (acquired) { code = acquired; break; }
    }
    lock.lock(); --c->active;
    trace("done", ctx, *c, code);
    ctx->gpr[4] = timeout;
    result(ctx, code);
}
}

extern "C" void i2_register_lwcond(void)
{
    ps3_hle_register_ctx(ps3_compute_nid("sys_lwcond_create"), "i2_sys_lwcond_create", create);
    ps3_hle_register_ctx(ps3_compute_nid("sys_lwcond_destroy"), "i2_sys_lwcond_destroy", destroy);
    ps3_hle_register_ctx(ps3_compute_nid("sys_lwcond_wait"), "i2_sys_lwcond_wait", wait);
    ps3_hle_register_ctx(ps3_compute_nid("sys_lwcond_signal"), "i2_sys_lwcond_signal", signal_one);
    ps3_hle_register_ctx(ps3_compute_nid("sys_lwcond_signal_all"), "i2_sys_lwcond_signal_all", signal_all);
    ps3_hle_register_ctx(ps3_compute_nid("sys_lwcond_signal_to"), "i2_sys_lwcond_signal_to", signal_to);
}
