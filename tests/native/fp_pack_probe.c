/* Pack/unpack fragment opcodes must produce the sRGB conversion helpers.
 * The encoding follows ps3recomp's own test_fp_decompiler.c. */
#include "rsx_fp_decompiler.h"
#include <stdio.h>
#include <string.h>

static int failures;

static void put_word(unsigned char* p, unsigned int h)
{
    unsigned int be = (h << 16) | (h >> 16);
    p[0] = (unsigned char)(be >> 24); p[1] = (unsigned char)(be >> 16);
    p[2] = (unsigned char)(be >> 8);  p[3] = (unsigned char)be;
}

#define OPC(x)      ((unsigned)(x) << 24)
#define OUTMASK_ALL (0xFu << 9)
#define END         (1u << 0)
#define INSRC(x)    ((unsigned)(x) << 13)
#define T_TEMP      0u
#define SWZ_IDENT   ((0u<<9)|(1u<<11)|(2u<<13)|(3u<<15))
#define REG(i)      ((unsigned)(i) << 2)
#define EXEC_ALWAYS ((7u << 18) | (0u << 21) | (1u << 23) | (2u << 25) | (3u << 27))

static void expect(const char* name, unsigned opcode, const char* call,
                   const char* helper)
{
    unsigned char program[16];
    char hlsl[65536];
    put_word(program + 0, OPC(opcode) | OUTMASK_ALL | END);
    put_word(program + 4, T_TEMP | REG(1) | SWZ_IDENT | EXEC_ALWAYS);
    put_word(program + 8, 0);
    put_word(program + 12, 0);
    int count = rsx_fp_decompile(program, sizeof(program), 0x40u, hlsl, sizeof(hlsl));
    if (count <= 0) {
        printf("FAIL %s: decompile returned %d\n", name, count);
        failures++;
        return;
    }
    if (!strstr(hlsl, call)) {
        printf("FAIL %s: missing call %s\n", name, call);
        failures++;
    } else if (!strstr(hlsl, helper)) {
        printf("FAIL %s: missing helper %s\n", name, helper);
        failures++;
    } else {
        printf("PASS %s\n", name);
    }
}

int main(void)
{
    /* PK4UB(0x27), UP4UB(0x28), PKG(0x2C), UPG(0x2D). */
    expect("pk4ub", 0x27, "_rsx_pk4ub(", "uint _rsx_pk4ub");
    expect("up4ub", 0x28, "_rsx_up4ub(", "float4 _rsx_up4ub");
    expect("pkg", 0x2C, "_rsx_pk4ub(_rsx_lin2srgb(", "float4 _rsx_lin2srgb");
    expect("upg", 0x2D, "_rsx_srgb2lin(_rsx_up4ub(", "float4 _rsx_srgb2lin");
    return failures ? 1 : 0;
}
