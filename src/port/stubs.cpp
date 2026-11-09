/*
 * ps3recomp game project -- game-specific hooks and overrides.
 *
 * main.cpp is the runner and is game-agnostic. This file is where a port puts
 * everything that is not: the PRX loading hook it must answer, overrides for
 * firmware functions the HLE library gets wrong for this title, and patches
 * over recompiled code.
 *
 * It starts out as the empty version of all of that, which is what a game with
 * no lifted PRX and no overrides needs.
 */

#include <cstdio>
#include <cstring>

#include "ppu_recomp.h"
#include "ps3emu/nid.h"
#include "ps3emu/ps3types.h"
#include "ps3emu/error_codes.h"
#include "../../tools/ps3recomp/libs/spurs/cellSpurs.h"
#include "win32_compat.h"

uint32_t vm_read32(uint64_t address);
void vm_write32(uint64_t address, uint32_t value);
extern "C" void ps3_hle_register_ctx(uint32_t nid, const char* name,
                                      void (*handler)(ppu_context*));
extern "C" void ps3_hle_call(uint32_t nid, ppu_context* ctx);
extern "C" int32_t _cellSpursLFQueueInitialize(uint64_t, uint64_t, uint64_t,
                                               uint32_t, uint32_t, uint32_t);
extern "C" void i2_register_lfqueue(void);

static void i2_lfqueue_initialize(ppu_context* ctx)
{
    uint32_t queue = (uint32_t)ctx->gpr[4];
    uint32_t buffer = (uint32_t)ctx->gpr[5];
    uint32_t size = (uint32_t)ctx->gpr[6], depth = (uint32_t)ctx->gpr[7];
    uint32_t direction = (uint32_t)ctx->gpr[8];
    int32_t invalid = 0;
    if (!queue || !buffer) invalid = CELL_SPURS_TASK_ERROR_NULL_POINTER;
    else if (ctx->gpr[4] > UINT32_MAX || ctx->gpr[5] > UINT32_MAX ||
             uint64_t(buffer) + uint64_t(size) * depth > 0x100000000ull)
        invalid = CELL_SPURS_TASK_ERROR_FAULT;
    else if ((queue & 127) || (buffer & 15)) invalid = CELL_SPURS_TASK_ERROR_ALIGN;
    else if (ctx->gpr[8] != 3) invalid = CELL_SPURS_TASK_ERROR_NOSYS;
    else if (!size || (size & 15) || size > 16384 || !depth || depth > 32767 ||
             ctx->gpr[6] > UINT32_MAX || ctx->gpr[7] > UINT32_MAX ||
             (uint64_t(buffer) < uint64_t(queue) + 128 &&
              uint64_t(buffer) + uint64_t(size) * depth > queue))
        invalid = CELL_SPURS_TASK_ERROR_INVAL;
    if (invalid) { ctx->gpr[3] = (uint64_t)(int64_t)invalid; return; }
    int32_t result = _cellSpursLFQueueInitialize(ctx->gpr[3], ctx->gpr[4],
        ctx->gpr[5], (uint32_t)ctx->gpr[6], (uint32_t)ctx->gpr[7], direction);
    if (result == 0) {
        /* The title's consumer (LS A29C/ACC8) reads depth at +14 and
         * direction at +24. +0C/+0E are producer control/reservation,
         * initially empty. Initialization precedes exposing the queue. */
        vm_write32(queue + 0x0C, 0);
        vm_write32(queue + 0x24, direction);
        fprintf(stderr, "[i2-lfqueue] initialized q=0x%08X direction=%u\n",
                queue, direction);
    }
    ctx->gpr[3] = (uint64_t)(int64_t)result;
}

extern "C" void i2_register_lwcond(void);

/* ---------------------------------------------------------------------------
 * PRX loading
 *
 * main.cpp calls this once, after the lifted function table is registered and
 * vm_base is live, before the game runs. A title that lifts a real system PRX
 * -- libsre, which is cellSpurs and cellSync -- loads its image into guest RAM
 * here and registers its exports. Most ports never need to: the HLE library
 * answers those modules instead.
 * -----------------------------------------------------------------------*/

extern "C" void ps3_load_prx_modules(void)
{
    /* Context handlers are searched in registration order; install these
     * title-specific ABI fixes before ppu_sysprx_register adds the defaults. */
    i2_register_lwcond();
    ps3_hle_register_ctx(0x011EE38Bu, "i2_cellSpursLFQueueInitialize",
                         i2_lfqueue_initialize);
    i2_register_lfqueue();
}

/* ---------------------------------------------------------------------------
 * Example: Override a specific NID
 *
 * The module system resolves function imports by NID.  To replace a default
 * HLE stub with custom logic, register your function at init time.
 * -----------------------------------------------------------------------*/

/*
 * Example: override cellGcmSetFlipMode (NID 0xA53D12AE) to force vsync.
 *
 *   static void my_cellGcmSetFlipMode(u32 mode)
 *   {
 *       printf("[hook] cellGcmSetFlipMode: forcing VSYNC (was %u)\n", mode);
 *       // Force vsync regardless of what the game requested
 *       cellGcmSetFlipMode(CELL_GCM_DISPLAY_VSYNC);
 *   }
 *
 * Then in your init code:
 *
 *   ps3::modules::override_nid(0xA53D12AE,
 *       reinterpret_cast<void*>(my_cellGcmSetFlipMode));
 */

/* ---------------------------------------------------------------------------
 * Example: Hook into module loading
 *
 * You can intercept cellSysmoduleLoadModule to perform custom actions
 * when a particular module is loaded.
 * -----------------------------------------------------------------------*/

/*
 *   static s32 my_loadmodule_hook(u16 id)
 *   {
 *       printf("[hook] Module 0x%04X loaded\n", id);
 *
 *       if (id == CELL_SYSMODULE_AUDIO) {
 *           // Perform custom audio backend initialization
 *           init_my_audio_backend();
 *       }
 *
 *       // Call the original implementation
 *       return cellSysmoduleLoadModule(id);
 *   }
 */

/* ---------------------------------------------------------------------------
 * Game-specific patches
 *
 * Sometimes you need to patch recompiled code at specific addresses.
 * Use the patch system to modify behavior without editing generated code.
 * -----------------------------------------------------------------------*/

/*
 *   // Skip an integrity check at guest address 0x00812FA0
 *   ps3::patches::nop_range(0x00812FA0, 0x00812FB0);
 *
 *   // Replace a function call with a direct return
 *   ps3::patches::force_return(0x009A1000, 0);  // return 0
 */
