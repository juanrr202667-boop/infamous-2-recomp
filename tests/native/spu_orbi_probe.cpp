/* Decode and execute real RI10 words in synthetic LS; no proprietary code. */
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
struct Case {
    const char* name;
    uint32_t word;
    unsigned destination;
    uint32_t expected[4];
};
// IBM SPU ISA v1.2 p104: opcode8=0x06, repeat the low 8 I10 bits in each byte.
// https://moss.csc.ncsu.edu/~mueller/cluster/ps3/SDK3.0/docs/arch/SPU_ISA_v1.2_27Jan2007_pub.pdf
static const Case cases[] = {
    {"observed",0x06011D38,56,{0x04152637,0x44556677,0x8C9DAEBF,0xCCDDEEFF}},
    {"negative",0x06FFDD38,56,{0xFFFFFFFF,0xFFFFFFFF,0xFFFFFFFF,0xFFFFFFFF}},
    {"minimum",0x06801D38,56,{0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF}},
    {"maximum",0x067FDD38,56,{0xFFFFFFFF,0xFFFFFFFF,0xFFFFFFFF,0xFFFFFFFF}},
    {"high-only",0x06401D38,56,{0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF}},
    {"zero",0x06001D38,56,{0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF}},
    {"signed-byte",0x06E01D38,56,{0x8091A2B3,0xC4D5E6F7,0x8899AABB,0xCCDDEEFF}},
    {"alias",0x06011D3A,58,{0x04152637,0x44556677,0x8C9DAEBF,0xCCDDEEFF}},
    {"word-positive",0x04011D38,56,{0x00112237,0x44556677,0x8899AABF,0xCCDDEEFF}},
    {"word-negative",0x04E01D38,56,{0xFFFFFFB3,0xFFFFFFF7,0xFFFFFFBB,0xFFFFFFFF}},
};
int main(int argc,char** argv) {
    const char* mode=argc>1 ? argv[1] : "observed";
    const Case* test=nullptr;
    for (const auto& c:cases) if (!std::strcmp(c.name,mode)) test=&c;
    if (!test) return 2;
    auto ctx=std::make_unique<spu_context>(); spu_context_init(ctx.get(),0);
    ctx->image_id=63;
    const uint32_t input[]={0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF};
    for (unsigned i=0;i<4;++i) {
        ctx->gpr[56]._u32[i]=0xDEADBEEF;
        ctx->gpr[58]._u32[i]=input[i];
        ctx->gpr[57]._u32[i]=0xC0FFEE;
    }
    constexpr uint32_t start=0x7014,end=start+4;
    for (unsigned i=0;i<4;++i) ctx->ls[start+i]=uint8_t(test->word>>(24-8*i));
    const uint32_t next=spu_interp_run_until(ctx.get(),start,end);
    if (ctx->status!=SPU_STATUS_RUNNING || ctx->pc!=end || next!=end) {
        std::fprintf(stderr,"ORBI %s FAIL: pc=%05X status=%X next=%05X\n",mode,ctx->pc,ctx->status,next);
        return 1;
    }
    for (unsigned i=0;i<4;++i) {
        if (ctx->gpr[test->destination]._u32[i]!=test->expected[i] ||
            ctx->gpr[57]._u32[i]!=0xC0FFEE ||
            (test->destination!=58 && ctx->gpr[58]._u32[i]!=input[i])) {
            std::fprintf(stderr,"ORBI %s FAIL lane=%u got=%08X want=%08X\n",mode,i,
                ctx->gpr[test->destination]._u32[i],test->expected[i]);
            return 1;
        }
    }
    std::fprintf(stderr,"ORBI %s PASS: byte/word semantics, alias and next PC\n",mode);
    return 0;
}
