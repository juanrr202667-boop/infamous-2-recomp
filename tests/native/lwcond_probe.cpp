/* Real guest HLE and guest mutexes, with synthetic addresses and host threads. */
#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "ps3emu/error_codes.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <thread>

extern "C" void ps3_load_prx_modules(void);
extern "C" void ppu_sysprx_register(void);
extern "C" void ps3_hle_call(uint32_t, ppu_context*);
static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}
constexpr uint32_t cond = 0x12000, mutex = 0x12100, payload = 0x12200;
using namespace std::chrono_literals;
static uint32_t call(ppu_context& ctx, const char* name, uint64_t a, uint64_t b = 0)
{
    ctx.gpr[3] = a; ctx.gpr[4] = b;
    ps3_hle_call(ps3_compute_nid(name), &ctx);
    return uint32_t(ctx.gpr[3]);
}
static ppu_context context(uint32_t tid)
{
    ppu_context c{}; c.thread_id = tid; c.gpr[1] = 0x30000 + tid * 0x100;
    return c;
}
static bool until(const std::atomic<bool>& flag)
{
    const auto limit = std::chrono::steady_clock::now() + 1s;
    while (!flag && std::chrono::steady_clock::now() < limit) std::this_thread::yield();
    return flag.load();
}
struct Waiter {
    std::atomic<bool> locked{false}, done{false};
    uint32_t result = 0xDEADBEEF, seen = 0, owner = 0, depth = 0;
    std::thread worker;
    explicit Waiter(uint32_t tid, uint64_t timeout = 2000000, bool recursive = false) : worker([this, tid, timeout, recursive] {
        auto c = context(tid);
        if (call(c, "sys_lwmutex_lock", mutex)) { done = true; return; }
        if (recursive) call(c, "sys_lwmutex_lock", mutex);
        locked = true;
        result = call(c, "sys_lwcond_wait", cond, timeout);
        owner = vm_read32(mutex); depth = vm_read32(mutex + 12);
        seen = vm_read32(payload);
        call(c, "sys_lwmutex_unlock", mutex);
        if (recursive) call(c, "sys_lwmutex_unlock", mutex);
        done = true;
    }) {}
    ~Waiter() { worker.join(); }
};
int main(int argc, char** argv)
{
    ps3_load_prx_modules(); ppu_sysprx_register();
    auto main = context(1);
    call(main, "sys_lwmutex_create", mutex);
    vm_write32(cond + 8, 0xDEADC0DE);
    if (call(main, "sys_lwcond_create", cond, mutex) ||
        vm_read32(cond) != mutex || !vm_read32(cond + 4) ||
        vm_read32(cond + 8) != 0xDEADC0DE) return 1;
    const char* mode = argc > 1 ? argv[1] : "timeout";
    bool ok = true;
    if (!std::strcmp(mode, "timeout")) {
        call(main, "sys_lwmutex_lock", mutex);
        const auto start = std::chrono::steady_clock::now();
        auto result = call(main, "sys_lwcond_wait", cond, 50000);
        auto elapsed = std::chrono::steady_clock::now() - start;
        ok = result == CELL_ETIMEDOUT && elapsed >= 40ms &&
            vm_read32(mutex) == 1 && vm_read32(mutex + 12) == 1;
        std::fprintf(stderr, "unsignaled wait rc=%08X elapsed=%lldms owner=%u\n",
            result, (long long)std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count(), vm_read32(mutex));
        call(main, "sys_lwmutex_unlock", mutex);
    } else if (!std::strcmp(mode, "invalid")) {
        ok = call(main, "sys_lwcond_wait", cond, 1) != 0 &&
            call(main, "sys_lwcond_signal", 0) != 0 &&
            call(main, "sys_lwcond_signal", cond + 0x40) != 0;
        vm_write32(cond + 0x40, mutex); vm_write32(cond + 0x44, vm_read32(cond + 4));
        ok &= call(main, "sys_lwcond_signal", cond + 0x40) != 0;
    } else if (!std::strcmp(mode, "destroy")) {
        Waiter w(2); ok &= until(w.locked);
        call(main, "sys_lwmutex_lock", mutex);
        ok &= !w.done && call(main, "sys_lwcond_destroy", cond) == CELL_EBUSY;
        call(main, "sys_lwcond_signal", cond); call(main, "sys_lwmutex_unlock", mutex);
        ok &= until(w.done) && w.result == 0;
        ok &= call(main, "sys_lwcond_destroy", cond) == 0;
        ok &= call(main, "sys_lwcond_signal", cond) != 0 && call(main, "sys_lwcond_wait", cond, 1) != 0;
        ok &= call(main, "sys_lwcond_create", cond, mutex) == 0;
        call(main, "sys_lwmutex_lock", mutex);
        ok &= call(main, "sys_lwcond_wait", cond, 10000) == CELL_ETIMEDOUT;
        call(main, "sys_lwmutex_unlock", mutex);
    } else if (!std::strcmp(mode, "delayed") || !std::strcmp(mode, "recursive") ||
               !std::strcmp(mode, "infinite") || !std::strcmp(mode, "huge-timeout")) {
        const bool recursive = !std::strcmp(mode, "recursive");
        const uint64_t timeout = !std::strcmp(mode, "infinite") ? 0 :
            !std::strcmp(mode, "huge-timeout") ? UINT64_MAX : 200000;
        Waiter w(2, timeout, recursive); ok &= until(w.locked);
        call(main, "sys_lwmutex_lock", mutex);
        call(main, "sys_lwmutex_unlock", mutex);
        std::this_thread::sleep_for(50ms);
        ok &= !w.done;
        call(main, "sys_lwmutex_lock", mutex);
        ok &= call(main, "sys_lwcond_signal", cond) == 0;
        std::this_thread::sleep_for(20ms); ok &= !w.done;
        vm_write32(payload, 0x76543210);
        call(main, "sys_lwmutex_unlock", mutex);
        ok &= until(w.done) && w.result == 0 && w.seen == 0x76543210 && w.owner == 2 && w.depth == (recursive ? 2 : 1);
    } else if (!std::strcmp(mode, "stale-signal")) {
        ok &= call(main, "sys_lwcond_signal", cond) == 0;
        call(main, "sys_lwmutex_lock", mutex);
        ok &= call(main, "sys_lwcond_wait", cond, 20000) == CELL_ETIMEDOUT;
        call(main, "sys_lwmutex_unlock", mutex);
    } else {
        Waiter first(2); ok &= until(first.locked);
        call(main, "sys_lwmutex_lock", mutex); ok &= !first.done;
        call(main, "sys_lwmutex_unlock", mutex);
        Waiter second(3); ok &= until(second.locked);
        call(main, "sys_lwmutex_lock", mutex); ok &= !first.done && !second.done;
        call(main, "sys_lwmutex_unlock", mutex);
        std::this_thread::sleep_for(50ms); ok &= !first.done && !second.done;
        call(main, "sys_lwmutex_lock", mutex);
        const bool all = !std::strcmp(mode, "all"), targeted = !std::strcmp(mode, "targeted");
        ok &= call(main, all ? "sys_lwcond_signal_all" : targeted ? "sys_lwcond_signal_to" : "sys_lwcond_signal", cond, 3) == 0;
        call(main, "sys_lwmutex_unlock", mutex);
        if (all) ok &= until(first.done) && until(second.done);
        else {
            ok &= until(targeted ? second.done : first.done);
            std::this_thread::sleep_for(20ms);
            ok &= !(targeted ? first.done : second.done);
            call(main, "sys_lwmutex_lock", mutex);
            call(main, "sys_lwcond_signal_all", cond);
            call(main, "sys_lwmutex_unlock", mutex);
            ok &= until(first.done) && until(second.done);
        }
        ok &= first.result == 0 && second.result == 0 && first.owner == 2 && second.owner == 3;
    }
    std::fprintf(stderr, "lwcond %s correct=%d\n", mode, ok);
    return ok ? 0 : 1;
}
