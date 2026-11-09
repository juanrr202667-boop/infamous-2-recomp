#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include "../../tools/ps3recomp/libs/input/cellPad.h"

/* Scaffold symbols required by cellPad.c in standalone harness */
uint8_t* vm_base = NULL;
uint32_t g_ww_lo = 0;
uint32_t g_ww_hi = 0;
void ps3_ww_report_inline(uint32_t ea, uint32_t val, int size) { (void)ea; (void)val; (void)size; }
int spu_coh_is_reserved(uint32_t ea, uint32_t size) { (void)ea; (void)size; return 0; }
void spu_lockline_lock(uint32_t ea) { (void)ea; }
void spu_coh_notify_write(uint32_t ea, uint32_t size) { (void)ea; (void)size; }
void spu_lockline_unlock(uint32_t ea) { (void)ea; }
int g_resv_store_active = 0;
void ppu_resv_break_store(uint32_t ea, uint32_t size) { (void)ea; (void)size; }

static inline uint16_t read_u16_be(const uint8_t* p) {
    return ((uint16_t)p[0] << 8) | p[1];
}

int main(void)
{
    _putenv("PAD_ALWAYS_CHANGE=1");
    _putenv("PAD_NO_KEYBOARD=1");

    vm_base = (uint8_t*)calloc(1, 0x100000);
    assert(vm_base != NULL);

    printf("[test] Testing cellPadInit...\n");
    s32 rc = cellPadInit(1);
    assert(rc == 0);

    printf("[test] Injecting pulse: mask=0x4008 (START | CROSS), duration=5 polls...\n");
    cellPad_inject_pulse(0, 0x4008, 5);

    const uint32_t guest_ea = 0x20000;
    for (int i = 0; i < 5; i++) {
        memset(vm_base + guest_ea, 0, sizeof(CellPadData));
        rc = cellPadGetData(0, (CellPadData*)(uintptr_t)guest_ea);
        assert(rc == 0);
        uint16_t btn1 = read_u16_be(vm_base + guest_ea + 4 + CELL_PAD_BTN_OFFSET_DIGITAL1 * 2);
        uint16_t btn2 = read_u16_be(vm_base + guest_ea + 4 + CELL_PAD_BTN_OFFSET_DIGITAL2 * 2);
        printf("[test] Poll %d: digital1=0x%02X (START=%d), digital2=0x%02X (CROSS=%d)\n",
               i + 1, btn1, (btn1 & CELL_PAD_CTRL_START) != 0, btn2, (btn2 & (CELL_PAD_CTRL_CROSS >> 8)) != 0);
        assert((btn1 & 0x08) == 0x08);
        assert((btn2 & 0x40) == 0x40);
    }

    // 6th poll should be back to neutral (0)
    memset(vm_base + guest_ea, 0, sizeof(CellPadData));
    rc = cellPadGetData(0, (CellPadData*)(uintptr_t)guest_ea);
    assert(rc == 0);
    uint16_t btn1 = read_u16_be(vm_base + guest_ea + 4 + CELL_PAD_BTN_OFFSET_DIGITAL1 * 2);
    uint16_t btn2 = read_u16_be(vm_base + guest_ea + 4 + CELL_PAD_BTN_OFFSET_DIGITAL2 * 2);
    printf("[test] Poll 6 (neutralized): digital1=0x%02X, digital2=0x%02X\n", btn1, btn2);
    assert((btn1 & 0x08) == 0);
    assert((btn2 & 0x40) == 0);

    printf("[test] SUCCESS: cellPad_inject_pulse test passed!\n");
    return 0;
}
