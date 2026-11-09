/* Actual LV2 event bindings and mailbox delivery; synthetic guest state. */
#include "ppu_recomp.h"
#define PPU_CONTEXT_H
#include "sys_event.h"
#include "spu_context.h"
#include <cstdio>
#include <cstring>
#include <memory>

extern "C" void lv2_init_syscalls(void);
extern "C" void spu_wrch(spu_context*, uint32_t, u128);
extern "C" u128 spu_rdch(spu_context*, uint32_t);
static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}
static uint32_t call(uint32_t number, uint64_t a=0, uint64_t b=0,
                     uint64_t c=0, uint64_t d=0) {
    ppu_context p{};
    p.gpr[11]=number; p.gpr[3]=a; p.gpr[4]=b; p.gpr[5]=c; p.gpr[6]=d;
    lv2_syscall_dispatch(&g_lv2_syscalls, &p);
    return uint32_t(p.gpr[3]);
}
static unsigned drain(uint32_t queue, uint32_t output) {
    ppu_context p{};
    p.gpr[3]=queue; p.gpr[4]=output; p.gpr[5]=8; p.gpr[6]=output+0x100;
    return sys_event_queue_tryreceive(&p) ? 99 : vm_read32(output+0x100);
}
int main(int argc, char** argv) {
    lv2_init_syscalls();
    if (call(SYS_SPU_THREAD_GROUP_CREATE,0x18000,1,100,0)) return 2;
    uint32_t group=vm_read32(0x18000);
    if (call(SYS_SPU_THREAD_INITIALIZE,0x18004,group,0,0)) return 2;
    uint32_t tid=vm_read32(0x18004);
    uint32_t selected=sys_event_queue_create_direct(0,8);
    uint32_t other=sys_event_queue_create_direct(0,8);
    if (!selected || !other) return 2;
    const char* mode=argc>1 ? argv[1] : "send";
    const bool throwing=std::strcmp(mode,"throw")==0;
    const bool group_only=std::strcmp(mode,"group")==0;
    const bool precedence=std::strcmp(mode,"precedence")==0;
    const bool unbound=std::strcmp(mode,"unbound")==0;
    const bool identity=std::strcmp(mode,"identity")==0;
    if (!group_only && call(SYS_SPU_THREAD_CONNECT_EVENT,tid,selected,1,53)) return 2;
    if (group_only || precedence) {
        uint32_t destination=group_only ? selected : other;
        if (call(SYS_SPU_THREAD_GROUP_CONNECT_EVENT_ALL_THREADS,
                 group,destination,1ull<<53,0x18010) || memory[0x18010]!=53) return 2;
    }
    auto ctx=std::make_unique<spu_context>(); spu_context_init(ctx.get(),tid);
    ctx->spu_id=tid; ctx->spu_group_id=group;
    if (identity) {
        if (call(SYS_SPU_THREAD_GROUP_CREATE,0x18014,1,100,0)) return 2;
        ctx->spu_group_id=vm_read32(0x18014); // This thread does not belong to that group.
    }
    u128 data{}, request{};
    data._u32[0]=0xC0CAFEED;
    request._u32[0]=throwing ? 0x75001234 : unbound ? 0x36001234 : 0x35001234;
    spu_wrch(ctx.get(),SPU_WrOutMbox,data);
    spu_wrch(ctx.get(),SPU_WrOutIntrMbox,request);
    const uint32_t expected_rc=(unbound || identity) ? CELL_ENOTCONN : CELL_OK;
    bool ack=throwing ? ctx->ch_in_mbox.count==0 : ctx->ch_in_mbox.count==1;
    if (!throwing && ack) ack=spu_rdch(ctx.get(),SPU_RdInMbox)._u32[0]==expected_rc;
    unsigned received=drain(selected,0x20000), wrong=drain(other,0x21000);
    bool payload=unbound || identity ||
        (vm_read64(0x20000)==0xFFFFFFFF53505501ull &&
         vm_read64(0x20008)==tid && vm_read64(0x20010)==0x0000003500001234ull &&
         vm_read64(0x20018)==0xC0CAFEED);
    bool ok=ack && payload && received==(unbound || identity ? 0u : 1u) &&
        wrong==0 && ctx->ch_out_mbox.count==0 && ctx->ch_out_intr_mbox.count==0;
    std::fprintf(stderr,"thread event %s: received=%u wrong=%u ack=%d payload=%d correct=%d\n",
        mode,received,wrong,ack,payload,ok);
    return ok ? 0 : 1;
}
