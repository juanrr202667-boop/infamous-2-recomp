/* Exercise the real runner WWS pump against a synthetic guest memory image. */
#define main static unused_port_main
#include "../../../src/port/main.cpp"
#undef main
#include <vector>

extern "C" { uint32_t ppu_vm_size = 0; }

int main(int argc, char** argv)
{
    const bool disabled = argc > 1 && strcmp(argv[1], "disabled") == 0;
    _putenv_s("I2_DISABLE_WWS_LANE_PUMP", disabled ? "1" : "");
    std::vector<uint8_t> memory(0x00B00000, 0);
    vm_base = memory.data();
    ppu_vm_size = static_cast<uint32_t>(memory.size());
    const uint32_t q = 0x00A93900, list = 0x00A80000, ticket_ea = q + 0x40;
    guest_write32(q + 0x30, list);
    guest_write16(ticket_ea, 2);
    guest_write16(list + 2 * 8 + 2, 1);
    guest_write16(list + 3 * 8 + 2, 1);
    guest_write16(list + 4 * 8 + 2, 0);
    guest_write16(ticket_ea + 2, 1);
    for (int lane = 1; lane < 7; ++lane) guest_write16(ticket_ea + 2 + lane * 2, 0xFFFF);

    // Fence behavior is independent and must remain active in either mode.
    guest_write32(0x00A25A18, 3);
    guest_write16(0x00A25800, 0);
    guest_write16(0x00A25802, 1);
    guest_write16(0x00A25804, 1);
    guest_write16(0x00A25806, 1);
    for (int n = 0; n < 33; ++n) i2_poll_wws_sync();
    const uint16_t lane = guest_read16(ticket_ea + 2);
    const uint32_t state = guest_read32(0x00A25A18);
    const uint16_t fence = guest_read16(0x00A25800);
    const uint16_t expected_lane = disabled ? 1 : 4;
    fprintf(stderr, "mode=%s lane=%u expected=%u fence=%u state=%u\n",
            disabled ? "disabled" : "default", lane, expected_lane, fence, state);
    return lane == expected_lane && fence == 0xFFFF && state == 4 ? 0 : 1;
}
