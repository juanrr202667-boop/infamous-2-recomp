/* Real NID adapter and staged cellSpurs implementation; synthetic guest RAM. */
#include "ppu_recomp.h"
#include "cellSpurs.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
extern "C" void _cellSpursAttributeInitialize();
extern "C" void _cellSpursWorkloadAttributeInitialize();
extern "C" void ps3_hle_register(uint32_t, const char*, void*);
extern "C" void ps3_hle_call(uint32_t, ppu_context*);
static uint8_t memory[0x80000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

static uint64_t received[8];
extern "C" uint64_t capture_eight(uint64_t a, uint64_t b, uint64_t c, uint64_t d,
                                  uint64_t e, uint64_t f, uint64_t g, uint64_t h) {
    const uint64_t args[] = {a, b, c, d, e, f, g, h};
    std::memcpy(received, args, sizeof(args));
    return 0x1122334455667788ull;
}

static int workload_case(const char* mode) {
    const bool null_case = !std::strcmp(mode, "null");
    const uint32_t count = null_case ? 5 : std::strtoul(mode, nullptr, 10);
    if (count != 1 && count != 5 && count != 6) return 2;
    constexpr uint32_t ea = 0x20000, priority_ea = 0x21000, sp = 0x70000;
    std::memset(memory, 0xCC, sizeof(memory));
    for (unsigned i = 0; i < 8; ++i) memory[priority_ea + i] = uint8_t(i + 1);
    ppu_context ctx{};
    ctx.gpr[1] = sp;
    ctx.gpr[2] = 0x9988776655443322ull;
    ctx.gpr[3] = null_case ? 0 : ea;
    ctx.gpr[4] = 2;
    ctx.gpr[5] = 0x00330000;
    ctx.gpr[6] = 0x24000;
    ctx.gpr[7] = 0x1230;
    ctx.gpr[8] = 0x0102030455667788ull;
    ctx.gpr[9] = priority_ea;
    ctx.gpr[10] = 1;
    vm_write64(sp + 0x68, 0xDEADDEAD00000003ull);
    vm_write64(sp + 0x70, 0xAABBCCDD00000000ull | count);
    vm_write64(sp + 0x78, 0xBEEFBEEF00000002ull);
    ps3_hle_register(0xEFEB2679u, "_cellSpursWorkloadAttributeInitialize",
                     (void*)_cellSpursWorkloadAttributeInitialize);
    ps3_hle_call(0xEFEB2679u, &ctx);
    if (null_case) {
        if ((uint32_t)ctx.gpr[3] != CELL_SPURS_CORE_ERROR_NULL_POINTER) return 1;
        for (unsigned i = 0; i < 0x38; ++i) if (memory[ea + i] != 0xCC) return 1;
        std::puts("PASS null workload attribute rejected");
        return 0;
    }
    if (ctx.gpr[3] != 0 || vm_read32(ea + 0x24) != count) {
        std::fprintf(stderr, "FAIL workload requested=%u returned=%llu maxContention=%u\n",
                     count, (unsigned long long)ctx.gpr[3], vm_read32(ea + 0x24));
        return 1;
    }
    if (vm_read32(ea) != 2 || vm_read32(ea + 4) != 0x00330000 ||
        vm_read32(ea + 8) != 0x24000 || vm_read32(ea + 12) != 0x1230 ||
        vm_read64(ea + 16) != 0x0102030455667788ull || vm_read32(ea + 0x20) != 1 ||
        std::memcmp(memory + ea + 0x18, memory + priority_ea, 8) ||
        ctx.gpr[2] != 0x9988776655443322ull ||
        vm_read64(sp + 0x28) != ctx.gpr[2]) return 1;
    for (unsigned i = 0x28; i < 0x38; ++i) if (memory[ea + i] != 0) return 1;
    if (memory[ea - 1] != 0xCC || memory[ea + 0x38] != 0xCC) return 1;
    std::printf("PASS workload requested=%u, eight register args and TOC preserved\n", count);
    return 0;
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    if (!std::strncmp(argv[1], "workload-", 9)) return workload_case(argv[1] + 9);
    if (!std::strcmp(argv[1], "generic-eight")) {
        ppu_context ctx{};
        ctx.gpr[1] = 0x70000;
        for (unsigned i = 0; i < 8; ++i) ctx.gpr[3 + i] = 0xAABBCCDD00000000ull + i;
        ps3_hle_register(0x12345678u, "capture_eight", (void*)capture_eight);
        ps3_hle_call(0x12345678u, &ctx);
        for (unsigned i = 0; i < 8; ++i)
            if (received[i] != 0xAABBCCDD00000000ull + i) return 1;
        if (ctx.gpr[3] != 0x1122334455667788ull) return 1;
        std::puts("PASS unrelated eight-argument handler");
        return 0;
    }
    const bool null_case = !std::strcmp(argv[1], "null");
    const int count = null_case ? 5 : std::atoi(argv[1]);
    if (count != 1 && count != 5) return 2;
    constexpr uint32_t ea = 0x20000;
    std::memset(memory + ea, 0xCC, sizeof(CellSpursAttribute));
    ppu_context ctx{};
    ctx.gpr[1] = 0x70000;
    const int priority = count == 1 ? 255 : 128;
    ctx.gpr[3] = null_case ? 0 : ea; ctx.gpr[4] = 2; ctx.gpr[5] = 0x00330000;
    ctx.gpr[6] = count; ctx.gpr[7] = priority; ctx.gpr[8] = 300; ctx.gpr[9] = 1;
    ps3_hle_register(0x95180230, "_cellSpursAttributeInitialize", (void*)_cellSpursAttributeInitialize);
    ps3_hle_call(0x95180230, &ctx);
    if (null_case) {
        if ((uint32_t)ctx.gpr[3] != CELL_SPURS_CORE_ERROR_NULL_POINTER) return 1;
        std::puts("PASS null attribute rejected");
        return 0;
    }
    auto* attr = reinterpret_cast<CellSpursAttribute*>(memory + ea);
    if (ctx.gpr[3] != 0 || attr->nSpus != unsigned(count) || attr->spuPriority[0] != priority) {
        std::fprintf(stderr, "FAIL requested=%d returned=%llu nSpus=%u priority=%d\n", count,
                     (unsigned long long)ctx.gpr[3], attr->nSpus, attr->spuPriority[0]);
        return 1;
    }
    for (int i = 0; i < CELL_SPURS_MAX_SPU; ++i)
        if (attr->spuPriority[i] != priority) return 1;
    std::printf("PASS requested=%d nSpus=%u priority=%d\n", count, attr->nSpus, attr->spuPriority[0]);
    return 0;
}
