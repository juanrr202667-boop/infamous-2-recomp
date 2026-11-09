#include "ppu_recomp.h"
#include <cstdio>
#include <cstring>
#include <io.h>
#include <windows.h>

extern "C" {
uint8_t memory[0x10000];
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
void lv2_init_syscalls(void);
}
static void call(ppu_context& c, uint32_t lr) {
    c.lr = lr; c.gpr[11] = 141; c.gpr[3] = 1;
    lv2_syscall(&c);
}
static unsigned count(const char* data, const char* needle) {
    unsigned n = 0; while ((data = std::strstr(data, needle))) { ++n; data += std::strlen(needle); } return n;
}
int main(int argc, char** argv) {
    const bool on = argc < 2 || std::strcmp(argv[1], "off") != 0;
    _putenv_s("I2_WWS_WAIT_AFTER_MS", on ? "100" : "");
    _putenv_s("I2_WWS_WAIT_INTERVAL_MS", "40");
    _putenv_s("I2_WWS_WAIT_MAX", "3");
    FILE* f = std::tmpfile(); if (!f) return 2;
    int saved = _dup(_fileno(stderr)); if (saved < 0 || _dup2(_fileno(f), _fileno(stderr))) return 2;
    lv2_init_syscalls(); ppu_context c{}; c.thread_id = 5;
    c.gpr[28] = 0x12345678; c.gpr[27] = 1; c.gpr[29] = 42;
    call(c, 0x37108c); Sleep(120); call(c, 0xdeadbeef);
    c.lr = 0x3710e4; c.gpr[11] = 1024; lv2_syscall(&c);
    call(c, 0x37108c); call(c, 0x3710b8); Sleep(50);
    call(c, 0x3710b8); Sleep(50); call(c, 0x3710e4);
    Sleep(50); call(c, 0x37108c);
    fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved);
    std::rewind(f); char data[16384]{}; std::fread(data, 1, sizeof(data)-1, f); fclose(f);
    if (!on) { bool quiet = !std::strstr(data, "[i2-wws-wait]");
        std::printf("wws wait off: quiet=%d\n", quiet); return quiet ? 0 : 1; }
    bool correct = count(data, "[i2-wws-wait]") == 3 &&
        count(data, "tid=5") == 3 && count(data, "r28=0000000012345678 r27=0000000000000001 r29=000000000000002A") == 3 &&
        count(data, "lr=0037108C") == 1 && count(data, "lr=003710B8") == 1 &&
        count(data, "lr=003710E4") == 1 && !std::strstr(data, "lr=DEADBEEF");
    std::printf("wws wait on: correct=%d\n", correct); return correct ? 0 : 1;
}
