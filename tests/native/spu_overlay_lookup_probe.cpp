/* The interpreter's "is this LSA lifted?" probe must resolve an address the
 * same way dispatch does: code streamed over an LS range (a resident overlay)
 * or a co-resident task's code owns that range, so the base image's stale lift
 * at the same address must lose. Without that order an interrupted WWS job,
 * resumed through the register-save stub the job manager runs, did not match
 * its own lift and the rest of the job ran on the interpreter. */
#include "ppu_recomp.h"
#include "spu_context.h"
#include "spu_interp.h"
#include <cstdio>
#include <cstring>

extern "C" {
void spu_register_function(uint32_t addr, void (*fn)(spu_context*));
void spu_begin_image(int image_id);
void spu_overlay_register_sig(const uint8_t sig[16], int image_id);
}

static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

static unsigned hits[3];
static void fn_base(spu_context*) { ++hits[0]; }
static void fn_task(spu_context*) { ++hits[1]; }
static void fn_overlay(spu_context*) { ++hits[2]; }

static int failures;

static void expect(const char* what, spu_lifted_fn got, void (*want)(spu_context*))
{
    if ((void*)got == (void*)want) {
        std::printf("[ok]   %s -> %s\n", what, want == fn_base ? "base" :
                    want == fn_task ? "task" : want == fn_overlay ? "overlay" : "none");
        return;
    }
    ++failures;
    std::printf("[FAIL] %s -> %p (want %s)\n", what, (void*)got,
                want == fn_base ? "base" : want == fn_task ? "task" :
                want == fn_overlay ? "overlay" : "none");
}

int main(int argc, char** argv)
{
    const char* mode = argc > 1 ? argv[1] : "resident-code";
    if (!std::strncmp(mode, "full-", 5)) {
        /* Execute the full resolver, not just its ownership lookup. The two
         * WWS fallback paths must not replace a known, unlifted code owner. */
        static spu_context ctx;
        spu_context_init(&ctx, 0);
        ctx.image_id = !std::strcmp(mode, "full-wws2-owner-unlifted") ||
                       !std::strcmp(mode, "full-wws-no-owner") ? 2 : 1;
        if (!std::strcmp(mode, "full-non-wws-owner-unlifted")) ctx.image_id = 71;
        ctx.policy_mode = 1;
        ctx.pc = 0x4000;
        spu_ls_write32(&ctx, 0x4000, 0x40200000); /* synthetic NOP */
        spu_ls_write32(&ctx, 0x4004, 0x00000002); /* bounded interpreter STOP */
        spu_begin_image(ctx.image_id == 71 ? 71 : 1);
        spu_register_function(0x4000, fn_base);
        const bool no_owner = !std::strcmp(mode, "full-wws-no-owner");
        const bool owner_lifted = !std::strcmp(mode, "full-owner-lifted");
        if (!no_owner) {
            ctx.resident_code[0].image_id = 93;
            ctx.resident_code[0].lsa = 0x4000;
            ctx.resident_code[0].size = 0x100;
        }
        if (owner_lifted) {
            spu_begin_image(93); spu_register_function(0x4000, fn_task);
        }
        if (!std::strcmp(mode, "full-wws-signature-owner-unlifted")) {
            spu_begin_image(94); spu_register_function(0x4000, fn_overlay);
            spu_overlay_register_sig(&ctx.ls[0x4000], 94);
            spu_ls_write32(&ctx, 0x1320, 0x4000); /* WWS code-buffer pointer */
        }
        expect("canonical owner before dispatch", spu_lifted_lookup(&ctx, ctx.pc),
               owner_lifted ? fn_task : nullptr);
        /* Image 2 has no base lift: the owner-free control intentionally uses
         * the legacy WWS image-1 fallback rather than the canonical lookup. */
        spu_indirect_branch(&ctx);
        const unsigned want_base = no_owner ? 1u : 0u;
        const unsigned want_task = owner_lifted ? 1u : 0u;
        const bool interpreted = !no_owner && !owner_lifted;
        if (hits[0] != want_base || hits[1] != want_task || hits[2] != 0 ||
            (interpreted && (ctx.pc != 0x4008 ||
                            !(ctx.status & SPU_STATUS_STOPPED_BY_STOP))) ||
            ctx.resident_ovl != 0) {
            ++failures;
            std::printf("[FAIL] full resolver base/task/overlay=%u/%u/%u pc=%05X "
                        "status=%X resident_ovl=%d\n", hits[0], hits[1], hits[2],
                        ctx.pc, ctx.status, ctx.resident_ovl);
        }
        std::printf("%s: %d failure(s)\n", mode, failures);
        return failures ? 1 : 0;
    }
    spu_begin_image(71); spu_register_function(0x4000, fn_base);   /* base image */
    spu_begin_image(91); spu_register_function(0x4000, fn_task);   /* job code, same LS */
    spu_begin_image(92); spu_register_function(0x5000, fn_overlay);/* streamed overlay */
    spu_begin_image(0);

    spu_context ctx;
    std::memset(&ctx, 0, sizeof ctx);
    ctx.image_id = 71;

    if (!std::strcmp(mode, "resident-code")) {
        /* A WWS job-code module is resident over the base image's bytes. */
        ctx.resident_code[0].image_id = 91;
        ctx.resident_code[0].lsa = 0x4000;
        ctx.resident_code[0].size = 0x100;
        expect("resident code span", spu_lifted_lookup(&ctx, 0x4000), fn_task);
    } else if (!std::strcmp(mode, "resident-ovl")) {
        ctx.resident_ovl = 92;
        expect("resident overlay", spu_lifted_lookup(&ctx, 0x5000), fn_overlay);
    } else if (!std::strcmp(mode, "ownership")) {
        /* Both images registered at 0x4000: the resident one must win. */
        ctx.resident_code[0].image_id = 91;
        ctx.resident_code[0].lsa = 0x4000;
        ctx.resident_code[0].size = 0x100;
        expect("ownership order", spu_lifted_lookup(&ctx, 0x4000), fn_task);
    } else if (!std::strcmp(mode, "owner-unlifted")) {
        /* The newest bytes belong to an image with no lift at this address.
         * Another resident span, task and base all have stale translated code. */
        ctx.resident_code[0].image_id = 93;
        ctx.resident_code[0].lsa = 0x4000;
        ctx.resident_code[0].size = 0x100;
        ctx.resident_code[1].image_id = 91;
        ctx.resident_code[1].lsa = 0x4000;
        ctx.resident_code[1].size = 0x100;
        ctx.resident_task = 91;
        ctx.resident_ovl = 92;
        expect("unlifted owner must stay interpreted", spu_lifted_lookup(&ctx, 0x4000), nullptr);
    } else if (!std::strcmp(mode, "resident-task")) {
        ctx.resident_task = 91;
        expect("resident task", spu_lifted_lookup(&ctx, 0x4000), fn_task);
    } else if (!std::strcmp(mode, "base")) {
        expect("base image", spu_lifted_lookup(&ctx, 0x4000), fn_base);
        expect("unlifted", spu_lifted_lookup(&ctx, 0x7770), nullptr);
    } else if (!std::strcmp(mode, "null-context")) {
        expect("no context (any image)", spu_lifted_lookup(nullptr, 0x5000), fn_overlay);
    } else {
        std::printf("[FAIL] unknown mode %s\n", mode);
        return 1;
    }

    std::printf("%s: %d failure(s)\n", mode, failures);
    return failures ? 1 : 0;
}
