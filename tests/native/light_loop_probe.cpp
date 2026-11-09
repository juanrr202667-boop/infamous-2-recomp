#include "ppu_recomp.h"
#include <cstdio>
#include <cstring>
#include <io.h>
#include <windows.h>
extern "C" {
uint8_t memory[0x10000]; uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
void i2_light_loop_trace(ppu_context*, unsigned);
}
int main(int argc, char** argv) {
    bool enabled = argc < 2 || strcmp(argv[1], "off") != 0;
    _putenv_s("I2_LIGHT_LOOP_AFTER_MS", enabled ? "50" : "");
    _putenv_s("I2_LIGHT_LOOP_INTERVAL_MS", "30");
    _putenv_s("I2_LIGHT_LOOP_MAX", "5");
    FILE* f = tmpfile(); if (!f) return 2;
    int saved = _dup(_fileno(stderr)); if (saved < 0 || _dup2(_fileno(f), _fileno(stderr))) return 2;
    ppu_context c{}; c.thread_id = 1; c.gpr[26] = 1; c.gpr[27] = 2;
    c.gpr[28] = 3; c.gpr[29] = 4; c.ctr = 0x1234; c.gpr[3] = 7;
    SetLastError(333); i2_light_loop_trace(&c, 0); i2_light_loop_trace(&c, 1);
    bool unchanged = GetLastError() == 333 && c.gpr[3] == 7 && c.ctr == 0x1234;
    Sleep(100); i2_light_loop_trace(&c, 2); Sleep(80);
    fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved);
    rewind(f); char data[4096]{}; fread(data, 1, sizeof(data) - 1, f); fclose(f);
    if (!enabled) return unchanged && !strstr(data, "[i2-light-loop]") ? 0 : 1;
    bool active = strstr(data, "scan=1 call=1 done=0") != nullptr;
    bool completed = strstr(data, "scan=1 call=1 done=1") != nullptr;
    bool fields = strstr(data, "r26=00000001 r27=00000002 r28=00000003 r29=00000004 target=00001234") != nullptr;
    bool result = strstr(data, "result=00000007") != nullptr;
    printf("active=%d completed=%d fields=%d result=%d unchanged=%d\n", active, completed, fields, result, unchanged);
    return active && completed && fields && result && unchanged ? 0 : 1;
}
