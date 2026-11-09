#pragma once
#include "wws_target_scan.h"

/* Local BCUS98125 snapshots for the completion predicate in guest 505490.
 * ReadProcessMemory tolerates an uninitialized/unmapped guest pointer. Reads
 * are concurrent observations, never an atomic proof of guest completion. */
static bool i2_wws_stats_enabled(void)
{
#ifdef _WIN32
    static const bool enabled = [] {
        const char* value = getenv("I2_WWS_STATS");
        return value && *value && strcmp(value, "0") != 0;
    }();
    return enabled;
#else
    return false;
#endif
}

static bool i2_wws_target_full_scan_enabled(void)
{
#ifdef _WIN32
    static const bool enabled = [] {
        const char* value = getenv("I2_WWS_TARGET_SCAN");
        return value && strcmp(value, "full") == 0;
    }();
    return enabled;
#else
    return false;
#endif
}

static void i2_wws_stats(void)
{
#ifdef _WIN32
    if (!i2_wws_stats_enabled()) return;
    const DWORD saved_error = GetLastError();
    const bool full_scan = i2_wws_target_full_scan_enabled();
    static volatile LONG full_scan_busy = 0;
    static volatile LONG full_scan_samples = 0;
    unsigned samples = 0;
    if (full_scan) {
        // This serializes only the opt-in observer with itself. Guest writes
        // remain concurrent and are checked by repeated reads below.
        if (InterlockedCompareExchange(&full_scan_busy, 1, 0) != 0) {
            SetLastError(saved_error);
            return;
        }
        const LONG count = InterlockedIncrement(&full_scan_samples);
        if (count > 120) {
            InterlockedExchange(&full_scan_busy, 0);
            SetLastError(saved_error);
            return;
        }
        samples = unsigned(count);
    } else {
        static unsigned legacy_samples = 0;
        if (legacy_samples >= 120) {
            SetLastError(saved_error);
            return;
        }
        samples = ++legacy_samples;
    }
    const uint64_t limit = ppu_vm_size ? ppu_vm_size : 0x100000000ULL;
    auto read = [limit](uint64_t ea, void* bytes, SIZE_T count) {
        if (!vm_base || !ea || ea >= limit || count > limit - ea) return false;
        SIZE_T copied = 0;
        return ReadProcessMemory(GetCurrentProcess(), vm_base + ea,
            bytes, count, &copied) && copied == count;
    };
    auto be16 = [](const unsigned char* p) {
        return (unsigned(p[0]) << 8) | p[1];
    };
    auto be32 = [](const unsigned char* p) -> uint32_t {
        return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) |
               (uint32_t(p[2]) << 8) | p[3];
    };
    unsigned char object[12] = {}, descriptor[0x40] = {}, active[4] = {};
    unsigned char selector_bytes[4] = {}, flags[4] = {};
    bool object_ok = read(0xA94B80, object, sizeof(object));
    const uint32_t data = be32(object), spurs = be32(object + 4), wid = be32(object + 8);
    bool data_ok = object_ok && data && read(uint64_t(data) + 0x20, descriptor, sizeof(descriptor));
    bool active_ok = data_ok && read(uint64_t(data) + 8, active, sizeof(active));
    bool selector_ok = read(0xA25810, selector_bytes, sizeof(selector_bytes));
    bool flags_ok = read(0xA259A0, flags, sizeof(flags));
    const uint32_t selector = be32(selector_bytes);
    unsigned char ready = 0, contention = 0;
    bool mirror_ok = object_ok && spurs && wid < 16 && read(uint64_t(spurs) + wid, &ready, 1) &&
        read(uint64_t(spurs) + 0x20 + wid, &contention, 1);
    for (unsigned buffer = 0; buffer < 2; ++buffer) {
        unsigned char capacity_bytes[4] = {}, entries[4][2] = {}, recheck[2] = {};
        bool capacity_ok = read(0xA94BA8 + 0x1Cu * buffer, capacity_bytes, 4);
        const uint32_t capacity = be32(capacity_bytes);
        const uint32_t list = be32(descriptor + 0x10 + buffer * 4);
        const unsigned char* row = descriptor + 0x20 + buffer * 16;
        const unsigned ticket = be16(row);
        unsigned minimum = 0xFFFF, lanes[7] = {}, entry_mask = 0;
        for (unsigned lane = 0; lane < 7; ++lane) {
            lanes[lane] = be16(row + 2 + lane * 2);
            if (lanes[lane] < minimum) minimum = lanes[lane];
        }
        unsigned target = ticket;
        bool target_known = false;
        // Preserve the legacy four-slot output. The separate opt-in scan below
        // is bounded by both capacity and its 32-entry diagnostic limit.
        if (data_ok && capacity_ok && list && ticket < capacity && ticket != 0xFFFF &&
            uint64_t(list) + uint64_t(capacity) * 8 <= limit) {
            for (unsigned slot = 0; slot < 4 && target < capacity; ++slot) {
                if (!read(uint64_t(list) + uint64_t(target) * 8 + 2, entries[slot], 2)) break;
                entry_mask |= 1u << slot;
                if (!be16(entries[slot])) { target_known = true; break; }
                ++target;
            }
        }
        bool recheck_ok = data_ok && read(uint64_t(data) + 0x40 + buffer * 16, recheck, 2);
        I2WwsTargetScan full_result{ticket, 0, false, false, false, false};
        if (full_scan && data_ok && capacity_ok && list && ticket < capacity &&
            uint64_t(list) + uint64_t(capacity) * 8 <= limit) {
            const auto read_entry = [&](unsigned index, uint16_t& value) {
                unsigned char bytes[2] = {};
                if (!read(uint64_t(list) + uint64_t(index) * 8 + 2, bytes, 2)) return false;
                value = uint16_t(be16(bytes));
                return true;
            };
            const auto read_ticket = [&](unsigned& value) {
                unsigned char bytes[2] = {};
                if (!read(uint64_t(data) + 0x40 + buffer * 16, bytes, 2)) return false;
                value = be16(bytes);
                return true;
            };
            full_result = i2_wws_scan_target(ticket, capacity, 32, read_entry, read_ticket);
        }
        bool scan_metadata_stable = false;
        if (full_result.exact_observed) {
            unsigned char data_after[4] = {}, list_after[4] = {}, capacity_after[4] = {};
            scan_metadata_stable = read(0xA94B80, data_after, 4) &&
                read(uint64_t(data) + 0x30 + buffer * 4, list_after, 4) &&
                read(0xA94BA8 + 0x1Cu * buffer, capacity_after, 4) &&
                be32(data_after) == data && be32(list_after) == list &&
                be32(capacity_after) == capacity;
            full_result.exact_observed = full_result.exact_observed && scan_metadata_stable;
        }
        int branch = selector_ok && flags_ok && selector <= 1 ? flags[1 - selector] : -1;
        fprintf(stderr, "[i2-wws] sample=%u data=%08X data_valid=%u buffer=%u "
            "list=%08X capacity=%u capacity_valid=%u ticket=%u ticket_recheck=%u recheck_valid=%u "
            "lanes=%u,%u,%u,%u,%u,%u,%u min=%u entries=%u,%u,%u,%u entry_mask=%X "
            "target_lower=%u target_known=%u scan_full=%u target_scan_candidate=%u "
            "target_exact_observed=%u "
            "scan_entries=%u scan_terminator=%u scan_entries_stable=%u scan_ticket_stable=%u "
            "scan_metadata_stable=%u "
            "active=%u active_valid=%u "
            "spurs=%08X wid=%u mirror_valid=%u ready=%u contention=%u "
            "light_selector=%u light_branch=%d concurrent=1\n",
            samples, data, data_ok, buffer, list, capacity, capacity_ok,
            ticket, be16(recheck), recheck_ok,
            lanes[0], lanes[1], lanes[2], lanes[3], lanes[4], lanes[5], lanes[6], minimum,
            be16(entries[0]), be16(entries[1]), be16(entries[2]), be16(entries[3]), entry_mask,
            target, target_known, full_scan, full_result.target, full_result.exact_observed,
            full_result.entries_read, full_result.terminator_seen, full_result.entries_stable,
            full_result.ticket_stable, scan_metadata_stable, be32(active), active_ok, spurs, wid,
            mirror_ok, ready,
            contention, selector, branch);
    }
    if (samples == 120) fprintf(stderr, "[i2-wws] trace_cap=120\n");
    if (full_scan) InterlockedExchange(&full_scan_busy, 0);
    SetLastError(saved_error);
#endif
}
