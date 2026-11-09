#include "ppu_recomp.h"
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
void ps3_hle_call(uint32_t, ppu_context*);
void ps3_hle_register_ctx(uint32_t, const char*, void (*)(ppu_context*));
}
static std::atomic<int> stage{0};
static std::atomic<bool> release_inner{false}, release_outer{false};
static std::atomic<bool> other_started{false};
static void other(ppu_context*) {
    other_started = true;
    while (!release_outer.load()) std::this_thread::yield();
}
static void inner(ppu_context*) {
    ++stage;
    while (!release_inner.load()) std::this_thread::yield();
}
static void outer(ppu_context* c) {
    c->lr = 0x2222;
    ps3_hle_call(0xFFFFFFFFu, c);
    ++stage;
    while (!release_outer.load()) std::this_thread::yield();
}
int main(int argc, char** argv) {
    const bool enabled = argc < 2 || std::strcmp(argv[1], "off") != 0;
    _putenv_s("PS3_HLE_SNAPSHOT_AFTER_MS", enabled ? "100" : "");
    _putenv_s("PS3_HLE_SNAPSHOT_INTERVAL_MS", "30");
    FILE* capture = std::tmpfile(); if (!capture) return 2;
    int saved = _dup(_fileno(stderr));
    if (saved < 0 || _dup2(_fileno(capture), _fileno(stderr))) return 2;
    ps3_hle_register_ctx(0, "outer", outer);
    ps3_hle_register_ctx(0xFFFFFFFFu, "inner", inner);
    ps3_hle_register_ctx(0x12345678u, "other", other);
    ppu_context c{}; c.thread_id = 3; c.lr = 0x1111; c.gpr[1] = 0x100;
    ppu_context d{}; d.thread_id = 5; d.lr = 0x3333; d.gpr[1] = 0x200;
    std::thread worker([&] { ps3_hle_call(0, &c); });
    std::thread concurrent([&] { ps3_hle_call(0x12345678u, &d); });
    while (stage.load() < 1 || !other_started.load()) std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(180));
    release_inner = true;
    while (stage.load() < 2) std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    release_outer = true; worker.join(); concurrent.join();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    std::fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved);
    std::rewind(capture); char data[16384]{};
    std::fread(data, 1, sizeof(data)-1, capture); std::fclose(capture);
    if (!enabled) {
        bool quiet = !std::strstr(data, "[hle-snapshot]");
        std::printf("hle snapshot off: quiet=%d\n", quiet); return quiet ? 0 : 1;
    }
    const char* a = std::strstr(data, "tid=3 nid=FFFFFFFF lr=00002222");
    const char* parallel = std::strstr(data, "tid=5 nid=12345678 lr=00003333");
    const char* b = a ? std::strstr(a, "tid=3 nid=00000000 lr=00001111") : nullptr;
    const char* idle = b ? std::strstr(b, "tid=3 idle") : nullptr;
    std::printf("hle snapshot: inner=%d outer=%d idle=%d parallel=%d\n", a!=nullptr, b!=nullptr, idle!=nullptr, parallel!=nullptr);
    return a && b && idle && parallel ? 0 : 1;
}
