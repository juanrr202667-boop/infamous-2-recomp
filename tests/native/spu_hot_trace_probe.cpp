/* Real GETL stall/retarget/resume and SPURS policy intercepts, synthetic bytes.
 * Catches a diagnostic gate accidentally swallowing DMA or kernel handoff. */
#include "ppu_recomp.h"
#include "spu_context.h"
#include "spu_workload.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <io.h>
#include <memory>
#include <string>

static uint8_t memory[SPU_LS_SIZE];
extern "C" {
void spu_wrch(spu_context*, uint32_t, u128);
u128 spu_rdch(spu_context*, uint32_t);
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
extern volatile unsigned g_spurs_pm_polls;
}
namespace {
void channel(spu_context* ctx, uint32_t ch, uint32_t value) {
    u128 arg{}; arg._u32[0] = value; spu_wrch(ctx, ch, arg);
}
void be32(uint8_t* out, uint32_t value) {
    out[0] = uint8_t(value >> 24); out[1] = uint8_t(value >> 16);
    out[2] = uint8_t(value >> 8); out[3] = uint8_t(value);
}
class Capture {
    FILE* file = std::tmpfile();
    int saved = -1;
public:
    Capture() {
        if (file) { std::fflush(stderr); saved = _dup(_fileno(stderr));
            if (saved >= 0 && _dup2(_fileno(file), _fileno(stderr))) { _close(saved); saved = -1; } }
    }
    ~Capture() { restore(); if (file) std::fclose(file); }
    bool ready() const { return saved >= 0; }
    void restore() { if (saved >= 0) { std::fflush(stderr); _dup2(saved, _fileno(stderr)); _close(saved); saved = -1; } }
    std::string finish() {
        restore(); std::rewind(file); std::string text; char bytes[512]; size_t n;
        while ((n = std::fread(bytes, 1, sizeof bytes, file))) text.append(bytes, n);
        return text;
    }
};
unsigned occurrences(const std::string& text, const char* needle) {
    unsigned n = 0; size_t pos = 0;
    while ((pos = text.find(needle, pos)) != std::string::npos) { ++n; pos += std::strlen(needle); }
    return n;
}
bool policy_ok = true;
void policy_entry(spu_context* ctx) {
    for (unsigned i = 1; i <= 4097; ++i) {
        ctx->gpr[0]._u32[0] = 0x200;
        ctx->gpr[3]._u32[0] = 0xDEADBEEF;
        ctx->pc = SPURS_PM_SELECT_WORKLOAD_LS;
        spu_indirect_branch(ctx);
        policy_ok = policy_ok && ctx->pc == 0x200 && ctx->gpr[3]._u32[0] == 0 && g_spurs_pm_polls == i;
    }
    ctx->pc = SPURS_PM_EXIT_TO_KERNEL_LS;
    spu_indirect_branch(ctx);
    policy_ok = policy_ok && ctx->status == SPU_STATUS_STOPPED_BY_STOP;
}
}
int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "dma-off";
    const bool dma = std::strncmp(mode, "dma-", 4) == 0;
    const bool on = std::strstr(mode, "-on") != nullptr;
    const bool zero = std::strstr(mode, "-zero") != nullptr;
    const char* flag = on ? "1" : zero ? "0" : "";
    _putenv_s("SPU_DMA_LIST_TRACE", flag); _putenv_s("SPURS_PM_TRACE", flag);
    _putenv_s("SPURS_PM_FLOW", ""); _putenv_s("SPU_DMA_LAX", "");
    bool semantics = true;
    Capture capture; if (!capture.ready()) return 2;
    if (dma) {
        auto ctx = std::make_unique<spu_context>(); spu_context_init(ctx.get(), 63);
        ctx->image_id = 63; std::memset(ctx->ls, 0x5A, SPU_LS_SIZE);
        for (unsigned i = 0; i < 16; ++i) { memory[0x10000 + i] = uint8_t(0x10 + i); memory[0x10040 + i] = uint8_t(0x80 + i); }
        be32(ctx->ls + 0x2000, 0x80000010); be32(ctx->ls + 0x2004, 0x10000);
        be32(ctx->ls + 0x2008, 16); be32(ctx->ls + 0x200C, 0x10020);
        channel(ctx.get(), MFC_LSA, 0x1000); channel(ctx.get(), MFC_EAH, 0);
        channel(ctx.get(), MFC_EAL, 0x2000); channel(ctx.get(), MFC_Size, 16);
        channel(ctx.get(), MFC_TagID, 7); channel(ctx.get(), MFC_Cmd, MFC_GETL_CMD);
        semantics = ctx->list_stall_mask == 0x80 && ctx->list_stall_stat == 0x80 &&
            (ctx->event_status & 2) && ctx->list_stall_remaining[7] == 1 && ctx->list_stall_dest_lsa[7] == 0x1010;
        std::fprintf(stderr, "fixture parked mask=%X stat=%X event=%X left=%u dest=%X first=%X tail=%X\n",
            ctx->list_stall_mask, ctx->list_stall_stat, ctx->event_status,
            ctx->list_stall_remaining[7], ctx->list_stall_dest_lsa[7], ctx->ls[0x1000], ctx->ls[0x1010]);
        for (unsigned i = 0; i < 16; ++i) semantics = semantics && ctx->ls[0x1000 + i] == uint8_t(0x10 + i) && ctx->ls[0x1010 + i] == 0x5A;
        // An interrupt can patch the parked list. Resume must read that updated EA.
        be32(ctx->ls + 0x200C, 0x10040);
        channel(ctx.get(), MFC_WrListStallAck, 7);
        channel(ctx.get(), MFC_WrTagMask, 0x80); channel(ctx.get(), MFC_WrTagUpdate, 2);
        semantics = semantics && ctx->list_stall_mask == 0 && spu_rdch(ctx.get(), MFC_RdTagStat)._u32[0] == 0x80;
        for (unsigned i = 0; i < 16; ++i) semantics = semantics && ctx->ls[0x1010 + i] == uint8_t(0x80 + i);
        channel(ctx.get(), MFC_WrListStallAck, 7); // limited invalid-ack warning survives either gate
    } else {
        const uint8_t pm[16]{};
        const int rc = spu_run_policy_module(policy_entry, 63, pm, sizeof pm, 0x1000, 0, 0x2000, 0);
        semantics = policy_ok && rc == 0 && g_spurs_pm_polls == 4097;
    }
    const std::string text = capture.finish();
    bool logs;
    if (dma) logs = occurrences(text, "[mfc-list] RESUME-ELEMS") == unsigned(on) &&
        occurrences(text, "[mfc-list] STALL") == 1 && occurrences(text, "[mfc-list] RESUME img=") == 1 &&
        occurrences(text, "but no parked list") == 1;
    else logs = occurrences(text, "[spurs-pm] poll") == (on ? 5u : 0u) &&
        occurrences(text, "[spurs-pm] RUN#") == unsigned(on) && occurrences(text, "[spurs-pm] END") == unsigned(on) &&
        occurrences(text, "[spurs-pm] exit-to-kernel") == unsigned(on);
    if (!semantics) std::fprintf(stderr, "%s", text.c_str());
    std::fprintf(stderr, "hot trace %s %s: semantics=%d log_contract=%d\n", mode,
        semantics && logs ? "PASS" : "FAIL", semantics, logs);
    return semantics && logs ? 0 : 1;
}
