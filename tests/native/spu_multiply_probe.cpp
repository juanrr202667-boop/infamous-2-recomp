/* Execute RI10 multiplication through the real interpreter, using synthetic LS. */
#include "ppu_recomp.h"
#include "spu_context.h"
#include "spu_interp.h"
#include <cstdio>
#include <cstring>
#include <memory>

static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

struct MultiplyCase {
    const char* name;
    uint32_t instruction;
    unsigned destination;
    uint32_t expected[4];
};

// IBM SPU ISA v1.2, pages 74-75: I10 is sign extended to 16 bits.
// MPYUI then treats both 16-bit operands as unsigned; MPYI treats them signed.
// https://arcb.csc.ncsu.edu/~mueller/cluster/ps3/SDK3.0/docs/arch/SPU_ISA_v1.2_27Jan2007_pub.pdf
static const MultiplyCase cases[] = {
    {"observed", 0x750C2545u, 69, {0, 0x30, 0x180000, 0x2FFFD0}},
    {"negative", 0x75FFE545u, 69, {0, 0xFFFF, 0x7FFF8000, 0xFFFE0001}},
    {"minimum", 0x75802545u, 69, {0, 0xFE00, 0x7F000000, 0xFDFF0200}},
    {"maximum", 0x757FE545u, 69, {0, 0x1FF, 0xFF8000, 0x1FEFE01}},
    {"zero", 0x75002545u, 69, {0, 0, 0, 0}},
    {"alias", 0x750C254Au, 74, {0, 0x30, 0x180000, 0x2FFFD0}},
    {"signed", 0x74FFE545u, 69, {0, 0xFFFFFFFF, 0x8000, 1}},
};

int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "observed";
    const MultiplyCase* test = nullptr;
    for (const auto& candidate : cases)
        if (!std::strcmp(mode, candidate.name)) test = &candidate;
    if (!test) return 2;

    auto ctx = std::make_unique<spu_context>();
    spu_context_init(ctx.get(), 0);
    ctx->image_id = 62;
    const uint32_t input[4] = {0x12340000, 0x7FFF0001, 0x55AA8000, 0xAAAAFFFF};
    for (unsigned lane = 0; lane < 4; ++lane) {
        ctx->gpr[69]._u32[lane] = 0xDEADBEEF;
        ctx->gpr[74]._u32[lane] = input[lane];
        ctx->gpr[68]._u32[lane] = 0xC0FFEE;
    }
    constexpr uint32_t start = 0x15860, end = start + 4;
    for (unsigned byte = 0; byte < 4; ++byte)
        ctx->ls[start + byte] = uint8_t(test->instruction >> (24 - 8 * byte));
    const uint32_t next = spu_interp_run_until(ctx.get(), start, end);
    if (ctx->status != SPU_STATUS_RUNNING || ctx->pc != end || next != end) {
        std::fprintf(stderr, "multiply %s FAIL: instruction halted; pc=%05X status=%X next=%05X\n",
            mode, ctx->pc, ctx->status, next);
        return 1;
    }
    for (unsigned lane = 0; lane < 4; ++lane) {
        if (ctx->gpr[test->destination]._u32[lane] != test->expected[lane]) {
            std::fprintf(stderr, "multiply %s FAIL: lane %u got=%08X want=%08X\n", mode, lane,
                ctx->gpr[test->destination]._u32[lane], test->expected[lane]);
            return 1;
        }
        if (ctx->gpr[68]._u32[lane] != 0xC0FFEE ||
            (test->destination != 74 && ctx->gpr[74]._u32[lane] != input[lane])) {
            std::fprintf(stderr, "multiply %s FAIL: unrelated/source register changed\n", mode);
            return 1;
        }
    }
    std::fprintf(stderr, "multiply %s PASS: four lanes and next PC\n", mode);
    return 0;
}
