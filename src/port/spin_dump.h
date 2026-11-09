#pragma once
#include "spin_snapshot.h"

/* I2_SPIN_DUMP=<slot>[,after_ms[,every_ms[,max]]] -- read the job-barrier
 * object the title is parked on, without touching guest state.
 *
 * The engine's job barrier (guest 0x00505490) spins on a queue object. Its
 * caller keeps that object at [saved_r31] and the poller reads, per row:
 *   queue[0x30 + row*4]   lane table pointer
 *   queue[0x40 + row*16]  ticket (u16)
 *   lane[ticket]          u16 at lane + ticket*8 + 2
 * The callee's frame saves r31 at slot-0x18, r30 at slot-0x20 and its return
 * address (the instruction after the `bl`) at slot. Watching those slots names
 * the object and shows whether tickets advance, which distinguishes "the
 * waiter is fine and the producer is gone" from "the ticket was never read
 * from the right place". Observing only: no guest memory is written.
 *
 * Slot default is 0x0FEFFAF0, the LR slot seen in every stalled run so far.
 * Enable late (after_ms) so the dump covers the stall instead of boot. */

#ifdef _WIN32
static bool i2_spin_dump_enabled(void)
{
    static const bool enabled = [] {
        const char* value = getenv("I2_SPIN_DUMP");
        return value && *value && strcmp(value, "0") != 0;
    }();
    return enabled;
}

static DWORD WINAPI i2_spin_dump_thread(LPVOID)
{
    const char* spec = getenv("I2_SPIN_DUMP");
    if (!spec || !*spec || !strcmp(spec, "0")) return 0;

    uint32_t slot = 0x0FEFFAF0u;
    unsigned after_ms = 0, every_ms = 3000, max_dumps = 40;
    {
        char buf[128];
        snprintf(buf, sizeof buf, "%s", spec);
        char* p = buf;
        char* comma = strchr(p, ',');
        if (comma) *comma = 0;
        if (*p && strcmp(p, "1") != 0) slot = (uint32_t)strtoul(p, nullptr, 16);
        if (comma) {
            p = comma + 1;
            if (*p) after_ms = (unsigned)strtoul(p, nullptr, 0);
            comma = strchr(p, ',');
            if (comma) {
                *comma = 0;
                p = comma + 1;
                if (*p) every_ms = (unsigned)strtoul(p, nullptr, 0);
                comma = strchr(p, ',');
                if (comma && *(comma + 1)) max_dumps = (unsigned)strtoul(comma + 1, nullptr, 0);
            }
        }
    }
    if (!every_ms) every_ms = 3000;

    const uint64_t limit = ppu_vm_size ? ppu_vm_size : 0x100000000ULL;
    auto read_word = [limit](uint32_t ea, uint32_t& value) {
        if (!vm_base || !ea || (ea & 3) || uint64_t(ea) + 4 > limit) return false;
        unsigned char bytes[4];
        SIZE_T copied = 0;
        if (!ReadProcessMemory(GetCurrentProcess(), vm_base + ea, bytes, 4, &copied) ||
            copied != 4)
            return false;
        value = ((uint32_t)bytes[0] << 24) | ((uint32_t)bytes[1] << 16) |
                ((uint32_t)bytes[2] << 8) | (uint32_t)bytes[3];
        return true;
    };
    auto read_half = [&](uint32_t ea, uint32_t& value) {
        uint32_t word = 0;
        if (!read_word(ea & ~3u, word)) return false;
        value = (ea & 2) ? (word & 0xFFFFu) : (word >> 16);
        return true;
    };
    /* Saved PPU registers are full 64-bit GPRs stored big-endian, so the value
     * lives in the LAST four bytes of the slot: reading the first four yields
     * the zero high half. */
    auto read_wide = [&](uint32_t ea, uint32_t& value) {
        uint32_t hi = 0, lo = 0;
        if (!read_word(ea, hi) || !read_word(ea + 4, lo)) return false;
        value = lo;
        return true;
    };

    const ULONGLONG started = GetTickCount64();
    fprintf(stderr, "[i2-spin-dump] armed slot=%08X after=%ums every=%ums max=%u\n",
            slot, after_ms, every_ms, max_dumps);
    unsigned dumps = 0, waits = 0;
    ULONGLONG last = 0, last_wait = 0, window = started;
    unsigned hits = 0;
    for (;;) {
        const ULONGLONG now = GetTickCount64();
        if (now - window >= 250) { window = now; hits = 0; }
        if (!vm_base) { Sleep(50); continue; }
        if (now - started < after_ms) { Sleep(10); continue; }

        /* Repeated matching saved LR values filter noisy samples. They do not
         * count calls or prove that this frame is still executing: stack slots
         * may retain values after return. The active row is not captured. */
        uint32_t lr = 0;
        if (!read_wide(slot, lr) ||
            (lr != 0x00370600u && lr != 0x00370640u && lr != 0x00370668u)) {
            Sleep(0);
            continue;
        }
        ++hits;
        if (hits < 40) { Sleep(0); continue; }
        if (last && now - last < every_ms) { Sleep(1); continue; }
        if (dumps >= max_dumps) { Sleep(100); continue; }

        uint32_t r31 = 0, r30 = 0;
        const bool read_ok = read_wide(slot - 0x18, r31) && read_wide(slot - 0x20, r30);
        uint32_t again = 0;
        uint32_t queue = 0;
        const bool stable = read_ok && read_wide(slot, again) && again == lr;
        const bool live = stable && read_word(r31, queue) && queue >= 0x10000 &&
                          uint64_t(queue) + 0x80 <= limit;
        if (!live) {
            /* Say why nothing is printed: a silent observer cannot be told
             * apart from a missing one. */
            if (waits < 12 && (!last_wait || now - last_wait >= 30000)) {
                last_wait = now;
                ++waits;
                fprintf(stderr,
                        "[i2-spin-dump] waiting: lr=%08X r31=%08X r30=%08X queue=%08X "
                        "read_ok=%d stable=%d\n",
                        lr, r31, r30, queue, read_ok ? 1 : 0, stable ? 1 : 0);
            }
            continue;
        }

        last = now;
        ++dumps;
        fprintf(stderr,
                "[i2-spin-dump] #%u t=%llums slot=%08X lr=%08X r31=%08X queue=%08X "
                "r30=%08X matching_samples=%u active_row=unknown\n",
                dumps, (unsigned long long)now, slot, lr, r31, queue, r30, hits);
        for (uint32_t off = 0; off < 0x30; off += 0x10) {
            uint32_t w[4] = {};
            for (unsigned i = 0; i < 4; ++i) read_word(queue + off + i * 4, w[i]);
            fprintf(stderr, "[i2-spin-dump]   queue+%02X: %08X %08X %08X %08X\n",
                    off, w[0], w[1], w[2], w[3]);
        }
        for (unsigned row = 0; row < 4; ++row) {
            const auto sample = i2_read_spin_row(queue, row, limit, read_word, read_half);
            fprintf(stderr,
                    "[i2-spin-dump]   row%u ticket=%04X lanes=%04X %04X %04X %04X %04X %04X "
                    "%04X min=%04X effective=%X advance=%u counters_ready=%d "
                    "lane=%08X status=%s poll_result=unknown\n",
                    row, sample.ticket, sample.lanes[0], sample.lanes[1], sample.lanes[2],
                    sample.lanes[3], sample.lanes[4], sample.lanes[5], sample.lanes[6],
                    sample.lowest, sample.effective, sample.advance, sample.counters_ready,
                    sample.lane, sample.status);
        }
        fflush(stderr);
    }
    return 0;
}
#endif
