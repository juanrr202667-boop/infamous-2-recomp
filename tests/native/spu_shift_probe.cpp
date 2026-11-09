/* Synthetic RI7 halfword shifts through the real interpreter. */
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
struct ShiftCase {
    const char* name;
    int immediate;
    unsigned destination;
    uint16_t expected[8];
};
// IBM SPU ISA v1.2 p137: count=(0-sign_extend(I7))&31.
// Shift each unsigned halfword logically right; counts >=16 produce zero.
// https://arcb.csc.ncsu.edu/~mueller/cluster/ps3/SDK3.0/docs/arch/SPU_ISA_v1.2_27Jan2007_pub.pdf
static const ShiftCase cases[] = {
    {"observed", -7, 21, {0, 0, 0, 1, 0xFF, 0x100, 0x1FE, 0x1FF}},
    {"zero", 0, 21, {0, 1, 0x7F, 0x80, 0x7FFF, 0x8000, 0xFF00, 0xFFFF}},
    {"last-bit", -15, 21, {0, 0, 0, 0, 0, 1, 1, 1}},
    {"wide", -16, 21, {0, 0, 0, 0, 0, 0, 0, 0}},
    {"modular", -32, 21, {0, 1, 0x7F, 0x80, 0x7FFF, 0x8000, 0xFF00, 0xFFFF}},
    {"positive", 1, 21, {0, 0, 0, 0, 0, 0, 0, 0}},
    {"alias", -7, 51, {0, 0, 0, 1, 0xFF, 0x100, 0x1FE, 0x1FF}},
};
int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "observed";
    const ShiftCase* test = nullptr;
    for (const auto& candidate : cases)
        if (!std::strcmp(mode, candidate.name)) test = &candidate;
    if (!test) return 2;
    auto ctx = std::make_unique<spu_context>();
    spu_context_init(ctx.get(), 0);
    ctx->image_id = 62;
    const uint16_t input[8] = {0, 1, 0x7F, 0x80, 0x7FFF, 0x8000, 0xFF00, 0xFFFF};
    for (unsigned lane = 0; lane < 8; ++lane) {
        ctx->gpr[51]._u16[lane] = input[lane];
        ctx->gpr[21]._u16[lane] = 0xDEAD;
        ctx->gpr[20]._u16[lane] = 0xBEEF;
    }
    // RI7 opcode 0x7D, ra51, rt21; observed word is exactly 0x0FBE5995.
    const uint32_t instruction = 0x0FA00000u | ((uint32_t(test->immediate) & 127u) << 14)
        | (51u << 7) | test->destination;
    constexpr uint32_t start = 0x49AC, end = start + 4;
    for (unsigned byte = 0; byte < 4; ++byte)
        ctx->ls[start + byte] = uint8_t(instruction >> (24 - 8 * byte));
    const uint32_t next = spu_interp_run_until(ctx.get(), start, end);
    if (ctx->status != SPU_STATUS_RUNNING || ctx->pc != end || next != end) {
        std::fprintf(stderr, "shift %s FAIL: word=%08X pc=%05X status=%X next=%05X\n",
            mode, instruction, ctx->pc, ctx->status, next);
        return 1;
    }
    for (unsigned lane = 0; lane < 8; ++lane) {
        if (ctx->gpr[test->destination]._u16[lane] != test->expected[lane]) {
            std::fprintf(stderr, "shift %s FAIL: halfword %u got=%04X want=%04X\n", mode, lane,
                ctx->gpr[test->destination]._u16[lane], test->expected[lane]);
            return 1;
        }
        if (ctx->gpr[20]._u16[lane] != 0xBEEF ||
            (test->destination != 51 && ctx->gpr[51]._u16[lane] != input[lane])) return 1;
    }
    std::fprintf(stderr, "shift %s PASS: eight halfwords and next PC\n", mode);
    return 0;
}
