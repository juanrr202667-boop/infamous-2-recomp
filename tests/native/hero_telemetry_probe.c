#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <math.h>
#include <stdint.h>

uint8_t* vm_base = NULL;
uint32_t ppu_vm_size = 0;

static inline void guest_write32(uint64_t addr, uint32_t val)
{
    if (!vm_base) return;
    uint8_t* p = vm_base + addr;
    p[0] = (uint8_t)(val >> 24);
    p[1] = (uint8_t)(val >> 16);
    p[2] = (uint8_t)(val >> 8);
    p[3] = (uint8_t)(val);
}

static inline uint32_t guest_read32(uint64_t addr)
{
    if (!vm_base) return 0;
    const uint8_t* p = vm_base + addr;
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  | ((uint32_t)p[3]);
}

static inline void guest_write_float(uint64_t addr, float f)
{
    uint32_t u;
    memcpy(&u, &f, sizeof(u));
    guest_write32(addr, u);
}

static inline float guest_read_float(uint64_t addr)
{
    uint32_t u = guest_read32(addr);
    float f;
    memcpy(&f, &u, sizeof(f));
    return f;
}

static inline int is_valid_guest_addr(uint32_t ea, uint32_t size)
{
    if (!vm_base || ea < 0x10000) return 0;
    uint64_t limit = ppu_vm_size ? ppu_vm_size : 0x100000000ULL;
    return ((uint64_t)ea + size) < limit;
}

int main(void)
{
    printf("[test] Initializing guest arena mock (16 MB)...\n");
    vm_base = (uint8_t*)calloc(1, 0x1000000);
    assert(vm_base != NULL);
    ppu_vm_size = 0x1000000;

    // 1. Set State Manager at 0x00ABFC60 with current_state at 0x00ABFE50 = 3
    guest_write32(0x00ABFE50, 3);
    assert(guest_read32(0x00ABFE50) == 3);

    // 2. Set CameraDriver at 0x00ABFC64 -> points to instance 0x00200000
    const uint32_t kCameraDriverEA = 0x00200000;
    guest_write32(0x00ABFC64, kCameraDriverEA);

    // 3. CameraDriver + 0x2E0 points to CHero (Cole) at 0x00300000
    const uint32_t kHeroEA = 0x00300000;
    guest_write32(kCameraDriverEA + 0x2E0, kHeroEA);

    // 4. Em CHero:
    // +0x000: Vtable (0x008D6418)
    // +0x004: Type ID (0x0000000B)
    guest_write32(kHeroEA + 0x000, 0x008D6418);
    guest_write32(kHeroEA + 0x004, 0x0000000B);

    // Test world coordinates
    const float kExpectedX = 100.5f;
    const float kExpectedY = 12.25f;
    const float kExpectedZ = -350.125f;
    guest_write_float(kHeroEA + 0x340, kExpectedX);
    guest_write_float(kHeroEA + 0x344, kExpectedY);
    guest_write_float(kHeroEA + 0x348, kExpectedZ);
    guest_write_float(kHeroEA + 0x34C, 1.0f);

    // 5. Resolve pointers exactly as i2_poll_cole_telemetry() does
    uint32_t resolved_hero = 0;
    uint32_t camDriver = guest_read32(0x00ABFC64);
    assert(is_valid_guest_addr(camDriver, 0x2E4));
    uint32_t target_cand = guest_read32(camDriver + 0x2E0);
    assert(is_valid_guest_addr(target_cand, 0x350));
    assert(guest_read32(target_cand + 0x04) == 0x0000000B);
    resolved_hero = target_cand;

    assert(resolved_hero == kHeroEA);

    float read_x = guest_read_float(resolved_hero + 0x340);
    float read_y = guest_read_float(resolved_hero + 0x344);
    float read_z = guest_read_float(resolved_hero + 0x348);

    printf("[test] Read Cole Pos: X=%.3f, Y=%.3f, Z=%.3f\n", read_x, read_y, read_z);
    assert(fabsf(read_x - kExpectedX) < 1e-6f);
    assert(fabsf(read_y - kExpectedY) < 1e-6f);
    assert(fabsf(read_z - kExpectedZ) < 1e-6f);

    printf("[COLE_POS] X=%.3f Y=%.3f Z=%.3f\n", read_x, read_y, read_z);
    printf("[test] SUCCESS: Cole telemetry probe test passed!\n");
    return 0;
}
