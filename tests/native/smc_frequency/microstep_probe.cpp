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
static void word(spu_context* c, uint32_t a, uint32_t v) {
    for (unsigned i = 0; i < 4; ++i) c->ls[a+i] = uint8_t(v >> (24-8*i));
}
static void target(spu_context* c) { c->gpr[10]._u32[0] = 0xC0FFEE; }
int main(int argc, char** argv) {
    const bool load = argc > 1 && !std::strcmp(argv[1], "lqx");
    auto c = std::make_unique<spu_context>();
    spu_context_init(c.get(), 0);
    c->image_id = 61; c->policy_mode = 1; c->status = SPU_STATUS_RUNNING;
    c->host_depth = 7; c->pc = 0xA68;
    c->gpr[76]._u32[0] = 0x1000; c->gpr[78]._u32[0] = 0x10;
    c->gpr[77]._u32[0] = load ? 0 : 0x12345678;
    word(c.get(), 0x1010, load ? 0x12345678 : 0);
    word(c.get(), 0xA68, load ? 0x3893A64D : 0x2893A64D);
    word(c.get(), 0xA6C, 0x217FFECE);
    spu_begin_image(61); spu_register_function(0xA60, target);
    g_spu_trampoline_fn = nullptr; spu_indirect_branch(c.get());
    bool correct = load ? c->gpr[77]._u32[0] == 0x12345678 :
                           std::memcmp(c->ls + 0x1010, "\x12\x34\x56\x78", 4) == 0;
    if (!correct || c->pc != 0xA60 || !g_spu_trampoline_fn) return 1;
    SPU_DRAIN(c.get());
    if (c->gpr[10]._u32[0] != 0xC0FFEE || c->host_depth != 7 || g_spu_trampoline_fn) return 2;
    std::fprintf(stderr, "%s semantics and continuation PASS\n", load ? "lqx" : "stqx");
    return 0;
}
