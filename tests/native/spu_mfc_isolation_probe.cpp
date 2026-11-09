// Synthetic bytes only. Calls the production SPU channel/DMA implementation.
// No scheduling, lifted title code, debugger, or proprietary input is used.
#include "ppu_recomp.h"
#include "spu_context.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>

extern "C" {
void spu_wrch(spu_context*, uint32_t, u128);
u128 spu_rdch(spu_context*, uint32_t);
void spu_mfc_release(spu_context*);
static uint8_t synthetic_memory[0x4000];
uint8_t* vm_base = synthetic_memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

namespace {
constexpr uint32_t tag = 3;
constexpr uint32_t tag_bit = 1u << tag;
constexpr uint32_t list_lsa = 0x100;
constexpr uint32_t a_dest = 0x200;
constexpr uint32_t b_dest = 0x400;

void write_channel(spu_context* ctx, uint32_t channel, uint32_t value) {
    u128 argument{};
    argument._u32[0] = value;
    spu_wrch(ctx, channel, argument);
}

uint32_t tag_status(spu_context* ctx) {
    write_channel(ctx, MFC_WrTagMask, tag_bit);
    write_channel(ctx, MFC_WrTagUpdate, 0); // Nonblocking immediate query.
    return spu_rdch(ctx, MFC_RdTagStat)._u32[0];
}

void transfer(spu_context* ctx, uint32_t lsa, uint32_t ea, uint32_t bytes,
              uint32_t transfer_tag, uint32_t command) {
    write_channel(ctx, MFC_LSA, lsa);
    write_channel(ctx, MFC_EAH, 0);
    write_channel(ctx, MFC_EAL, ea);
    write_channel(ctx, MFC_Size, bytes);
    write_channel(ctx, MFC_TagID, transfer_tag);
    write_channel(ctx, MFC_Cmd, command);
}

bool bytes_are(const uint8_t* bytes, uint8_t value) {
    for (unsigned i = 0; i < 16; ++i)
        if (bytes[i] != value) return false;
    return true;
}
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const bool private_control = std::strcmp(argv[1], "private") == 0;
    const bool same_peer = std::strcmp(argv[1], "overflow-same") == 0;
    const bool other_peer = std::strcmp(argv[1], "overflow-other") == 0;
    const bool reclaim = std::strcmp(argv[1], "overflow-reclaim") == 0;
    if (!private_control && !same_peer && !other_peer && !reclaim) return 2;

    std::unique_ptr<spu_context> contexts[10]; // Never more than ten contexts.
    for (unsigned i = 0; i < 10; ++i) {
        contexts[i] = std::make_unique<spu_context>();
        spu_context_init(contexts[i].get(), 0x100u + i);
        contexts[i]->image_id = 90 + static_cast<int>(i);
    }
    const unsigned held = private_control ? 6u : 8u;
    for (unsigned i = 0; i < held; ++i)
        write_channel(contexts[i].get(), MFC_WrTagMask, 1);
    spu_context* a = contexts[8].get();
    spu_context* b = contexts[9].get();
    // Claim both extras before the experiment through a real MFC write.
    write_channel(a, MFC_WrTagMask, tag_bit);
    write_channel(b, MFC_WrTagMask, tag_bit);

    std::memset(synthetic_memory + 0x1000, 0x11, 16);
    std::memset(synthetic_memory + 0x1010, 0x22, 16);
    std::memset(synthetic_memory + 0x1100, 0x33, 16);
    std::memset(a->ls + a_dest, 0xA5, 32);
    // First GETL element transfers, then stalls; second must await A's ACK.
    spu_ls_write32(a, list_lsa, 0x80000010u);
    spu_ls_write32(a, list_lsa + 4, 0x1000u);
    spu_ls_write32(a, list_lsa + 8, 0x00000010u);
    spu_ls_write32(a, list_lsa + 12, 0x1010u);
    transfer(a, a_dest, list_lsa, 16, tag, MFC_GETL_CMD);

    const uint32_t before = tag_status(a);
    const bool parked_before = (a->list_stall_mask & tag_bit) != 0;
    const bool first_arrived = bytes_are(a->ls + a_dest, 0x11);
    const bool second_pending_before = bytes_are(a->ls + a_dest + 16, 0xA5);
    const uint32_t remaining_before = a->list_stall_remaining[tag];
    std::printf("mode=%s held=%u contexts=10 before_A_status=%08X "
                "parked=%d remaining=%u first_arrived=%d second_pending=%d\n",
                argv[1], held, before, parked_before, remaining_before,
                first_arrived, second_pending_before);

    bool peer_arrived = true;
    if (reclaim) {
        // Still ten objects, but one held MFC slot becomes free. A has no ACK.
        spu_mfc_release(contexts[0].get());
    } else {
        transfer(b, b_dest, 0x1100, 16, other_peer ? tag + 1 : tag, MFC_GET_CMD);
        peer_arrived = bytes_are(b->ls + b_dest, 0x33);
    }
    const uint32_t after = tag_status(a);
    const bool parked_after = (a->list_stall_mask & tag_bit) != 0;
    const bool second_pending_after = bytes_are(a->ls + a_dest + 16, 0xA5);
    std::printf("after_%s A_status=%08X parked=%d remaining=%u "
                "second_pending=%d peer_arrived=%d\n", reclaim ? "slot_release" : "B",
                after, parked_after, a->list_stall_remaining[tag],
                second_pending_after, peer_arrived);

    // Positive control: only A's ACK may finish its second real transfer.
    write_channel(a, MFC_WrListStallAck, tag);
    const uint32_t ack_status = tag_status(a);
    const bool second_arrived = bytes_are(a->ls + a_dest + 16, 0x22);
    const bool unparked = !(a->list_stall_mask & tag_bit);
    std::printf("after_A_ACK A_status=%08X parked=%d second_arrived=%d\n",
                ack_status, !unparked, second_arrived);

    const bool controls_ok = before == 0 && parked_before && first_arrived &&
        second_pending_before && remaining_before == 1 && parked_after &&
        second_pending_after && peer_arrived && ack_status == tag_bit &&
        second_arrived && unparked;
    const bool isolation_ok = after == 0;
    for (auto& ctx : contexts) spu_mfc_release(ctx.get());
    std::printf("result=%s controls=%s isolation=%s\n",
                controls_ok && isolation_ok ? "PASS" : "FAIL",
                controls_ok ? "PASS" : "FAIL", isolation_ok ? "PASS" : "FAIL");
    return controls_ok && isolation_ok ? 0 : 1;
}
