/* Real MFC channels, synthetic memory only. CBEA 1.01 section 9.1.4
 * permits size zero; it must complete its tag without moving data or
 * diagnosing an invalid transfer. Positive-size invalid controls remain. */
#include "ppu_recomp.h"
#include "spu_context.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <io.h>
#include <memory>
#include <string>

extern "C" void spu_wrch(spu_context*, uint32_t, u128);
extern "C" u128 spu_rdch(spu_context*, uint32_t);
static uint8_t memory[SPU_LS_SIZE];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

namespace {
void write_channel(spu_context* ctx, uint32_t channel, uint32_t value) {
    u128 argument{};
    argument._u32[0] = value;
    spu_wrch(ctx, channel, argument);
}

bool transfer(spu_context* ctx, uint32_t lsa, uint32_t ea,
              uint32_t size, uint32_t command) {
    constexpr uint32_t tag = 5;
    write_channel(ctx, MFC_LSA, lsa);
    write_channel(ctx, MFC_EAH, 0);
    write_channel(ctx, MFC_EAL, ea);
    write_channel(ctx, MFC_Size, size);
    write_channel(ctx, MFC_TagID, tag);
    write_channel(ctx, MFC_Cmd, command);
    write_channel(ctx, MFC_WrTagMask, 1u << tag);
    write_channel(ctx, MFC_WrTagUpdate, 2);
    return spu_rdch(ctx, MFC_RdTagStat)._u32[0] == (1u << tag);
}

class StderrCapture {
public:
    StderrCapture() : file_(std::tmpfile()), saved_(-1) {
        if (!file_) return;
        std::fflush(stderr);
        saved_ = _dup(_fileno(stderr));
        if (saved_ >= 0 && _dup2(_fileno(file_), _fileno(stderr)) != 0) {
            _close(saved_);
            saved_ = -1;
        }
    }
    ~StderrCapture() {
        restore();
        if (file_) std::fclose(file_);
    }
    bool ready() const { return saved_ >= 0; }
    std::string finish() {
        restore();
        std::rewind(file_);
        std::string result;
        char buffer[512];
        size_t count;
        while ((count = std::fread(buffer, 1, sizeof buffer, file_)) != 0)
            result.append(buffer, count);
        return result;
    }
private:
    void restore() {
        if (saved_ < 0) return;
        std::fflush(stderr);
        _dup2(saved_, _fileno(stderr));
        _close(saved_);
        saved_ = -1;
    }
    FILE* file_;
    int saved_;
};
}

int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "zero-get";
    // Fresh process per CTest also gives each mode fresh cached diagnostics.
    _putenv_s("SPU_DMA_LAX", "");
    _putenv_s("SPU_DMACHK", std::strcmp(mode, "zero-dmachk") == 0 ? "1" : "");
    auto ctx = std::make_unique<spu_context>();
    spu_context_init(ctx.get(), 61);
    ctx->image_id = 61;
    ctx->pc = 0x100;
    std::memset(memory, 0xA5, sizeof memory);
    std::memset(ctx->ls, 0x5A, SPU_LS_SIZE);
    auto original_memory = std::make_unique<uint8_t[]>(sizeof memory);
    auto original_ls = std::make_unique<uint8_t[]>(SPU_LS_SIZE);
    std::memcpy(original_memory.get(), memory, sizeof memory);
    std::memcpy(original_ls.get(), ctx->ls, SPU_LS_SIZE);

    uint32_t lsa = 0x1000, ea = 0x10000, size = 0, command = MFC_GET_CMD;
    bool invalid = false, valid_copy = false;
    if (std::strcmp(mode, "zero-put") == 0) command = MFC_PUT_CMD;
    else if (std::strcmp(mode, "zero-put-low") == 0) { ea = 0; command = MFC_PUT_CMD; }
    else if (std::strcmp(mode, "oversized") == 0) { size = 0x4010; invalid = true; }
    else if (std::strcmp(mode, "unaligned") == 0) { ea += 4; size = 16; invalid = true; }
    else if (std::strcmp(mode, "nonmultiple") == 0) { size = 17; invalid = true; }
    else if (std::strcmp(mode, "low-put") == 0) { ea = 0; size = 16; command = MFC_PUT_CMD; invalid = true; }
    else if (std::strcmp(mode, "valid-get") == 0) { size = 32; valid_copy = true; }
    else if (std::strcmp(mode, "valid-put") == 0) { size = 32; command = MFC_PUT_CMD; valid_copy = true; }
    else if (std::strcmp(mode, "zero-get") != 0 && std::strcmp(mode, "zero-dmachk") != 0) return 2;

    StderrCapture capture;
    if (!capture.ready()) { std::fprintf(stderr, "dma FAIL: stderr capture unavailable\n"); return 2; }
    const bool complete = transfer(ctx.get(), lsa, ea, size, command);
    const std::string diagnostic = capture.finish();
    const bool rejected = diagnostic.find("REJECTED malformed transfer") != std::string::npos;
    const bool bad = diagnostic.find("[dmachk] BAD") != std::string::npos;
    bool memory_ok;
    if (valid_copy) {
        if (command == MFC_GET_CMD) std::memcpy(original_ls.get() + lsa, original_memory.get() + ea, size);
        else std::memcpy(original_memory.get() + ea, original_ls.get() + lsa, size);
    }
    memory_ok = std::memcmp(original_memory.get(), memory, sizeof memory) == 0 &&
                std::memcmp(original_ls.get(), ctx->ls, SPU_LS_SIZE) == 0;
    if ((!invalid && !complete) || !memory_ok || rejected != invalid || (!invalid && bad)) {
        std::fprintf(stderr, "dma %s FAIL: tag=%d memory=%d rejected=%d bad=%d\n%s",
                     mode, complete, memory_ok, rejected, bad, diagnostic.c_str());
        return 1;
    }
    std::fprintf(stderr, "dma %s PASS: tag=%d memory verified, rejected=%d\n", mode, complete, rejected);
    return 0;
}
