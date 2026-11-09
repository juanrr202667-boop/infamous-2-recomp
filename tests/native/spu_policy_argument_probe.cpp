/* Real policy-module driver argument ABI, without proprietary SPU code. */
#include "ppu_recomp.h"
#include "spu_context.h"
#include "spu_workload.h"
#include <cstdio>
#include <cstring>

static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}
static uint32_t captured[2];
static uint8_t stored[8];
static unsigned calls;
static bool environment_ok;

static void policy_entry(spu_context* ctx) {
    ++calls;
    captured[0] = ctx->gpr[4]._u32[0];
    captured[1] = ctx->gpr[4]._u32[1];
    // Observe the actual register as big-endian guest bytes, like PM STQA r4.
    spu_ls_write128(ctx, 0x800, ctx->gpr[4]);
    std::memcpy(stored, ctx->ls + 0x800, sizeof stored);
    environment_ok = ctx->policy_mode && ctx->image_id == 62 &&
        ctx->gpr[3]._u32[0] == 0x100 && ctx->gpr[5]._u32[0] == 0;
    ctx->pc = SPURS_PM_EXIT_TO_KERNEL_LS;
    spu_indirect_branch(ctx);
}

int main(int argc, char** argv) {
    const bool high = argc > 1 && !std::strcmp(argv[1], "distinct-high");
    const uint64_t argument = high ? 0x1234567800A93900ULL : 0x0000000000A93900ULL;
    const uint8_t expected_high[8] = {0x12, 0x34, 0x56, 0x78, 0x00, 0xA9, 0x39, 0x00};
    const uint8_t expected_zero[8] = {0x00, 0x00, 0x00, 0x00, 0x00, 0xA9, 0x39, 0x00};
    // IBM SPU ABI v1.7 sections 2.1.1-2.1.3 and 2.2.3: a scalar u64
    // occupies the first doubleword in big-endian order; unused bytes undefined.
    // https://ilab.usc.edu/packages/cell-processor/docs/SPU_ABI-Specification_1.7.pdf
    const uint8_t synthetic_pm[16] = {};
    const int rc = spu_run_policy_module(policy_entry, 62, synthetic_pm, sizeof synthetic_pm,
        argument, 0, 0x1000, 0);
    if (rc != 0 || calls != 1 || !environment_ok) {
        std::fprintf(stderr, "policy argument FAIL: driver rc=%d calls=%u environment=%d\n",
            rc, calls, environment_ok);
        return 1;
    }
    if (captured[0] != (high ? 0x12345678u : 0u) || captured[1] != 0x00A93900u ||
        std::memcmp(stored, high ? expected_high : expected_zero, sizeof stored)) {
        std::fprintf(stderr, "policy argument %s FAIL: r4 preferred doubleword=%08X_%08X\n",
            high ? "distinct-high" : "zero-high", captured[0], captured[1]);
        return 1;
    }
    std::fprintf(stderr, "policy argument %s PASS: high/low preserved and kernel exit\n",
        high ? "distinct-high" : "zero-high");
    return 0;
}
