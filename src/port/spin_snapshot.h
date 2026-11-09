#pragma once
#include <cstdint>

struct I2SpinRowSnapshot {
    uint32_t ticket = 0, lane = 0, effective = 0, advance = 0;
    uint32_t lanes[7] = {}, lowest = 0xFFFF;
    const char* status = "unreadable";
    int counters_ready = -1;
};

template<class ReadWord, class ReadHalf>
I2SpinRowSnapshot i2_read_spin_row(uint32_t queue, unsigned row, uint64_t limit,
                                 ReadWord read_word, ReadHalf read_half,
                                 unsigned scan_limit = 4096)
{
    I2SpinRowSnapshot out;
    // Read-only evidence, not the guest poller's return value. An asynchronous
    // sample cannot establish frame liveness or satisfy the workload-info gate.
    if (limit > 0x100000000ULL) limit = 0x100000000ULL;
    if (row >= 4 || queue < 0x10000 || uint64_t(queue) + 0x80 > limit) return out;
    const uint32_t row_ea = queue + 0x40 + row * 16;
    if (!read_half(row_ea, out.ticket) ||
        !read_word(queue + 0x30 + row * 4, out.lane)) return out;
    auto scan = [&](uint32_t ticket, uint32_t lane, uint32_t& advance) {
        advance = 0;
        if (lane < 0x10000 || (lane & 1)) return false;
        for (unsigned i = 0; i < scan_limit; ++i) {
            const uint64_t ea = uint64_t(lane) + (uint64_t(ticket) + i) * 8 + 2;
            uint32_t marker = 0;
            if (ea + 2 > limit || !read_half(static_cast<uint32_t>(ea), marker))
                return false;
            if (!marker) return true;
            ++advance;
        }
        out.status = "scan-limit";
        return false;
    };
    if (!scan(out.ticket, out.lane, out.advance)) return out;
    // The guest adds in a GPR; the target is not truncated back to u16.
    out.effective = out.ticket + out.advance;
    for (unsigned i = 0; i < 7; ++i) {
        if (!read_half(row_ea + 2 + i * 2, out.lanes[i])) return out;
        if (out.lanes[i] < out.lowest) out.lowest = out.lanes[i];
    }
    uint32_t ticket_again = 0, lane_again = 0, advance_again = 0;
    if (!read_half(row_ea, ticket_again) ||
        !read_word(queue + 0x30 + row * 4, lane_again) ||
        !scan(ticket_again, lane_again, advance_again)) return out;
    if (ticket_again != out.ticket || lane_again != out.lane ||
        ticket_again + advance_again != out.effective) {
        out.status = "changed";
        return out;
    }
    out.status = "sampled";
    out.counters_ready = out.lowest >= out.effective;
    return out;
}
