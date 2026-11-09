#include <cstdio>
#include <cstring>
#include <cstdint>
#include <io.h>
#include <windows.h>
#if __has_include("pm_quantum_observer.h") // Allows the red test before the observer exists.
#include "pm_quantum_observer.h"
#else
static void i2_pm_quantum_trace(uint32_t, uint32_t, uint64_t, unsigned, int, uint32_t) {}
#endif
int main(int argc, char** argv) {
    const bool enabled = argc < 2 || strcmp(argv[1], "off") != 0;
    _putenv_s("I2_PM0_AFTER_MS", enabled ? "40" : "");
    _putenv_s("I2_PM0_INTERVAL_MS", "25");
    _putenv_s("I2_PM0_MAX", "8");
    _putenv_s("I2_PM0_ARG", "0x00A93900");
    FILE* f = tmpfile(); if (!f) return 2;
    int saved = _dup(_fileno(stderr)); if (saved < 0 || _dup2(_fileno(f), _fileno(stderr))) return 2;
    SetLastError(333);
    i2_pm_quantum_trace(0, 0, 0xA93900, 0, 0, 0);
    i2_pm_quantum_trace(1, 0, 0xA93900, 0, 0, 0);
    i2_pm_quantum_trace(0, 0, 0xA93901, 0, 0, 0);
    bool unchanged = GetLastError() == 333;
    Sleep(100);
    i2_pm_quantum_trace(0, 0, 0xA93900, 1, 7, 0x9c0);
    Sleep(80);
    fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved);
    rewind(f); char data[4096]{}; fread(data, 1, sizeof(data) - 1, f); fclose(f);
    if (!enabled) return unchanged && !strstr(data, "[i2-pm0]") ? 0 : 1;
    bool active = strstr(data, "spu=0 arg=00A93900 entered=1 returned=0") != nullptr;
    bool returned = strstr(data, "spu=0 arg=00A93900 entered=1 returned=1") != nullptr;
    bool result = strstr(data, "last_halt=7 last_pc=000009C0") != nullptr;
    printf("active=%d returned=%d result=%d unchanged=%d\n", active, returned, result, unchanged);
    return active && returned && result && unchanged ? 0 : 1;
}
