/* Production helper; allocation and command COM boundaries stay on CPU. */
#define CINTERFACE
#include <windows.h>
#include <d3d12.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if __has_include("rsx_d3d12_display_bridge.h")
#include "rsx_d3d12_display_bridge.h"

#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)

typedef struct fake_resource {
    ID3D12Resource api;
    D3D12_RESOURCE_DESC desc;
    D3D12_RESOURCE_STATES physical_state;
    uint8_t pixels[4][4];
} fake_resource;

typedef struct event {
    char kind;
    ID3D12Resource* resource;
    D3D12_RESOURCE_STATES before, after;
} event;

static fake_resource canonical_store, display_store;
static event events[64];
static unsigned event_count, copies, bad, ownership_calls, device_calls, descriptions;
static ID3D12ResourceVtbl resource_table;
static ID3D12GraphicsCommandListVtbl command_table;
static ID3D12GraphicsCommandList command;

static ULONG STDMETHODCALLTYPE ownership_call(ID3D12Resource* resource)
{
    (void)resource;
    ++ownership_calls;
    return 1;
}

static HRESULT STDMETHODCALLTYPE get_device(ID3D12Resource* resource, REFIID iid, void** output)
{
    (void)resource; (void)iid;
    ++device_calls;
    *output = NULL;
    return E_NOTIMPL;
}

static D3D12_RESOURCE_DESC* STDMETHODCALLTYPE get_description(ID3D12Resource* resource,
        D3D12_RESOURCE_DESC* output)
{
    ++descriptions;
    *output = ((fake_resource*)resource)->desc;
    return output;
}

static void STDMETHODCALLTYPE barrier(ID3D12GraphicsCommandList* list,
        UINT count, const D3D12_RESOURCE_BARRIER* barriers)
{
    if (list != &command) ++bad;
    for (UINT i = 0; i < count; ++i) {
        const D3D12_RESOURCE_BARRIER* b = &barriers[i];
        if (event_count >= 64 || b->Type != D3D12_RESOURCE_BARRIER_TYPE_TRANSITION ||
                b->Flags != D3D12_RESOURCE_BARRIER_FLAG_NONE ||
                b->Transition.Subresource != D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES ||
                (b->Transition.pResource != &canonical_store.api &&
                 b->Transition.pResource != &display_store.api) ||
                b->Transition.StateBefore == b->Transition.StateAfter) {
            ++bad;
            return;
        }
        fake_resource* resource = (fake_resource*)b->Transition.pResource;
        if (resource->physical_state != b->Transition.StateBefore) ++bad;
        resource->physical_state = b->Transition.StateAfter;
        event recorded = {'B', b->Transition.pResource,
                          b->Transition.StateBefore, b->Transition.StateAfter};
        events[event_count++] = recorded;
    }
}

static void STDMETHODCALLTYPE copy_resource(ID3D12GraphicsCommandList* list,
        ID3D12Resource* destination, ID3D12Resource* source)
{
    fake_resource* dst = (fake_resource*)destination;
    fake_resource* src = (fake_resource*)source;
    if (list != &command || event_count >= 64 || !dst || !src || dst == src ||
            dst->physical_state != D3D12_RESOURCE_STATE_COPY_DEST ||
            src->physical_state != D3D12_RESOURCE_STATE_COPY_SOURCE) {
        ++bad;
        return;
    }
    memcpy(dst->pixels, src->pixels, sizeof(dst->pixels));
    event recorded = {'C', destination, 0, 0};
    events[event_count++] = recorded;
    ++copies;
}

static void setup(rsx_d3d12_display_bridge_context* context,
        rsx_d3d12_linear_resource* canonical, D3D12_RESOURCE_STATES* state)
{
    resource_table.AddRef = ownership_call;
    resource_table.Release = ownership_call;
    resource_table.GetDevice = get_device;
    resource_table.GetDesc = get_description;
    canonical_store.api.lpVtbl = display_store.api.lpVtbl = &resource_table;
    canonical_store.desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    canonical_store.desc.Width = canonical_store.desc.Height = 2;
    canonical_store.desc.DepthOrArraySize = canonical_store.desc.MipLevels = 1;
    canonical_store.desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    canonical_store.desc.SampleDesc.Count = 1;
    canonical_store.desc.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
    canonical_store.desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
    display_store.desc = canonical_store.desc;
    canonical_store.physical_state = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
    display_store.physical_state = D3D12_RESOURCE_STATE_RENDER_TARGET;
    const uint8_t original[4][4] = {{10,20,30,40}, {50,60,70,80},
                                  {90,100,110,120}, {130,140,150,160}};
    memcpy(canonical_store.pixels, original, sizeof(original));
    memset(display_store.pixels, 0xCD, sizeof(display_store.pixels));
    command_table.ResourceBarrier = barrier;
    command_table.CopyResource = copy_resource;
    command.lpVtbl = &command_table;
    context->command_list = &command;
    context->display = &display_store.api;
    context->width = context->height = 2;
    context->format = DXGI_FORMAT_R8G8B8A8_UNORM;
    *state = canonical_store.physical_state;
    canonical->resource = &canonical_store.api;
    canonical->state = state;
    rsx_linear_view view = {0x3B0000, 2, 2, 8, 4, 0x85, 1};
    canonical->view = view;
    canonical->format = DXGI_FORMAT_R8G8B8A8_UNORM;
}

static int rejected(rsx_d3d12_display_bridge_context* context,
        const rsx_d3d12_linear_resource* canonical, int end)
{
    unsigned previous_events = event_count, previous_copies = copies;
    D3D12_RESOURCE_STATES previous_state = canonical_store.physical_state;
    CHECK(!(end ? rsx_d3d12_display_bridge_end(context, canonical) :
                  rsx_d3d12_display_bridge_begin(context, canonical)));
    CHECK(event_count == previous_events && copies == previous_copies);
    CHECK(canonical_store.physical_state == previous_state &&
          display_store.physical_state == D3D12_RESOURCE_STATE_RENDER_TARGET);
    CHECK(context->last_hr == E_INVALIDARG && context->last_error && *context->last_error);
    return 0;
}

static int rejected_both(rsx_d3d12_display_bridge_context* context,
        const rsx_d3d12_linear_resource* canonical)
{
    CHECK(!rejected(context, canonical, 0));
    CHECK(!rejected(context, canonical, 1));
    return 0;
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    rsx_d3d12_display_bridge_context context = {0};
    rsx_d3d12_linear_resource canonical = {0};
    D3D12_RESOURCE_STATES state;
    setup(&context, &canonical, &state);
    if (!strcmp(argv[1], "partial-draws")) {
        CHECK(rsx_d3d12_display_bridge_begin(&context, &canonical));
        const uint8_t original[4][4] = {{10,20,30,40}, {50,60,70,80},
                                      {90,100,110,120}, {130,140,150,160}};
        CHECK(!memcmp(display_store.pixels, original, sizeof(original)));
        CHECK(state == D3D12_RESOURCE_STATE_COPY_SOURCE &&
              display_store.physical_state == D3D12_RESOURCE_STATE_RENDER_TARGET);
        /* Controlled draw writes green on two pixels only. Copies and tracked
         * states come from the production bridge, not from this draw fixture. */
        display_store.pixels[0][1] = 99;
        display_store.pixels[1][1] = 109;
        CHECK(rsx_d3d12_display_bridge_end(&context, &canonical));
        const uint8_t first[4][4] = {{10,99,30,40}, {50,109,70,80},
                                   {90,100,110,120}, {130,140,150,160}};
        CHECK(!memcmp(canonical_store.pixels, first, sizeof(first)));
        CHECK(state == D3D12_RESOURCE_STATE_COPY_DEST &&
              display_store.physical_state == D3D12_RESOURCE_STATE_RENDER_TARGET);
        memset(display_store.pixels, 0xCD, sizeof(display_store.pixels));
        CHECK(rsx_d3d12_display_bridge_begin(&context, &canonical));
        CHECK(!memcmp(display_store.pixels, first, sizeof(first)));
        display_store.pixels[3][3] = 7;
        CHECK(rsx_d3d12_display_bridge_end(&context, &canonical));
        const uint8_t second[4][4] = {{10,99,30,40}, {50,109,70,80},
                                    {90,100,110,120}, {130,140,150,7}};
        CHECK(!memcmp(canonical_store.pixels, second, sizeof(second)));
        CHECK(copies == 4 && !bad);
    } else if (!strcmp(argv[1], "states")) {
        CHECK(rsx_d3d12_display_bridge_begin(&context, &canonical));
        CHECK(event_count == 4 && events[2].kind == 'C' && events[2].resource == context.display);
        CHECK(events[0].kind == 'B' && events[1].kind == 'B');
        CHECK(events[3].kind == 'B' && events[3].resource == context.display &&
              events[3].before == D3D12_RESOURCE_STATE_COPY_DEST &&
              events[3].after == D3D12_RESOURCE_STATE_RENDER_TARGET);
        CHECK(state == D3D12_RESOURCE_STATE_COPY_SOURCE &&
              canonical_store.physical_state == state);
        CHECK(rsx_d3d12_display_bridge_end(&context, &canonical));
        CHECK(event_count == 8 && events[6].kind == 'C' && events[6].resource == canonical.resource);
        CHECK(events[7].kind == 'B' && events[7].resource == context.display &&
              events[7].before == D3D12_RESOURCE_STATE_COPY_SOURCE &&
              events[7].after == D3D12_RESOURCE_STATE_RENDER_TARGET);
        CHECK(state == D3D12_RESOURCE_STATE_COPY_DEST && canonical_store.physical_state == state && !bad);
    } else if (!strcmp(argv[1], "copy-states")) {
        state = canonical_store.physical_state = D3D12_RESOURCE_STATE_COPY_SOURCE;
        CHECK(rsx_d3d12_display_bridge_begin(&context, &canonical));
        CHECK(event_count == 3 && events[0].resource == context.display &&
              events[1].kind == 'C' && state == D3D12_RESOURCE_STATE_COPY_SOURCE);
        state = canonical_store.physical_state = D3D12_RESOURCE_STATE_COPY_DEST;
        CHECK(rsx_d3d12_display_bridge_end(&context, &canonical));
        CHECK(event_count == 6 && events[3].resource == context.display &&
              events[4].kind == 'C' && state == D3D12_RESOURCE_STATE_COPY_DEST && !bad);
    } else if (!strcmp(argv[1], "invalid")) {
        CHECK(!rsx_d3d12_display_bridge_begin(NULL, &canonical));
        CHECK(!rsx_d3d12_display_bridge_end(NULL, &canonical));
        CHECK(!rejected_both(&context, NULL));
        rsx_d3d12_display_bridge_context changed_context = context;
        changed_context.command_list = NULL; CHECK(!rejected_both(&changed_context, &canonical));
        changed_context = context; changed_context.display = NULL; CHECK(!rejected_both(&changed_context, &canonical));
        changed_context = context; changed_context.width = 0; CHECK(!rejected_both(&changed_context, &canonical));
        changed_context = context; changed_context.height = 0; CHECK(!rejected_both(&changed_context, &canonical));
        changed_context = context; changed_context.format = DXGI_FORMAT_UNKNOWN;
        CHECK(!rejected_both(&changed_context, &canonical));
        changed_context = context; changed_context.width = 1; CHECK(!rejected_both(&changed_context, &canonical));
        changed_context = context; changed_context.height = 1; CHECK(!rejected_both(&changed_context, &canonical));
        rsx_d3d12_linear_resource changed = canonical;
        changed.resource = NULL; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.resource = context.display; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.state = NULL; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.view.width = 0; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.view.height = 0; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.view.pitch = 4; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.view.format_id = 0x86; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.view.bytes_per_pixel = 8; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.view.location = 0; CHECK(!rejected_both(&context, &changed));
        changed = canonical; changed.format = DXGI_FORMAT_R32_FLOAT; CHECK(!rejected_both(&context, &changed));
        CHECK(!event_count && !copies && state == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
        CHECK(rsx_d3d12_display_bridge_begin(&context, &canonical));
        CHECK(context.last_hr == S_OK && !context.last_error && !bad);
    } else if (!strcmp(argv[1], "descriptions")) {
        const D3D12_RESOURCE_DESC original = canonical_store.desc;
        for (unsigned member = 0; member < 2; ++member) {
            fake_resource* resource = member ? &display_store : &canonical_store;
            resource->desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original; resource->desc.Width = 1;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original; resource->desc.Height = 1;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original; resource->desc.DepthOrArraySize = 2;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original; resource->desc.MipLevels = 2;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original; resource->desc.Format = DXGI_FORMAT_R32_FLOAT;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original; resource->desc.SampleDesc.Count = 4;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original; resource->desc.SampleDesc.Quality = 1;
            CHECK(!rejected_both(&context, &canonical));
            resource->desc = original;
        }
        CHECK(!event_count && !copies && state == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE && !bad);
    } else if (!strcmp(argv[1], "borrowed")) {
        rsx_d3d12_linear_resource previous = canonical;
        CHECK(rsx_d3d12_display_bridge_begin(&context, &canonical));
        CHECK(rsx_d3d12_display_bridge_end(&context, &canonical));
        CHECK(canonical.resource == previous.resource && canonical.state == previous.state &&
              canonical.format == previous.format && !memcmp(&canonical.view, &previous.view, sizeof(canonical.view)));
        CHECK(context.display == &display_store.api && context.command_list == &command &&
              context.width == 2 && context.height == 2 && context.format == DXGI_FORMAT_R8G8B8A8_UNORM);
        CHECK(descriptions >= 4 && !ownership_calls && !device_calls && !bad);
    } else return 2;
    CHECK(!ownership_calls && !device_calls && !bad);
    puts("PASS D3D12 display bridge");
    return 0;
}
#else
int main(void) { puts("FAIL display bridge not implemented"); return 1; }
#endif
