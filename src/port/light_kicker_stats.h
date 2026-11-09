#pragma once

/* Optional observations of this title's Light kicker. Reads are independent
 * snapshots while guest threads keep running; no guest state is modified. */
static bool i2_light_kicker_stats_enabled(void)
{
#ifdef _WIN32
    static const bool enabled = [] {
        const char* value = getenv("I2_LIGHT_KICKER_STATS");
        return value && *value && strcmp(value, "0") != 0;
    }();
    return enabled;
#else
    return false;
#endif
}

static void i2_light_kicker_stats(void)
{
#ifdef _WIN32
    if (!i2_light_kicker_stats_enabled()) return;
    static ULONGLONG previous = 0;
    const ULONGLONG now = GetTickCount64();
    if (previous && now - previous < 1000) return;
    previous = now;
    const DWORD saved_error = GetLastError();
    const uint64_t limit = ppu_vm_size ? ppu_vm_size : 0x100000000ULL;
    auto read_word = [limit](uint32_t ea, uint32_t& value) {
        if (!vm_base || !ea || (ea & 3) || uint64_t(ea) + 4 > limit) return false;
        unsigned char bytes[4];
        SIZE_T copied = 0;
        if (!ReadProcessMemory(GetCurrentProcess(), vm_base + ea, bytes, 4, &copied) || copied != 4)
            return false;
        value = (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) |
                (uint32_t(bytes[2]) << 8) | bytes[3];
        return true;
    };
    const uint32_t addresses[] = {
        0xA25A18, 0xA25810, 0xA25884, 0xA25888, 0xA25890, 0xA25894,
        0xA25800, 0xA25804
    };
    uint32_t values[10] = {}, mask = 0;
    for (unsigned i = 0; i < 8; ++i)
        if (read_word(addresses[i], values[i])) mask |= 1u << i;
    if ((mask & (1u << 2)) && read_word(values[2], values[8])) mask |= 1u << 8;
    if ((mask & (1u << 3)) && read_word(values[3], values[9])) mask |= 1u << 9;
    fprintf(stderr, "[i2-light-kicker] t_ms=%llu valid_mask=%03X state=%08X selector=%08X "
            "counter0_ea=%08X counter1_ea=%08X ticket0=%08X ticket1=%08X "
            "fence0=%08X fence4=%08X counter0=%08X counter1=%08X concurrent=1\n",
            (unsigned long long)now, mask, values[0], values[1], values[2], values[3],
            values[4], values[5], values[6], values[7], values[8], values[9]);
    /* The title's producer uses the context pointer at B011E0. Observe its
     * ownership and write head independently of the FIFO walker's GET. */
    static const bool context_enabled = [] {
        const char* value = getenv("I2_GCM_CONTEXT_STATS");
        return value && *value && strcmp(value, "0") != 0;
    }();
    if (context_enabled) {
        uint32_t c[12] = {}, valid = 0;
        if (read_word(0xB011E0, c[0])) valid |= 1u;
        if ((valid & 1u) && c[0] && c[0] <= 0xFFFFFFF3u)
            for (unsigned i = 1; i <= 4; ++i)
                if (read_word(c[0] + (i - 1) * 4, c[i])) valid |= 1u << i;
        if ((valid & (1u << 4)) && read_word(c[4], c[5])) valid |= 1u << 5;
        for (unsigned i = 6; i <= 8; ++i)
            if (read_word(0x20002000 + (i - 6) * 4, c[i])) valid |= 1u << i;
        if (valid & (1u << 3)) {
            if (c[3] >= 4 && read_word(c[3] - 4, c[9])) valid |= 1u << 9;
            if (read_word(c[3], c[10])) valid |= 1u << 10;
            if (c[3] <= 0xFFFFFFFBu && read_word(c[3] + 4, c[11])) valid |= 1u << 11;
        }
        fprintf(stderr, "[i2-gcm-context] t_ms=%llu valid_mask=%03X context=%08X "
                "begin=%08X end=%08X current=%08X callback=%08X callback_entry=%08X "
                "put=%08X get=%08X ref=%08X prev_word=%08X current_word=%08X "
                "next_word=%08X concurrent=1\n",
                (unsigned long long)now, valid, c[0], c[1], c[2], c[3], c[4],
                c[5], c[6], c[7], c[8], c[9], c[10], c[11]);
        uint32_t driver[14] = {}, driver_valid = 0;
        const uint32_t driver_addresses[] = {
            0xA009EC, 0xA00A18, 0xA00A1C, 0xB01284, 0xB01280, 0xA00A00
        };
        for (unsigned i = 0; i < 6; ++i)
            if (read_word(driver_addresses[i], driver[i])) driver_valid |= 1u << i;
        if ((driver_valid & (1u << 5)) && driver[5] <= 1) {
            if (read_word(0xA009F0 + driver[5] * 4, driver[6])) driver_valid |= 1u << 6;
            if (read_word(0xA009F8 + driver[5] * 4, driver[7])) driver_valid |= 1u << 7;
            if ((driver_valid & (1u << 7)) && read_word(driver[7], driver[13]))
                driver_valid |= 1u << 13;
        }
        const uint32_t ring_context = (driver_valid & (1u << 4)) && driver[4] < 4 ?
            0xB01240 + driver[4] * 16 : 0;
        if (ring_context) {
            for (unsigned i = 8; i <= 11; ++i)
                if (read_word(ring_context + (i - 8) * 4, driver[i])) driver_valid |= 1u << i;
            if ((driver_valid & (1u << 11)) && read_word(driver[11], driver[12]))
                driver_valid |= 1u << 12;
        }
        fprintf(stderr, "[i2-gcm-driver] t_ms=%llu valid_mask=%04X base=%08X pending=%08X "
                "flags=%08X control=%08X ring_index=%u buffer_selector=%u buffer_start=%08X "
                "closing_jump=%08X closing_word=%08X ring_context=%08X ring_begin=%08X "
                "ring_end=%08X ring_current=%08X ring_callback=%08X ring_callback_entry=%08X "
                "concurrent=1\n",
                (unsigned long long)now, driver_valid, driver[0], driver[1], driver[2], driver[3],
                driver[4], driver[5], driver[6], driver[7], driver[13], ring_context,
                driver[8], driver[9], driver[10], driver[11], driver[12]);
    }
    SetLastError(saved_error);
#endif
}

#ifdef _WIN32
/* A FIFO drain can block for seconds compiling a new shader. Keep observing
 * the kicker independently, including while the frame clock is in that drain. */
static DWORD WINAPI i2_light_kicker_watch_thread(LPVOID)
{
    for (;;) {
        i2_light_kicker_stats();
        i2_wws_stats();
        Sleep(1000);
    }
    return 0;
}
#endif
