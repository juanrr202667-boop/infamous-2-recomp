#include "../../src/port/spin_snapshot.h"
#include <cstdio>
#include <map>
#include <cstring>

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "check failed at line %d: %s\n", __LINE__, #condition); \
    return 1; } } while (0)

int main()
{
    constexpr uint32_t queue = 0x10000, lane = 0x20000;
    std::map<uint32_t, uint32_t> memory;
    memory[queue + 0x30] = lane;
    memory[queue + 0x40] = 0x59;
    for (unsigned i = 0; i < 7; ++i) memory[queue + 0x42 + i * 2] = 0x59;
    for (unsigned i = 0; i < 5; ++i) memory[lane + (0x59 + i) * 8 + 2] = i < 4;
    auto read = [&](uint32_t ea, uint32_t& value) {
        auto it = memory.find(ea);
        if (it == memory.end()) return false;
        value = it->second;
        return true;
    };
    auto result = i2_read_spin_row(queue, 0, 0x100000000ULL, read, read);
    if (result.counters_ready != 0 || result.effective != 0x5D) {
        fprintf(stderr, "pending markers: effective=%X ready=%d; expected 5D/0\n",
                result.effective, result.counters_ready);
        return 1;
    }
    for (unsigned i = 0; i < 7; ++i) memory[queue + 0x42 + i * 2] = 0x5D;
    result = i2_read_spin_row(queue, 0, 0x100000000ULL, read, read);
    CHECK(result.counters_ready == 1 && result.advance == 4);
    memory[lane + 0x59 * 8 + 2] = 0;
    result = i2_read_spin_row(queue, 0, 0x100000000ULL, read, read);
    CHECK(result.effective == 0x59 && result.advance == 0);
    memory[lane + 0x59 * 8 + 2] = 1;
    result = i2_read_spin_row(queue, 0, 0x100000000ULL, read, read, 4);
    CHECK(result.counters_ready == -1 && !strcmp(result.status, "scan-limit"));
    memory.erase(queue + 0x4E);
    result = i2_read_spin_row(queue, 0, 0x100000000ULL, read, read);
    CHECK(result.counters_ready == -1 && !strcmp(result.status, "unreadable"));
    memory[queue + 0x4E] = 0x5D;
    unsigned reads = 0;
    auto changing_read = [&](uint32_t ea, uint32_t& value) {
        if (ea == lane + 0x59 * 8 + 2 && ++reads == 2) { value = 0; return true; }
        return read(ea, value);
    };
    result = i2_read_spin_row(queue, 0, 0x100000000ULL, read, changing_read);
    CHECK(result.counters_ready == -1 && !strcmp(result.status, "changed"));
    memory[queue + 0x30] = 0xFFFFFFF8;
    unsigned wrapped_reads = 0;
    auto no_wrap_read = [&](uint32_t ea, uint32_t& value) {
        if (ea < 0x10000) ++wrapped_reads;
        return read(ea, value);
    };
    result = i2_read_spin_row(queue, 0, 0x100000000ULL, no_wrap_read, no_wrap_read);
    CHECK(result.counters_ready == -1 && wrapped_reads == 0);
    memory[queue + 0x30] = lane;
    memory[queue + 0x40] = 0xFFFF;
    for (unsigned i = 0; i < 7; ++i) memory[queue + 0x42 + i * 2] = 0xFFFF;
    memory[lane + 0xFFFF * 8 + 2] = 1;
    memory[lane + 0x10000 * 8 + 2] = 0;
    result = i2_read_spin_row(queue, 0, 0x100000000ULL, read, read);
    CHECK(result.effective == 0x10000 && result.counters_ready == 0);
    result = i2_read_spin_row(queue, 4, 0x100000000ULL, read, read);
    CHECK(result.counters_ready == -1);
    puts("spin snapshot: PASS");
    return 0;
}
