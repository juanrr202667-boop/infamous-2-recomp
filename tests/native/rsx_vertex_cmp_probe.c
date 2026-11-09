/* Synthetic guest bytes exercise the fetch path used by D3D12. */
#include "rsx_vertex_fetch.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static u8 memory[0x4000];
u8* vm_base = memory;
u32 cellGcmResolveLocated(int local, u32 offset)
{
    return (local ? 0x1000u : 0x2000u) + offset;
}
u32 cellGcmResolveOffset(u32 offset) { return 0x2000u + offset; }

static int check(const char* name, const float actual[4], const float expected[4])
{
    for (int i = 0; i < 4; ++i) {
        if (!isfinite(actual[i]) || fabsf(actual[i] - expected[i]) > 0.000002f) {
            fprintf(stderr, "%s component %d: got %.9g, expected %.9g\n",
                    name, i, actual[i], expected[i]);
            return 1;
        }
    }
    return 0;
}

int main(void)
{
    rsx_state state;
    memset(&state, 0, sizeof state);
    state.vertex_data4f[10][0] = 7.0f;
    state.vertex_data4f[10][1] = 8.0f;
    state.vertex_data4f[10][2] = 9.0f;
    state.vertex_data4f[10][3] = 10.0f;
    rsx_vertex_attrib* a = &state.vertex_attribs[10];
    a->enabled = 1; a->type = 6; a->size = 1; a->stride = 4;
    /* CMP is one BE word: signed 11/11/10, expanded to signed 16-bit lanes.
     * Literals cover each sign bit and packing boundary independently. */
    const struct { u8 bytes[4]; float want[4]; } cases[] = {
        {{0x00,0x00,0x00,0x00}, {0,0,0,1}},
        {{0x7F,0xDF,0xFB,0xFF}, {32736.0f/32767,32736.0f/32767,32704.0f/32767,1}},
        {{0x80,0x60,0x0C,0x01}, {-32736.0f/32767,-32736.0f/32767,-32704.0f/32767,1}},
        {{0x80,0x20,0x04,0x00}, {-32768.0f/32767,-32768.0f/32767,-32768.0f/32767,1}},
        {{0x00,0x7F,0xF8,0x01}, {32.0f/32767,-32.0f/32767,64.0f/32767,1}},
    };
    float out[4];
    int failures = 0;
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
        memcpy(memory + 0x1000 + i * 4, cases[i].bytes, 4);
        rsx_fetch_attrib(&state, 10, i, out);
        failures += check("CMP local indexed", out, cases[i].want);
    }
    a->offset = 0x80000020u;
    a->stride = 8;
    a->frequency = 2;
    memcpy(memory + 0x2028, cases[4].bytes, 4);
    rsx_fetch_attrib(&state, 10, 3, out); /* floor(3/2), MAIN, stride 8 */
    failures += check("CMP main frequency", out, cases[4].want);
    a->enabled = 0;
    rsx_fetch_attrib(&state, 10, 3, out);
    failures += check("disabled constant", out, state.vertex_data4f[10]);
    if (!failures) puts("CMP packed fetch: PASS (signs, BE, size=1, stride, MAIN, divisor, disabled)");
    return failures ? 1 : 0;
}
