#include "ppu_recomp.h"
#include <cstdio>
#include <cstring>
#include <io.h>
#include <windows.h>
extern "C" {
uint8_t memory[0x10000]; uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
void i2_light_callsite_trace(ppu_context*, unsigned, unsigned);
}
int main(int argc, char** argv) {
    bool on = argc < 2 || strcmp(argv[1], "off") != 0;
    _putenv_s("I2_LIGHT_CALL_AFTER_MS", on ? "60" : "");
    _putenv_s("I2_LIGHT_CALL_INTERVAL_MS", "30");
    _putenv_s("I2_LIGHT_CALL_MAX", "8");
    FILE* f = tmpfile(); if (!f) return 2;
    int saved = _dup(_fileno(stderr)); if (saved < 0 || _dup2(_fileno(f), _fileno(stderr))) return 2;
    ppu_context c{}; c.thread_id = 3; c.lr = 0x1DB718; c.gpr[3] = 0x1234;
    ppu_context d{}; d.thread_id = 5; d.lr = 0x1DB97C;
    i2_light_callsite_trace(&c, 0, 0x1DB718);
    HANDLE worker = CreateThread(nullptr, 0, [](LPVOID p) -> DWORD {
        auto* c = static_cast<ppu_context*>(p);
        i2_light_callsite_trace(c, 0, 0x1DB97C); Sleep(200);
        i2_light_callsite_trace(c, 1, 0x1DB97C); return 0;
    }, &d, 0, nullptr);
    if (!worker) return 2;
    Sleep(140);
    i2_light_callsite_trace(&c, 0, 0x1DB97C); Sleep(50);
    i2_light_callsite_trace(&c, 1, 0x1DB97C); Sleep(50);
    i2_light_callsite_trace(&c, 1, 0x1DB718); Sleep(60);
    WaitForSingleObject(worker, INFINITE); CloseHandle(worker);
    fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved);
    rewind(f); char data[16384]{}; fread(data, 1, sizeof(data)-1, f); fclose(f);
    if (!on) { bool quiet = !strstr(data, "[i2-light-call]"); printf("off quiet=%d\n", quiet); return quiet ? 0 : 1; }
    const char* outer = strstr(data, "tid=3 active=001DB718 depth=1");
    const char* inner = outer ? strstr(outer, "tid=3 active=001DB97C depth=2") : nullptr;
    const char* restored = inner ? strstr(inner, "tid=3 active=001DB718 depth=1") : nullptr;
    const char* idle = restored ? strstr(restored, "tid=3 idle") : nullptr;
    const char* per_site = idle ? strstr(idle, "enter718=1 return718=1 enter97C=1 return97C=1") : nullptr;
    const char* concurrent = strstr(data, "tid=5 active=001DB97C depth=1");
    bool unchanged = c.lr == 0x1DB718 && c.gpr[3] == 0x1234;
    printf("light outer=%d inner=%d restored=%d idle=%d concurrent=%d guest=%d\n", !!outer, !!inner, !!restored, !!idle, !!concurrent, unchanged);
    return outer && inner && restored && idle && per_site && concurrent && unchanged ? 0 : 1;
}
