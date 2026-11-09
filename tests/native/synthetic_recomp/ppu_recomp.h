/*
 * Synthetic PPU scaffold header for test fixtures and synthetic runner probes.
 *
 * This file provides only the game-agnostic Cell PPU runtime types (ppu_context,
 * func_entry, PPU_FENCE) required to compile ps3recomp boot scaffolds and test
 * probes in isolation. It contains no proprietary game code, no lifted function
 * signatures, and no game-specific data.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>

#ifndef PPU_FENCE
#  ifdef __cplusplus
#    include <atomic>
#    define PPU_FENCE(o) std::atomic_thread_fence(std::memory_order_##o)
#  else
#    include <stdatomic.h>
#    define PPU_FENCE(o) atomic_thread_fence(memory_order_##o)
#  endif
#endif

typedef union {
    uint8_t  b[16];
    uint16_t h[8];
    uint32_t w[4];
    uint64_t d[2];
    float    f[4];
} ppu_vr;

typedef struct ppu_context {
    uint64_t gpr[32];
    double   fpr[32];
#ifdef _MSC_VER
    __declspec(align(16)) ppu_vr vr[32];
#else
    ppu_vr   vr[32] __attribute__((aligned(16)));
#endif
    uint32_t cr;
    uint64_t lr;
    uint64_t ctr;
    uint32_t xer;
    uint32_t fpscr;
    uint32_t vscr;
    uint64_t cia;
    uint64_t thread_id;
    uint64_t reserve_addr;
    uint64_t reserve_value;
    int      reserve_valid;
} ppu_context;

#ifdef __cplusplus
extern "C" {
#endif

uint8_t  vm_read8 (uint64_t addr);
uint16_t vm_read16(uint64_t addr);
uint32_t vm_read32(uint64_t addr);
uint64_t vm_read64(uint64_t addr);
void     vm_write8 (uint64_t addr, uint8_t  val);
void     vm_write16(uint64_t addr, uint16_t val);
void     vm_write32(uint64_t addr, uint32_t val);
void     vm_write64(uint64_t addr, uint64_t val);
int      ppu_stwcx32(uint64_t addr, uint32_t expected, uint32_t val);
int      ppu_stdcx64(uint64_t addr, uint64_t expected, uint64_t val);

void lv2_syscall(ppu_context* ctx);

#ifndef PPU_THREAD_LOCAL
#  ifdef _MSC_VER
#    define PPU_THREAD_LOCAL __declspec(thread)
#  else
#    define PPU_THREAD_LOCAL __thread
#  endif
#endif

typedef struct {
    uint64_t addr;
    void (*func)(ppu_context*);
    const char* name;
} func_entry;

extern const func_entry function_table[];
extern const uint64_t   function_table_count;

#ifdef __cplusplus
}
#endif
