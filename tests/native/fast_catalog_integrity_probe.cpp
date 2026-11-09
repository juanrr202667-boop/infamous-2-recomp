// Synthetic CPU fixtures exercise production inflate and LFQueue publication.
#include "ppu_context.h"
#include "cellSpurs.h"
#include "fast_inflate.h"
#include <windows.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <vector>

extern "C" {
uint8_t* vm_base = nullptr;
void i2_register_lfqueue(void);
}
static void (*queue_push)(ppu_context*) = nullptr;
static std::mutex line_lock;
static unsigned notifications = 0, events = 0, signals = 0;
extern "C" void ps3_hle_register_ctx(uint32_t nid, const char*, void (*handler)(ppu_context*)) {
    if (nid == 0x8A85674Du) queue_push = handler;
}
extern "C" void spu_lockline_lock(void) { line_lock.lock(); }
extern "C" void spu_lockline_unlock(void) { line_lock.unlock(); }
extern "C" void spu_coh_notify_write(uint32_t) { ++notifications; }
extern "C" void spu_taskset_signal_task(uint32_t, uint32_t) { ++signals; }
extern "C" s32 cellSpursCreateTaskset(CellSpurs*, CellSpursTaskset*, u64, const u8*, u32) { return CELL_SPURS_TASK_ERROR_NOSYS; }
extern "C" s32 cellSpursCreateTasksetWithAttribute(CellSpurs*, CellSpursTaskset*, const CellSpursTasksetAttribute*) { return CELL_SPURS_TASK_ERROR_NOSYS; }
extern "C" s32 cellSpursDestroyTaskset(CellSpursTaskset*) { return CELL_SPURS_TASK_ERROR_NOSYS; }
extern "C" s32 cellSpursShutdownTaskset(CellSpursTaskset*) { return CELL_SPURS_TASK_ERROR_NOSYS; }
extern "C" s32 cellSpursEventFlagSet(CellSpursEventFlag*, u16) { ++events; return 0; }

static void put16(uint32_t address, uint16_t value) {
    vm_base[address] = uint8_t(value >> 8); vm_base[address + 1] = uint8_t(value);
}
static void put32(uint32_t address, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) vm_base[address + i] = uint8_t(value >> (24 - i * 8));
}
static unsigned get16(uint32_t address) { return unsigned(vm_base[address]) * 256 + vm_base[address + 1]; }

int main(int argc, char** argv) {
    if (argc == 2 && !std::strcmp(argv[1], "overlap")) {
        uint8_t bytes[] = {1, 3, 0, 0xFC, 0xFF, 'A', 'B', 'C'};
        uint8_t before[sizeof(bytes)]; std::memcpy(before, bytes, sizeof(bytes));
        const bool ok = fast_inflate::decompress(bytes, 3, bytes, sizeof(bytes));
        std::printf("ok=%u intact=%u\n", unsigned(ok), unsigned(!std::memcmp(before, bytes, sizeof(bytes))));
        return 0;
    }
    if (argc == 4 && !std::strcmp(argv[1], "inflate")) {
        std::vector<uint8_t> input;
        for (size_t i = 0; i < std::strlen(argv[2]); i += 2) {
            char byte[3] = {argv[2][i], argv[2][i + 1], 0};
            input.push_back(uint8_t(std::strtoul(byte, nullptr, 16)));
        }
        const size_t length = std::strtoul(argv[3], nullptr, 10);
        std::vector<uint8_t> output(length + 2, 0xA5);
        const bool ok = fast_inflate::decompress(output.data() + 1, length, input.data(), input.size());
        std::printf("%u %u ", unsigned(ok), unsigned(output.front() == 0xA5 && output.back() == 0xA5));
        for (size_t i = 0; i < length; ++i) std::printf("%02x", output[i + 1]);
        std::puts("");
        return 0;
    }
    if (argc != 3 || std::strcmp(argv[1], "queue")) return 2;
    constexpr uint32_t queue = 0x47D7FD00, data = 0x30000, source = 0x40000, destination = 0x50000;
    vm_base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x100000000ull, MEM_RESERVE, PAGE_NOACCESS));
    if (!vm_base || !VirtualAlloc(vm_base + 0x20000, 0x40000, MEM_COMMIT, PAGE_READWRITE) ||
        !VirtualAlloc(vm_base + (queue & ~0xFFFFu), 0x10000, MEM_COMMIT, PAGE_READWRITE)) return 3;
    put32(queue + 0x10, 16); put32(queue + 0x14, 12);
    put32(queue + 0x1C, 0x20000); put32(queue + 0x24, 3);
    // The descriptor is opaque to the faithful queue producer, even if invalid.
    put32(data, !std::strcmp(argv[2], "invalid-descriptor") ? 0xFFFFFFF0u : source);
    put32(data + 4, destination); put32(data + 8, 32); put32(data + 12, 64);
    std::memset(vm_base + source, 0x78, 32); std::memset(vm_base + destination, 0xA5, 64);
    if (!std::strcmp(argv[2], "unsupported")) put16(queue + 0x0A, 1);
    if (!std::strcmp(argv[2], "invalid-queue")) put32(queue + 0x14, 0);
    if (!std::strcmp(argv[2], "invalid-bounds")) put32(queue + 0x1C, 0xFFFFFFF8u);
    uint8_t before[128]; std::memcpy(before, vm_base + queue, 128);
    i2_register_lfqueue();
    ppu_context context{}; context.gpr[3] = queue; context.gpr[4] = data; context.gpr[5] = 3;
    queue_push(&context);
    if (!std::strcmp(argv[2], "repeat")) {
        context.gpr[3] = queue; context.gpr[4] = data; context.gpr[5] = 3;
        queue_push(&context);
    }
    bool destination_intact = true;
    for (unsigned i = 0; i < 64; ++i) destination_intact &= vm_base[destination + i] == 0xA5;
    std::printf("result=%08x completed=%u ticket=%u reserved=%u copied=%u intact=%u same=%u notifications=%u events=%u signals=%u\n",
        unsigned(context.gpr[3]), get16(queue), get16(queue + 14), get16(queue + 6),
        unsigned(!std::memcmp(vm_base + 0x20000, vm_base + data, 16)), unsigned(destination_intact),
        unsigned(!std::memcmp(before, vm_base + queue, 128)), notifications, events, signals);
    VirtualFree(vm_base, 0, MEM_RELEASE);
    return 0;
}
