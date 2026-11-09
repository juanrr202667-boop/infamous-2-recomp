/* Behavioral fixture for the witnessed movie immediate-vertex stream. */
#include "rsx_commands.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

uint32_t ppu_hle_inject_base = 0x03000000u;
static rsx_state state;
static struct {
    unsigned arrays, indexed, count, primitive, guest_writes;
    float vertices[256][16][4];
} capture;

/* The real dispatcher owns decoding, emission and snapshots. The capture
 * backend replaces GPU submission only, so the fixture needs no device. */
void ps3_ms(const char* key) { (void)key; }
void vm_write32(uint32_t addr, uint32_t value) {
    (void)addr; (void)value; capture.guest_writes++;
}
static void arrays(void* user, u32 primitive, u32 first, u32 count) {
    (void)user;
    capture.arrays++;
    capture.count = count;
    capture.primitive = primitive;
#ifdef RSX_MAX_IMMEDIATE_VERTICES
    if (state.immediate_active && count <= 256)
        memcpy(capture.vertices, state.immediate_vertices[first], count * sizeof(capture.vertices[0]));
#else
    (void)first;
#endif
}
static void indexed(void* user, u32 primitive, u32 first, u32 count) {
    (void)user; (void)primitive; (void)first; (void)count;
    capture.indexed++;
}
static void reset(void) {
    static rsx_backend backend;
    memset(&capture, 0, sizeof(capture));
    rsx_state_init(&state);
    memset(&backend, 0, sizeof(backend));
    backend.draw_arrays = arrays;
    backend.draw_indexed = indexed;
    rsx_set_backend(&backend);
}
static int check(int ok, const char* message) {
    if (!ok) fprintf(stderr, "FAIL: %s\n", message);
    return ok;
}
static void pair(u32 base, float x, float y) {
    u32 a,b; memcpy(&a,&x,4); memcpy(&b,&y,4);
    rsx_process_method(&state, base, a);
    rsx_process_method(&state, base+4, b);
}
static void vertex(float x, float y, float u, float v) {
    pair(0x1888,u,v);
    pair(0x1880,x,y);
}
static void movie_stream(void) {
    rsx_process_method(&state, 0x1808, 6);
    rsx_process_method(&state, 0x1948, 0xffffffffu);
    vertex( 1, 1,1,1);
    vertex(-1, 1,0,1);
    vertex( 1,-1,1,0);
    vertex(-1,-1,0,0);
    rsx_process_method(&state, 0x1808, 0);
}
static int movie(void) {
    const float xy[4][2]={{1,1},{-1,1},{1,-1},{-1,-1}};
    const float uv[4][2]={{1,1},{0,1},{1,0},{0,0}};
    reset(); movie_stream();
    if (!check(capture.arrays==1 && capture.count==4 && capture.primitive==6,
               "movie BEGIN/2F/END must emit one four-vertex triangle strip")) return 1;
    for (unsigned i=0;i<4;i++) {
        if (!check(capture.vertices[i][0][0]==xy[i][0] && capture.vertices[i][0][1]==xy[i][1],
                   "each position must survive until backend capture")) return 1;
        if (!check(capture.vertices[i][1][0]==uv[i][0] && capture.vertices[i][1][1]==uv[i][1],
                   "each position must snapshot its preceding texcoord")) return 1;
        for (unsigned attr=0;attr<2;attr++)
            if (!check(capture.vertices[i][attr][2]==0 && capture.vertices[i][attr][3]==1,
                       "2F values need implicit z=0 and w=1")) return 1;
        for (unsigned lane=0;lane<4;lane++)
            if (!check(capture.vertices[i][2][lane]==1,"packed white must be snapshotted with each vertex")) return 1;
    }
    return !check(!capture.guest_writes,"immediate draw must not allocate or write guest scratch");
}
static int constants(void) {
    reset();
    pair(0x1880,2,3); pair(0x1888,4,5);
    rsx_process_method(&state,0x1948,0x80402010u);
    if (!check(!capture.arrays && !capture.indexed,"outside BEGIN must not draw")) return 1;
    if (!check(state.vertex_data4f[0][0]==2 && state.vertex_data4f[0][1]==3 &&
               state.vertex_data4f[0][2]==0 && state.vertex_data4f[0][3]==1,
               "outside BEGIN must update current position with 2F defaults")) return 1;
    const float rgba[4]={16.f/255.f,32.f/255.f,64.f/255.f,128.f/255.f};
    for(unsigned i=0;i<4;i++)
        if (!check(fabsf(state.vertex_data4f[2][i]-rgba[i])<1e-7f,"4UB must normalize low-byte-first components")) return 1;
    return 0;
}
static int terminal(void) {
    reset();
    rsx_process_method(&state,0x1808,6);
    rsx_process_method(&state,0x1880,0x3f800000u);
    rsx_process_method(&state,0x1808,0);
    if (!check(!capture.arrays,"attr0 x alone must not emit a vertex")) return 1;
    movie_stream();
    if (!check(capture.arrays==1 && capture.count==4,"new BEGIN must discard previous incomplete vertex")) return 1;
    rsx_process_method(&state,0x1808,0);
    return !check(capture.arrays==1,"repeated END must not duplicate immediate draw");
}
static int normal_arrays(int use_index) {
    reset();
    rsx_process_method(&state,0x1808,5);
    vertex(1,1,0,0);
    rsx_process_method(&state,use_index ? 0x1824 : 0x1814,0x02000000u);
    rsx_process_method(&state,0x1808,0);
    return !check(use_index ? capture.indexed==1 && capture.arrays==0 : capture.arrays==1 && capture.indexed==0,
                  "explicit array draw with current attributes must not also emit immediate draw at END");
}
static int inline_marker(void) {
    reset();
    rsx_process_method(&state,0x1808,6);
    vertex(1,1,0,0);
    const int status=rsx_process_method(&state,0x1818,0x3f800000u);
    rsx_process_method(&state,0x1808,0);
    if (!check(status==-1,"unsupported INLINE_ARRAY must retain unsupported status")) return 1;
    return !check(!capture.arrays && !capture.indexed,
                  "INLINE_ARRAY must suppress synthetic immediate draw at END");
}
static int overflow(void) {
    reset();
    rsx_process_method(&state,0x1808,6);
    for(unsigned i=0;i<257;i++) vertex(1,1,0,0);
    rsx_process_method(&state,0x1808,0);
    if (!check(!capture.arrays,"overflow must reject whole primitive rather than truncate")) return 1;
    movie_stream();
    return !check(capture.arrays==1 && capture.count==4,"overflow must not poison next BEGIN");
}
int main(int argc,char** argv) {
    const char* mode=argc>1?argv[1]:"movie";
    if(!strcmp(mode,"movie")) return movie();
    if(!strcmp(mode,"constants")) return constants();
    if(!strcmp(mode,"terminal")) return terminal();
    if(!strcmp(mode,"arrays")) return normal_arrays(0);
    if(!strcmp(mode,"indexed")) return normal_arrays(1);
    if(!strcmp(mode,"overflow")) return overflow();
    if(!strcmp(mode,"inline")) return inline_marker();
    return 2;
}
