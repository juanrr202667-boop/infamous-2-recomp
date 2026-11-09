/* Real indirect-branch/microstep/interpreter boundary, using synthetic LS code. */
#include "ppu_recomp.h"
#include "spu_context.h"
#include <cstdio>
#include <cstring>
#include <memory>

extern "C" void spu_begin_image(int);
extern "C" void spu_register_function(uint32_t, void (*)(spu_context*));
static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}
namespace {
void word(spu_context* ctx, uint32_t addr, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) ctx->ls[addr + i] = uint8_t(value >> (24 - 8 * i));
}
void lifted_continuation(spu_context* ctx) { ctx->gpr[10]._u32[0] = 0xC0FFEE; }
int fail(const char* why, spu_context* ctx) {
    std::fprintf(stderr, "handoff FAIL: %s pc=%05X status=%X pending=%d depth=%u ret=%05X\n",
        why, ctx->pc, ctx->status, g_spu_trampoline_fn != nullptr, ctx->host_depth, ctx->drain_ret_pc);
    return 1;
}
}
int main(int argc, char** argv) {
    auto ctx = std::make_unique<spu_context>();
    spu_context_init(ctx.get(), 0);
    ctx->image_id = 61;
    ctx->policy_mode = 1;
    ctx->status = SPU_STATUS_RUNNING;
    ctx->host_depth = 7;
    ctx->pc = 0xA68;
    ctx->gpr[76]._u32[0] = 0x1000;
    ctx->gpr[77]._u32[0] = 0x12345678;
    ctx->gpr[78]._u32[0] = 0x10;
    // stqx r77,r76,r78 is the authentic instruction at PM A68, missing in
    // microstep's small opcode set. The real interpreter performs its store.
    word(ctx.get(), 0xA68, 0x2893A64D);
    word(ctx.get(), 0xA6C, 0x217FFECE); // brnz r78,A60
    spu_begin_image(61);
    spu_register_function(0xA60, lifted_continuation);
    bool absolute_load = argc > 1 && std::strcmp(argv[1], "absolute-load") == 0;
    bool absolute_store = argc > 1 && std::strcmp(argv[1], "absolute-store") == 0;
    if (absolute_load || absolute_store) {
        // RI16 absolute addressing uses words (4 bytes), not quadwords (16).
        // Each wrong address contains a different sentinel to catch the old
        // scaling while the real indirect branch still reaches lifted A60.
        if (absolute_load) {
            word(ctx.get(), 0xA40, 0x11223344);
            word(ctx.get(), 0x2900, 0x55667788);
            ctx->pc = 0xA54;
            word(ctx.get(), 0xA54, 0x3081484D); // lqa r77,A40
            word(ctx.get(), 0xA58, 0x32000100); // br A60
        } else {
            std::memset(ctx->ls + 0x1520, 0xCC, 16);
            std::memset(ctx->ls + 0x5480, 0xDD, 16);
            ctx->gpr[78]._u32[0] = 0xDEADBEEF;
            ctx->pc = 0xA34;
            word(ctx.get(), 0xA34, 0x2082A44E); // stqa r78,1520
            word(ctx.get(), 0xA38, 0x32000500); // br A60
        }
        g_spu_trampoline_fn = nullptr;
        spu_indirect_branch(ctx.get());
        if (absolute_load && (ctx->gpr[77]._u32[0] != 0x11223344 ||
                              std::memcmp(ctx->ls + 0x2900, "\x55\x66\x77\x88", 4)))
            return fail("LQA did not read word-scaled address A40", ctx.get());
        if (absolute_store) {
            const uint8_t want[16] = {0xDE, 0xAD, 0xBE, 0xEF};
            uint8_t untouched[16]; std::memset(untouched, 0xDD, 16);
            if (std::memcmp(ctx->ls + 0x1520, want, 16) || std::memcmp(ctx->ls + 0x5480, untouched, 16))
                return fail("STQA did not write 1520 exclusively", ctx.get());
        }
        if (ctx->pc != 0xA60 || !g_spu_trampoline_fn || ctx->host_depth != 7)
            return fail("absolute instruction lost lifted continuation", ctx.get());
        SPU_DRAIN(ctx.get());
        if (ctx->gpr[10]._u32[0] != 0xC0FFEE || ctx->host_depth != 7 || g_spu_trampoline_fn)
            return fail("absolute instruction continuation did not run", ctx.get());
        std::fprintf(stderr, "handoff absolute-%s PASS: expected address, wrong-address sentinel intact\n",
            absolute_load ? "load" : "store");
        return 0;
    }
    bool caller_return = argc > 1 && std::strcmp(argv[1], "return") == 0;
    bool stop = argc > 1 && std::strcmp(argv[1], "stop") == 0;
    if (caller_return) ctx->drain_ret_pc = 0xA60;
    if (stop) word(ctx.get(), 0xA6C, 0x1234); // STOP 0x1234, not a continuation
    g_spu_trampoline_fn = nullptr;
    spu_indirect_branch(ctx.get());
    if (std::memcmp(ctx->ls + 0x1010, "\x12\x34\x56\x78", 4))
        return fail("interpreter did not execute stqx", ctx.get());
    if (ctx->host_depth != 7 || ctx->drain_ret_pc != (caller_return ? 0xA60u : 0u))
        return fail("caller depth/return point changed", ctx.get());
    if (stop) {
        if (ctx->status != SPU_STATUS_STOPPED_BY_STOP || ctx->stop_code != 0x1234 || g_spu_trampoline_fn)
            return fail("STOP acquired a continuation", ctx.get());
    } else if (caller_return) {
        if (ctx->pc != 0xA60 || ctx->status != SPU_STATUS_RUNNING || g_spu_trampoline_fn || ctx->gpr[10]._u32[0])
            return fail("caller return redispatched", ctx.get());
    } else {
        if (ctx->pc != 0xA60 || ctx->status != SPU_STATUS_RUNNING || !g_spu_trampoline_fn)
            return fail("running lifted rejoin lost its pending transfer", ctx.get());
        SPU_DRAIN(ctx.get());
        if (ctx->gpr[10]._u32[0] != 0xC0FFEE || g_spu_trampoline_fn || ctx->host_depth != 7)
            return fail("lifted continuation did not run at caller depth", ctx.get());
    }
    std::fprintf(stderr, "handoff %s PASS: stqx executed, caller depth preserved\n",
        stop ? "stop" : caller_return ? "return" : "rejoin");
    return 0;
}
