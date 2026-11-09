#include "ppu_recomp.h"
#define PPU_CONTEXT_H
#include "lv2_syscall_table.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <thread>
#include <io.h>

extern "C" {
uint8_t memory[0x10000];
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
void lv2_init_syscalls(void);
}
static std::atomic<int> entered{0};
static std::atomic<bool> release_waiters{false};
static int64_t wait_handler(ppu_context*) {
    ++entered;
    while (!release_waiters.load()) std::this_thread::yield();
    return 0;
}
int main(int argc, char** argv) {
    const bool enabled = argc < 2 || std::strcmp(argv[1], "off") != 0;
    _putenv_s("PS3_SC_SNAPSHOT_AFTER_MS", enabled ? "100" : "");
    _putenv_s("PS3_SC_SNAPSHOT_INTERVAL_MS", "50");
    FILE* capture = std::tmpfile();
    if (!capture) return 2;
    const int saved = _dup(_fileno(stderr));
    if (saved < 0 || _dup2(_fileno(capture), _fileno(stderr))) return 2;
    lv2_init_syscalls();
    lv2_syscall_register(&g_lv2_syscalls, 0, wait_handler);
    lv2_syscall_register(&g_lv2_syscalls, 7, wait_handler);
    auto call = [](unsigned tid, unsigned number, unsigned lr) {
        ppu_context c{}; c.thread_id = tid; c.gpr[11] = number; c.lr = lr;
        lv2_syscall(&c);
    };
    std::thread a(call, 3, 0, 0x1234);
    std::thread b(call, 5, 7, 0x5678);
    while (entered.load() != 2) std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(220));
    release_waiters = true; a.join(); b.join();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    std::fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved);
    std::rewind(capture); char data[16384]{};
    std::fread(data, 1, sizeof(data)-1, capture); std::fclose(capture);
    const bool zero = std::strstr(data, "tid=3 syscall=0 lr=00001234") != nullptr;
    const bool other = std::strstr(data, "tid=5 syscall=7 lr=00005678") != nullptr;
    const char* idle_at = std::strstr(data, "tid=3 idle");
    const bool idle = idle_at && !std::strstr(idle_at, "tid=3 syscall=0");
    if (!enabled) {
        const bool quiet = !std::strstr(data, "[sc-snapshot]");
        std::printf("snapshot off: quiet=%d\n", quiet);
        return quiet ? 0 : 1;
    }
    std::printf("snapshot: zero=%d other=%d idle=%d\n", zero, other, idle);
    return zero && other && idle ? 0 : 1;
}
