/* Actual LV2 binding and SPU mailbox receive, with synthetic guest state. */
#include "ppu_recomp.h"
#define PPU_CONTEXT_H
#include "sys_event.h"
#include "spu_context.h"
#include "spu_interp.h"
#include <cstdio>
#include <cstring>
#include <future>
#include <chrono>
#include <memory>

extern "C" void lv2_init_syscalls(void);
extern "C" void spu_stop(spu_context*);
extern "C" u128 spu_rdch(spu_context*, uint32_t);
#include "receive_emitted.h"
static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}
static uint32_t call(uint32_t number, uint64_t a=0, uint64_t b=0,
                     uint64_t c=0, uint64_t d=0, uint64_t e=0, uint64_t f=0) {
    ppu_context p{};
    p.gpr[11]=number; p.gpr[3]=a; p.gpr[4]=b; p.gpr[5]=c;
    p.gpr[6]=d; p.gpr[7]=e; p.gpr[8]=f;
    lv2_syscall_dispatch(&g_lv2_syscalls, &p);
    return uint32_t(p.gpr[3]);
}
static int fail(const char* message) { std::fprintf(stderr,"receive FAIL: %s\n",message); return 1; }
int main(int argc, char** argv) {
    lv2_init_syscalls();
    if (call(SYS_SPU_THREAD_GROUP_CREATE,0x1000,1,100,0)) return 2;
    uint32_t group=vm_read32(0x1000);
    if (call(SYS_SPU_THREAD_INITIALIZE,0x1004,group,0,0,0,0)) return 2;
    uint32_t tid=vm_read32(0x1004);
    uint32_t queue=sys_event_queue_create_direct(0,8);
    uint32_t other=sys_event_queue_create_direct(0,8);
    g_sys_event_queues[queue-1].type=SYS_SPU_QUEUE;
    g_sys_event_queues[other-1].type=SYS_SPU_QUEUE;
    bool invalid=argc>1 && std::strcmp(argv[1],"invalid")==0;
    if (invalid) {
        if (call(SYS_SPU_THREAD_BIND_QUEUE,tid,0,5)==0)
            return fail("binding accepted nonexistent queue");
        std::fprintf(stderr,"receive invalid PASS\n"); return 0;
    }
    uint32_t binding=argc>1 && std::strcmp(argv[1],"large-key")==0 ? 0x72647035 : 5;
    if (call(SYS_SPU_THREAD_BIND_QUEUE,tid,queue,binding)) return fail("binding failed");
    if (call(SYS_SPU_THREAD_BIND_QUEUE,tid,other,7)) return fail("second binding failed");
    auto ctx=std::make_unique<spu_context>(); spu_context_init(ctx.get(),tid);
    ctx->spu_id=tid; ctx->spu_group_id=group; ctx->image_id=61;
    ctx->pc=0x200; ctx->status=SPU_STATUS_RUNNING;
    spu_channel_write(&ctx->ch_out_mbox,binding);
    sys_event_queue_push_by_id(other,0,0xBAD,0xBAD,0xBAD);
    bool delayed=argc>1 && std::strcmp(argv[1],"delayed")==0;
    bool cancel=argc>1 && std::strcmp(argv[1],"cancel")==0;
    bool interpreted=argc>1 && std::strcmp(argv[1],"interpreted")==0;
    bool try_empty=argc>1 && std::strcmp(argv[1],"try-empty")==0;
    bool try_queued=argc>1 && std::strcmp(argv[1],"try-queued")==0;
    bool pending=false;
    if (try_empty) {
        lifted_try_receive(ctx.get());
        if (ctx->status!=SPU_STATUS_RUNNING || ctx->pc!=0x210 || !g_spu_trampoline_fn ||
            ctx->rcv_evt_n!=1 || spu_rdch(ctx.get(),SPU_RdInMbox)._u32[0]!=CELL_EBUSY ||
            g_sys_event_queues[other-1].count!=1)
            return fail("try receive did not return empty without consuming another queue");
        std::fprintf(stderr,"receive try-empty PASS\n"); return 0;
    } else if (delayed || cancel) {
        auto worker=std::async(std::launch::async,[&]{
            lifted_receive(ctx.get());
            return g_spu_trampoline_fn!=nullptr; // Transfer belongs to this host thread.
        });
        if (worker.wait_for(std::chrono::milliseconds(100))!=std::future_status::timeout)
            return fail("empty bound queue did not block");
        if (cancel) sys_event_queue_cancel_by_id(queue);
        else sys_event_queue_push_by_id(queue,0,0x123456789ULL,0xABCDEF012ULL,0xC0FFEE);
        if (worker.wait_for(std::chrono::seconds(1))!=std::future_status::ready)
            return fail("receiver did not wake");
        pending=worker.get();
    } else {
        sys_event_queue_push_by_id(queue,0,0x123456789ULL,0xABCDEF012ULL,0xC0FFEE);
        if (interpreted) {
            ctx->ls[0x20C]=0; ctx->ls[0x20D]=0; ctx->ls[0x20E]=1; ctx->ls[0x20F]=0x10;
            spu_interp_run_until(ctx.get(),0x20C,0x210);
            pending=g_spu_trampoline_fn==nullptr; // Interpreter continues in its own loop.
        } else {
            if (try_queued) lifted_try_receive(ctx.get());
            else lifted_receive(ctx.get());
            pending=g_spu_trampoline_fn!=nullptr;
        }
    }
    if (ctx->status!=SPU_STATUS_RUNNING || ctx->pc!=0x210 || !pending)
        return fail("receive lost next instruction continuation");
    if (ctx->ch_out_mbox.count || g_sys_event_queues[other-1].count!=1)
        return fail("wrong mailbox/queue consumed");
    if (cancel) {
        if (ctx->rcv_evt_n!=1 || spu_rdch(ctx.get(),SPU_RdInMbox)._u32[0]!=CELL_ECANCELED)
            return fail("cancellation did not return its error");
    } else {
        const uint32_t expected[]={0,0x23456789,0xBCDEF012,0xC0FFEE};
        if (ctx->rcv_evt_n!=4 || g_sys_event_queues[queue-1].count)
            return fail("event not delivered exactly once");
        for (uint32_t word:expected)
            if (spu_rdch(ctx.get(),SPU_RdInMbox)._u32[0]!=word)
                return fail("mailbox payload differs from event");
    }
    g_spu_trampoline_fn=nullptr;
    if (call(SYS_SPU_THREAD_UNBIND_QUEUE,tid,binding)) return fail("unbind failed");
    spu_channel_write(&ctx->ch_out_mbox,binding); lifted_receive(ctx.get());
    if (ctx->rcv_evt_n!=1 || spu_rdch(ctx.get(),SPU_RdInMbox)._u32[0]!=CELL_EINVAL)
        return fail("unbound receive did not return error");
    std::fprintf(stderr,"receive %s PASS: exact queue, payload and continuation\n",argc>1?argv[1]:"queued");
    return 0;
}
