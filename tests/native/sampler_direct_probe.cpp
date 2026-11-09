/* Synthetic host threads exercise the real sampling loop and fallback. */
#include "ppu_recomp.h"
#include <atomic>
#include <cstdio>
#include <cstring>
#include <io.h>
#include "../../tools/ps3recomp/runtime/ppu/ppu_loader.cpp"

static DWORD WINAPI guest_thread(void*);
static DWORD WINAPI decoy_thread(void*);
extern "C" {
uint8_t memory[0x10000];
uint8_t* vm_base = memory;
const func_entry function_table[] = {
    {0x1234, (void (*)(ppu_context*))0x10000000},
    {0x5678, (void (*)(ppu_context*))0x10000100},
    {0x00314BC8, (void (*)(ppu_context*))guest_thread},
    {0x9999, (void (*)(ppu_context*))((uintptr_t)guest_thread + 0x400)},
    {0xABCD, (void (*)(ppu_context*))decoy_thread},
    {0xABCE, (void (*)(ppu_context*))((uintptr_t)decoy_thread + 0x400)}
};
const uint64_t function_table_count = 6;
}
static std::atomic<bool> ready{false}, stop{false};
static bool guest_work = false;
static DWORD WINAPI decoy_thread(void*) {
    while (!stop.load()) {}
    return 0;
}
static DWORD WINAPI guest_thread(void*) {
    volatile uintptr_t guest_return = 0x10000008;
    ps3_sampler_start();
    ready = true;
    while (!stop.load()) { if (!guest_return) break; if (!guest_work) Sleep(5); }
    return 0;
}
int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "boundaries") == 0) {
        DWORD64 image = 0;
        const uintptr_t start = (uintptr_t)guest_thread;
        RUNTIME_FUNCTION* rf = RtlLookupFunctionEntry(start, &image, nullptr);
        if (!rf || image + rf->BeginAddress != start) return 2;
        s_rip_target = start; s_rip_after = 100; s_rip_max = 1;
        samp_rip_sample(start, 99, 0);
        samp_rip_sample(start - 1, 100, 0);
        samp_rip_sample(image + rf->EndAddress, 100, 0);
        if (s_rip_used != 0) return 1;
        samp_rip_sample(start, 100, 0);
        if (s_rip_used != 1 || s_rip_bucket[0] != 0 || s_rip_count[0] != 1) return 1;
        s_rip_windows = 1; samp_rip_sample(start, 200, 0);
        return s_rip_count[0] == 1 ? 0 : 1;
    }
    bool direct = argc > 1 && std::strcmp(argv[1], "direct") == 0;
    const bool rip_mode = argc > 1 && std::strcmp(argv[1], "rip") == 0;
    guest_work = rip_mode || (argc > 1 && std::strcmp(argv[1], "guest") == 0);
    direct = direct || guest_work;
    _putenv_s("PS3_SAMPLE", "5"); _putenv_s("PS3_SAMPLE_MAIN", "1");
    _putenv_s("PS3_SAMPLE_DIRECT_ONLY", direct ? "1" : "");
    _putenv_s("PS3_SAMPLE_RIP_AFTER_MS", rip_mode ? "100" : "");
    _putenv_s("PS3_SAMPLE_RIP_WINDOW_MS", "100");
    _putenv_s("PS3_SAMPLE_RIP_MAX_WINDOWS", "2");
    FILE* f = std::tmpfile();
    char temp_fallback_path[MAX_PATH] = {0};
    if (!f) {
        char temp_dir[MAX_PATH] = {0};
        if (GetTempPathA(MAX_PATH, temp_dir) != 0) {
            if (GetTempFileNameA(temp_dir, "smp", 0, temp_fallback_path) != 0) {
                f = std::fopen(temp_fallback_path, "w+b");
            }
        }
    }
    if (!f) {
        std::snprintf(temp_fallback_path, sizeof(temp_fallback_path), "sampler_tmp_%lu.tmp", (unsigned long)GetCurrentProcessId());
        f = std::fopen(temp_fallback_path, "w+b");
    }
    if (!f) return 2;
    int saved = _dup(_fileno(stderr));
    if (saved < 0 || _dup2(_fileno(f), _fileno(stderr))) return 2;
    HANDLE worker = CreateThread(nullptr, 0, guest_thread, nullptr, 0, nullptr);
    if (!worker) return 2;
    while (!ready.load()) Sleep(1);
    HANDLE decoy = CreateThread(nullptr, 0, decoy_thread, nullptr, 0, nullptr);
    if (!decoy) return 2;
    Sleep(750); stop = true; WaitForSingleObject(worker, INFINITE); CloseHandle(worker);
    WaitForSingleObject(decoy, INFINITE); CloseHandle(decoy);
    fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved);
    std::rewind(f); char data[16384]{}; std::fread(data, 1, sizeof(data)-1, f); fclose(f);
    if (temp_fallback_path[0] != '\0') std::remove(temp_fallback_path);
    const char* report = std::strstr(data, "[samp] ");
    unsigned long long total = 0, guest = 0;
    while (report && std::sscanf(report, "[samp] %llu samples, %llu in guest code", &total, &guest) != 2)
        report = std::strstr(report + 7, "[samp] ");
    bool pass = report && total > 0 && (direct && !guest_work ? guest == 0 : guest > 0);
    bool first_window = false, second_window = false, third_window = false, bucket = false;
    if (rip_mode) {
        const char* first = std::strstr(data, "[samp-rip] window=");
        const char* second = first ? std::strstr(first + 1, "[samp-rip] window=") : nullptr;
        const char* third = second ? std::strstr(second + 1, "[samp-rip] window=") : nullptr;
        first_window = first != nullptr;
        second_window = second != nullptr;
        third_window = third != nullptr;
        bucket = std::strstr(data, "bucket=") != nullptr;
        // OS scheduling may miss this short function; the boundaries mode
        // checks bucket attribution deterministically.
        pass = pass && first_window && second_window && !third_window;
    } else pass = pass && !std::strstr(data, "[samp-rip]");
    std::printf("sampler direct=%d total=%llu guest=%llu first=%d second=%d third=%d bucket=%d pass=%d\n",
                direct, total, guest, first_window, second_window, third_window, bucket, pass);
    if (!pass) std::fprintf(stderr, "%s", data);
    return pass ? 0 : 1;
}
