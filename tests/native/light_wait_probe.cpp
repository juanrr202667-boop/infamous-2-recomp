#include "ppu_recomp.h"
#include <cstdio>
#include <cstring>
#include <io.h>
#include <windows.h>
extern "C" {
uint8_t memory[0x10000]; uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
void i2_light_wait_trace(ppu_context*, unsigned);
}
static void put16(unsigned a, unsigned v) { memory[a] = v >> 8; memory[a + 1] = v; }
static void put32(unsigned a, unsigned v) { memory[a]=v>>24; memory[a+1]=v>>16; memory[a+2]=v>>8; memory[a+3]=v; }
int main(int argc, char** argv) {
    const bool on = argc < 2 || strcmp(argv[1], "off") != 0;
    const bool invalid = argc > 1 && strcmp(argv[1], "invalid") == 0;
    const bool second = argc > 1 && strcmp(argv[1], "second") == 0;
    _putenv_s("I2_JOB_TRACE_AFTER_MS", on ? "1" : "");
    _putenv_s("I2_JOB_TRACE_INTERVAL_MS", "1");
    _putenv_s("I2_JOB_TRACE_MAX", "2");
    FILE* f = tmpfile();
    char temp_fallback_path[MAX_PATH] = {0};
    if (!f) {
        char temp_dir[MAX_PATH] = {0};
        if (GetTempPathA(MAX_PATH, temp_dir) != 0) {
            if (GetTempFileNameA(temp_dir, "lwt", 0, temp_fallback_path) != 0) {
                f = fopen(temp_fallback_path, "w+b");
            }
        }
    }
    if (!f) {
        snprintf(temp_fallback_path, sizeof(temp_fallback_path), "light_wait_tmp_%lu.tmp", (unsigned long)GetCurrentProcessId());
        f = fopen(temp_fallback_path, "w+b");
    }
    if (!f) return 2;
    int saved = _dup(_fileno(stderr)); if (saved < 0 || _dup2(_fileno(f), _fileno(stderr))) return 2;
    ppu_context c{}; c.thread_id=1; c.gpr[31]=0x1000; c.gpr[3]=0x2000; c.lr=0x1234;
    put32(0x1000+0x190,0x2000); put32(0x1000+0x2c4,7); memory[0x1000+0x30e]=1;
    memory[0x2000+0x610]=3; memory[0x2000+0x611]=8; put16(0x2000+0x612,42); memory[0x2000+0x66c]=1;
    put32(0x3000+0x80,0x4000); memory[0x3000+0xb8]=second?7:8; memory[0x3000+0xd4]=second?8:9;
    for (unsigned i=0;i<7;i++) put16(0x4000+0x42+(second?16:0)+2*i, i==0?40:0xffff);
    i2_light_wait_trace(&c,0); i2_light_wait_trace(&c,3);
    uint64_t t0 = GetTickCount64();
    while (GetTickCount64() - t0 < 20) Sleep(2);
    i2_light_wait_trace(&c,0); c.gpr[3]=0x3000; c.gpr[4]=invalid?0xfffffff0:0x2610;
    i2_light_wait_trace(&c,1); c.gpr[3]=0; i2_light_wait_trace(&c,2);
    c.gpr[3]=1; i2_light_wait_trace(&c,3); fflush(stderr);
    _dup2(saved,_fileno(stderr)); _close(saved); rewind(f); char data[8192]{}; fread(data,1,sizeof(data)-1,f); fclose(f);
    if (temp_fallback_path[0] != '\0') remove(temp_fallback_path);
    if (!on) return strstr(data,"[i2-job-wait]") ? 1 : 0;
    if (invalid) return strstr(data,"lanes_valid=0") && strstr(data,"lane_mask=00") && strstr(data,"wws_result=0") ? 0 : 1;
    const bool good = strstr(data,"object=00001000 job=00002000") && strstr(data,"job_state=1 job_index=3") && strstr(data,"ticket=42") &&
        strstr(data,second?"gen0=7 gen1=8":"gen0=8 gen1=9") && strstr(data,second?"sel=1 min=40":"sel=0 min=40") && strstr(data,"ptr=00004000 lanes=40,65535,65535,65535,65535,65535,65535 lane_mask=7F") && strstr(data,"wws_result=0") && strstr(data,"job_result=1") && c.lr==0x1234;
    printf("light wait observed=%d\n", good); return good?0:1;
}
