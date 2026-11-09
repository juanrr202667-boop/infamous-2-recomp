/* Actual RI10 byte XOR through the interpreter, with synthetic GPRs and LS. */
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
    unsigned source,destination;
    uint32_t expected[4];
};
// IBM SPU ISA v1.2 p109: opcode8=0x46; XOR each byte with I10 & 0xFF.
// p111: XORI instead XORs each word with the sign-extended I10.
// https://moss.csc.ncsu.edu/~mueller/cluster/ps3/SDK3.0/docs/arch/SPU_ISA_v1.2_27Jan2007_pub.pdf
static const Case cases[] = {
    {"observed",0x4604061E,12,30,{0x10013223,0x54457667,0x9889BAAB,0xDCCDFEEF}},
    {"second",0x4608061A,12,26,{0x20310213,0x64754657,0xA8B98A9B,0xECFDCEDF}},
    {"observed-alias",0x46020F1E,30,30,{0x08192A3B,0x4C5D6E7F,0x8091A2B3,0xC4D5E6F7}},
    {"negative",0x46FFC61E,12,30,{0xFFEEDDCC,0xBBAA9988,0x77665544,0x33221100}},
    {"minimum",0x4680061E,12,30,{0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF}},
    {"maximum",0x467FC61E,12,30,{0xFFEEDDCC,0xBBAA9988,0x77665544,0x33221100}},
    {"high-only",0x4640061E,12,30,{0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF}},
    {"signed-byte",0x46E0061E,12,30,{0x8091A2B3,0xC4D5E6F7,0x08192A3B,0x4C5D6E7F}},
    {"zero",0x4600061E,12,30,{0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF}},
    {"alias",0x4604060C,12,12,{0x10013223,0x54457667,0x9889BAAB,0xDCCDFEEF}},
    {"word-positive",0x4404061E,12,30,{0x00112223,0x44556667,0x8899AAAB,0xCCDDEEEF}},
    {"word-negative",0x44E0061E,12,30,{0xFFEEDDB3,0xBBAA99F7,0x7766553B,0x3322117F}},
};
int main(int argc,char** argv) {
    const char* mode=argc>1 ? argv[1] : "observed";
    const Case* test=nullptr;
    for (const auto& c:cases) if (!std::strcmp(c.name,mode)) test=&c;
    if (!test) return 2;
    auto ctx=std::make_unique<spu_context>(); spu_context_init(ctx.get(),0);
    ctx->image_id=65;
    const uint32_t input[]={0x00112233,0x44556677,0x8899AABB,0xCCDDEEFF};
    for (unsigned i=0;i<4;++i) {
        ctx->gpr[test->destination]._u32[i]=0xDEADBEEF;
        ctx->gpr[test->source]._u32[i]=input[i];
        ctx->gpr[31]._u32[i]=0xC0FFEE;
    }
    constexpr uint32_t start=0x9840,end=start+4;
    for (unsigned i=0;i<4;++i) ctx->ls[start+i]=uint8_t(test->word>>(24-8*i));
    const uint32_t next=spu_interp_run_until(ctx.get(),start,end);
    if (ctx->status!=SPU_STATUS_RUNNING || ctx->pc!=end || next!=end) {
        std::fprintf(stderr,"XORBI %s FAIL: pc=%05X status=%X next=%05X\n",mode,ctx->pc,ctx->status,next);
        return 1;
    }
    for (unsigned i=0;i<4;++i) {
        if (ctx->gpr[test->destination]._u32[i]!=test->expected[i] ||
            ctx->gpr[31]._u32[i]!=0xC0FFEE ||
            (test->destination!=test->source && ctx->gpr[test->source]._u32[i]!=input[i])) {
            std::fprintf(stderr,"XORBI %s FAIL lane=%u got=%08X want=%08X\n",mode,i,
                ctx->gpr[test->destination]._u32[i],test->expected[i]);
            return 1;
        }
    }
    std::fprintf(stderr,"XORBI %s PASS: byte/word masks, alias and next PC\n",mode);
    return 0;
}
