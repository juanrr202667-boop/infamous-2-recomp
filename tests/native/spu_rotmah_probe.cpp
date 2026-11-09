/* Actual RR arithmetic halfword shifts through the real interpreter. */
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
    uint16_t counts[8], expected[8];
};
// IBM SPU ISA v1.2 p145: eight independent count=(0-RBhalf)&31.
// Signed shift if count<16; otherwise replicate the input halfword's sign.
// https://moss.csc.ncsu.edu/~mueller/cluster/ps3/SDK3.0/docs/arch/SPU_ISA_v1.2_27Jan2007_pub.pdf
static const Case cases[] = {
    {"observed",0x0BC8501A,26,{0,0xFFFF,0xFFF9,0xFFF9,0xFFFF,0xFFF8,0xFFFC,0xFFF1},
        {0,0,0x00FF,0xFF00,0xFFFF,0xFFFF,0x0123,0xFFFF}},
    {"negative",0x0BC8501A,26,{0xFFFF,0xFFFF,0xFFFF,0xFFFF,0xFFFF,0xFFFF,0xFFFF,0xFFFF},
        {0,0,0x3FFF,0xC000,0xFFFF,0xFF80,0x091A,0xE000}},
    {"wide",0x0BC8501A,26,{0xFFF0,0xFFF0,0xFFF0,0xFFF0,0xFFF0,0xFFF0,0xFFF0,0xFFF0},
        {0,0,0,0xFFFF,0xFFFF,0xFFFF,0,0xFFFF}},
    {"modular",0x0BC8501A,26,{0x20,0xFFE0,0x40,0xFFC0,0x100,0xFF00,0x20,0x7FE0},
        {0,1,0x7FFF,0x8000,0xFFFF,0xFF00,0x1234,0xC000}},
    {"positive",0x0BC8501A,26,{1,1,1,1,1,1,1,1},
        {0,0,0,0xFFFF,0xFFFF,0xFFFF,0,0xFFFF}},
    {"zero",0x0BC8501A,26,{0,0,0,0,0,0,0,0},
        {0,1,0x7FFF,0x8000,0xFFFF,0xFF00,0x1234,0xC000}},
    {"alias-source",0x0BC85020,32,{0,0xFFFF,0xFFF9,0xFFF9,0xFFFF,0xFFF8,0xFFFC,0xFFF1},
        {0,0,0x00FF,0xFF00,0xFFFF,0xFFFF,0x0123,0xFFFF}},
    {"alias-count",0x0BC85021,33,{0,0xFFFF,0xFFF9,0xFFF9,0xFFFF,0xFFF8,0xFFFC,0xFFF1},
        {0,0,0x00FF,0xFF00,0xFFFF,0xFFFF,0x0123,0xFFFF}},
};
int main(int argc,char** argv) {
    const char* mode=argc>1 ? argv[1] : "observed";
    const Case* test=nullptr;
    for (const auto& c:cases) if (!std::strcmp(c.name,mode)) test=&c;
    if (!test) return 2;
    auto ctx=std::make_unique<spu_context>(); spu_context_init(ctx.get(),0);
    ctx->image_id=64;
    const uint16_t input[]={0,1,0x7FFF,0x8000,0xFFFF,0xFF00,0x1234,0xC000};
    for (unsigned i=0;i<8;++i) {
        ctx->gpr[26]._u16[i]=0xDEAD;
        ctx->gpr[32]._u16[i]=input[i];
        ctx->gpr[33]._u16[i]=test->counts[i];
        ctx->gpr[31]._u16[i]=0xBEEF;
    }
    constexpr uint32_t start=0xC86C,end=start+4;
    for (unsigned i=0;i<4;++i) ctx->ls[start+i]=uint8_t(test->word>>(24-8*i));
    uint32_t next=spu_interp_run_until(ctx.get(),start,end);
    if (ctx->status!=SPU_STATUS_RUNNING || ctx->pc!=end || next!=end) {
        std::fprintf(stderr,"ROTMAH %s FAIL: pc=%05X status=%X next=%05X\n",mode,ctx->pc,ctx->status,next);
        return 1;
    }
    for (unsigned i=0;i<8;++i) {
        if (ctx->gpr[test->destination]._u16[i]!=test->expected[i] ||
            ctx->gpr[31]._u16[i]!=0xBEEF ||
            (test->destination!=32 && ctx->gpr[32]._u16[i]!=input[i]) ||
            (test->destination!=33 && ctx->gpr[33]._u16[i]!=test->counts[i])) {
            std::fprintf(stderr,"ROTMAH %s FAIL half=%u got=%04X want=%04X\n",mode,i,
                ctx->gpr[test->destination]._u16[i],test->expected[i]);
            return 1;
        }
    }
    std::fprintf(stderr,"ROTMAH %s PASS: eight signed halfwords, independent counts and PC\n",mode);
    return 0;
}
