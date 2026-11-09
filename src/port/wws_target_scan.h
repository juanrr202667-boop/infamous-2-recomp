#pragma once

#include <cstdint>

/* Bounded observer for the linear sentinel scan in guest 0x00505490.
 * A target is only exact-observed when every entry through the zero sentinel
 * is unchanged on an immediate second read and the ticket is unchanged after
 * the scan. This is still not an atomic snapshot of guest memory. */
struct I2WwsTargetScan {
    unsigned target;
    unsigned entries_read;
    bool terminator_seen;
    bool entries_stable;
    bool ticket_stable;
    bool exact_observed;
};

template <class ReadEntry, class ReadTicket>
static I2WwsTargetScan i2_wws_scan_target(unsigned ticket, unsigned capacity,
    unsigned max_entries, ReadEntry&& read_entry, ReadTicket&& read_ticket)
{
    I2WwsTargetScan result{ticket, 0, false, true, false, false};
    const unsigned available = ticket < capacity ? capacity - ticket : 0u;
    const unsigned bounded_count = max_entries < available ? max_entries : available;
    for (unsigned offset = 0; offset < bounded_count; ++offset) {
        const unsigned index = ticket + offset;
        uint16_t first = 0, confirm = 0;
        if (!read_entry(index, first)) {
            result.entries_stable = false;
            break;
        }
        ++result.entries_read;
        if (!read_entry(index, confirm)) {
            result.entries_stable = false;
            break;
        }
        if (first != confirm) {
            result.entries_stable = false;
            break;
        }
        if (first == 0) {
            result.terminator_seen = true;
            break;
        }
        ++result.target;
    }
    unsigned ticket_after = 0;
    result.ticket_stable = read_ticket(ticket_after) && ticket_after == ticket;
    result.exact_observed = result.terminator_seen && result.entries_stable &&
        result.ticket_stable;
    return result;
}
