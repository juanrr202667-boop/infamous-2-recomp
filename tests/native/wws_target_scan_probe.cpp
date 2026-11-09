#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstdint>

#include "win32_compat.h"
#include "wws_target_scan.h"

uint8_t* vm_base = nullptr;
uint32_t ppu_vm_size = 0;
#include "wws_stats.h"
static void (*const wws_stats_compile_check)() = &i2_wws_stats;

static void require(bool condition, const char* message)
{
    if (condition) return;
    std::fprintf(stderr, "wws_target_scan_probe: %s\n", message);
    std::exit(1);
}

int main()
{
    // A valid, non-wrapping queue sequence with its terminator in slot 32.
    std::array<unsigned short, 64> entries{};
    for (unsigned i = 4; i < 35; ++i) entries[i] = 0x17;
    unsigned reads = 0;
    const auto read_full = [&](unsigned index, unsigned short& value) {
        ++reads;
        if (index >= entries.size()) return false;
        value = entries[index];
        return true;
    };
    const auto ticket_same = [](unsigned& value) { value = 4; return true; };
    auto scan = i2_wws_scan_target(4, 64, 32, read_full, ticket_same);
    require(scan.terminator_seen, "full scan missed the zero terminator at its 32nd entry");
    require(scan.target == 35 && scan.entries_read == 32 && reads == 64,
        "full scan returned the wrong target or read count");
    require(scan.exact_observed, "stable ticket and observed terminator must be reported exact-observed");

    // Four-entry mode models the legacy default and remains only a lower bound.
    reads = 0;
    scan = i2_wws_scan_target(4, 64, 4, read_full, ticket_same);
    require(!scan.terminator_seen && !scan.exact_observed && scan.target == 8,
        "bounded legacy scan must not claim an unseen terminator");
    require(reads == 8, "legacy scan exceeded its configured read bound");

    // Capacity is a hard linear bound; no wrap to slot zero is permitted.
    entries.fill(0x17);
    reads = 0;
    scan = i2_wws_scan_target(62, 64, 32, read_full, ticket_same);
    require(!scan.terminator_seen && !scan.exact_observed && scan.target == 64,
        "capacity truncation must remain unknown rather than wrap");
    require(reads == 4, "capacity truncation must stop at the linear capacity");

    // Failed reads and a changed ticket invalidate exactness.
    reads = 0;
    const auto read_failure = [&](unsigned index, unsigned short& value) {
        ++reads;
        if (index == 5) return false;
        value = 1;
        return true;
    };
    scan = i2_wws_scan_target(4, 64, 32, read_failure, ticket_same);
    require(!scan.terminator_seen && !scan.exact_observed && scan.target == 5 && reads == 3,
        "read failure must preserve only the observed lower bound");

    entries[4] = 0;
    const auto ticket_changed = [](unsigned& value) { value = 5; return true; };
    scan = i2_wws_scan_target(4, 64, 32, read_full, ticket_changed);
    require(scan.terminator_seen && !scan.exact_observed,
        "ticket change during observation must invalidate exactness");

    entries[4] = 1;
    unsigned reads_at_four = 0;
    const auto unstable_entry = [&](unsigned index, unsigned short& value) {
        if (index == 4) value = reads_at_four++ == 0 ? 1 : 2;
        else value = 0;
        return true;
    };
    scan = i2_wws_scan_target(4, 64, 32, unstable_entry, ticket_same);
    require(!scan.terminator_seen && !scan.entries_stable && !scan.exact_observed,
        "an entry changed between observations but was reported exact");
    return 0;
}
