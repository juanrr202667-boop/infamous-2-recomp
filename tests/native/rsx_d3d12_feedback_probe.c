/* Execute the real helper; simulate only allocation and command COM calls. */
#define CINTERFACE
#include <windows.h>
#include <d3d12.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if __has_include("rsx_d3d12_feedback.h")
#include "rsx_d3d12_feedback.h"

const GUID IID_ID3D12Resource = {0};
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)

typedef struct fake_resource {
    ID3D12Resource api;
    D3D12_RESOURCE_DESC desc;
    D3D12_RESOURCE_STATES physical_state;
    uint32_t pixels[2];
    unsigned owned, released;
} fake_resource;

typedef struct event {
    char kind;
    ID3D12Resource* resource;
    D3D12_RESOURCE_STATES before, after;
} event;

static fake_resource source_store[2], snapshots[40];
static ID3D12ResourceVtbl resource_table;
static unsigned creates, releases, bad, fail_create, null_create;
static unsigned copies, event_count;
static event events[64];

static ULONG STDMETHODCALLTYPE release_resource(ID3D12Resource* api)
{
    fake_resource* resource = (fake_resource*)api;
    if (!resource->owned || resource->released++) ++bad;
    ++releases;
    return 0;
}

static D3D12_RESOURCE_DESC* STDMETHODCALLTYPE get_description(ID3D12Resource* api,
        D3D12_RESOURCE_DESC* output)
{
    *output = ((fake_resource*)api)->desc;
    return output;
}

static HRESULT STDMETHODCALLTYPE create_resource(ID3D12Device* device,
        const D3D12_HEAP_PROPERTIES* heap, D3D12_HEAP_FLAGS flags,
        const D3D12_RESOURCE_DESC* desc, D3D12_RESOURCE_STATES initial,
        const D3D12_CLEAR_VALUE* clear, REFIID iid, void** output)
{
    (void)device;
    ++creates;
    *output = NULL;
    if (creates == fail_create) return E_OUTOFMEMORY;
    if (creates == null_create) return S_OK;
    if (creates > 40) return E_OUTOFMEMORY;
    if (!heap || heap->Type != D3D12_HEAP_TYPE_DEFAULT ||
            heap->CPUPageProperty != D3D12_CPU_PAGE_PROPERTY_UNKNOWN ||
            heap->MemoryPoolPreference != D3D12_MEMORY_POOL_UNKNOWN ||
            heap->CreationNodeMask != 1 || heap->VisibleNodeMask != 1 ||
            flags != D3D12_HEAP_FLAG_NONE || clear || !iid ||
            memcmp(iid, &IID_ID3D12Resource, sizeof(*iid)) ||
            desc->Dimension != D3D12_RESOURCE_DIMENSION_TEXTURE2D || desc->Alignment ||
            desc->Width != 2 || desc->Height != 1 || desc->DepthOrArraySize != 1 ||
            desc->MipLevels != 1 || desc->Format != DXGI_FORMAT_R8G8B8A8_UNORM ||
            desc->SampleDesc.Count != 1 || desc->SampleDesc.Quality ||
            desc->Layout != D3D12_TEXTURE_LAYOUT_UNKNOWN ||
            desc->Flags != D3D12_RESOURCE_FLAG_NONE ||
            initial != D3D12_RESOURCE_STATE_COPY_DEST) ++bad;
    fake_resource* resource = &snapshots[creates - 1];
    resource->api.lpVtbl = &resource_table;
    resource->desc = *desc;
    resource->physical_state = initial;
    resource->owned = 1;
    resource->pixels[0] = resource->pixels[1] = 0xCDCDCDCD;
    *output = &resource->api;
    return S_OK;
}

static void STDMETHODCALLTYPE barrier(ID3D12GraphicsCommandList* command,
        UINT count, const D3D12_RESOURCE_BARRIER* barriers)
{
    (void)command;
    for (UINT i = 0; i < count; ++i) {
        const D3D12_RESOURCE_BARRIER* b = &barriers[i];
        if (event_count >= 64 || b->Type != D3D12_RESOURCE_BARRIER_TYPE_TRANSITION ||
                b->Flags != D3D12_RESOURCE_BARRIER_FLAG_NONE ||
                b->Transition.Subresource != D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES ||
                !b->Transition.pResource || b->Transition.StateBefore == b->Transition.StateAfter) {
            ++bad;
            return;
        }
        fake_resource* resource = (fake_resource*)b->Transition.pResource;
        if (resource->physical_state != b->Transition.StateBefore || resource->released) ++bad;
        resource->physical_state = b->Transition.StateAfter;
        event recorded = {'B', b->Transition.pResource,
                          b->Transition.StateBefore, b->Transition.StateAfter};
        events[event_count++] = recorded;
    }
}

static void STDMETHODCALLTYPE copy_resource(ID3D12GraphicsCommandList* command,
        ID3D12Resource* destination, ID3D12Resource* source)
{
    (void)command;
    fake_resource* dst = (fake_resource*)destination;
    fake_resource* src = (fake_resource*)source;
    if (event_count >= 64 || !dst || !src || dst == src ||
            dst->physical_state != D3D12_RESOURCE_STATE_COPY_DEST ||
            src->physical_state != D3D12_RESOURCE_STATE_COPY_SOURCE ||
            dst->released || src->released ||
            dst->desc.Width != src->desc.Width || dst->desc.Height != src->desc.Height ||
            dst->desc.Format != src->desc.Format) {
        ++bad;
        return;
    }
    memcpy(dst->pixels, src->pixels, sizeof(dst->pixels));
    event recorded = {'C', destination, 0, 0};
    events[event_count++] = recorded;
    ++copies;
}

static void setup(rsx_d3d12_feedback_context* context,
        rsx_d3d12_linear_resource* source, D3D12_RESOURCE_STATES* state)
{
    static ID3D12DeviceVtbl device_table;
    static ID3D12GraphicsCommandListVtbl command_table;
    static ID3D12Device device;
    static ID3D12GraphicsCommandList command;
    device_table.CreateCommittedResource = create_resource;
    command_table.ResourceBarrier = barrier;
    command_table.CopyResource = copy_resource;
    resource_table.Release = release_resource;
    resource_table.GetDesc = get_description;
    device.lpVtbl = &device_table;
    command.lpVtbl = &command_table;
    context->device = &device;
    context->command_list = &command;
    for (unsigned i = 0; i < 2; ++i) {
        source_store[i].api.lpVtbl = &resource_table;
        source_store[i].desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        source_store[i].desc.Width = 2;
        source_store[i].desc.Height = 1;
        source_store[i].desc.DepthOrArraySize = 1;
        source_store[i].desc.MipLevels = 1;
        source_store[i].desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        source_store[i].desc.SampleDesc.Count = 1;
        source_store[i].desc.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
        source_store[i].desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
        source_store[i].physical_state = D3D12_RESOURCE_STATE_RENDER_TARGET;
        source_store[i].pixels[0] = 10;
        source_store[i].pixels[1] = 20;
    }
    *state = D3D12_RESOURCE_STATE_RENDER_TARGET;
    source->resource = &source_store[0].api;
    source->state = state;
    rsx_linear_view view = {0xAB8000, 2, 1, 8, 4, 0x85, 1};
    source->view = view;
    source->format = DXGI_FORMAT_R8G8B8A8_UNORM;
}

/* A draw's shader consumes the snapshot, then writes the real destination.
 * Restore the caller-owned destination state between draws, as replay does. */
static int draw(rsx_d3d12_feedback_context* context, uint32_t id,
        rsx_d3d12_linear_resource* source)
{
    fake_resource* snapshot = (fake_resource*)context->slots[id].resource;
    fake_resource* destination = (fake_resource*)source->resource;
    CHECK(snapshot != destination && snapshot->physical_state == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    CHECK(context->slots[id].state == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    CHECK(*source->state == D3D12_RESOURCE_STATE_COPY_SOURCE);
    *source->state = destination->physical_state = D3D12_RESOURCE_STATE_RENDER_TARGET;
    destination->pixels[0] = snapshot->pixels[0] + 1;
    destination->pixels[1] = snapshot->pixels[1] + 1;
    return 0;
}

static int rejected_without_commands(rsx_d3d12_feedback_context* context,
        uint32_t id, const rsx_d3d12_linear_resource* source, int reserve)
{
    unsigned previous_events = event_count, previous_copies = copies, previous_creates = creates;
    CHECK(!(reserve ? rsx_d3d12_feedback_reserve(context, id, source) :
                      rsx_d3d12_feedback_snapshot(context, id, source)));
    CHECK(event_count == previous_events && copies == previous_copies && creates == previous_creates);
    CHECK(context->last_hr == E_INVALIDARG && context->last_error && *context->last_error);
    return 0;
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    rsx_d3d12_feedback_context context = {0};
    rsx_d3d12_linear_resource source = {0};
    D3D12_RESOURCE_STATES state;
    setup(&context, &source, &state);
    if (!strcmp(argv[1], "refresh")) {
        CHECK(rsx_d3d12_feedback_reserve(&context, 2, &source));
        CHECK(creates == 1 && !event_count && !copies);
        CHECK(rsx_d3d12_feedback_snapshot(&context, 2, &source));
        CHECK(event_count == 3 && copies == 1);
        CHECK(events[0].kind == 'B' && events[0].resource == source.resource &&
              events[0].before == D3D12_RESOURCE_STATE_RENDER_TARGET &&
              events[0].after == D3D12_RESOURCE_STATE_COPY_SOURCE);
        CHECK(events[1].kind == 'C' && events[1].resource == context.slots[2].resource);
        CHECK(events[2].kind == 'B' && events[2].resource == context.slots[2].resource &&
              events[2].before == D3D12_RESOURCE_STATE_COPY_DEST &&
              events[2].after == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
        CHECK(!draw(&context, 2, &source));
        CHECK(source_store[0].pixels[0] == 11 && source_store[0].pixels[1] == 21);
        CHECK(rsx_d3d12_feedback_reserve(&context, 2, &source));
        CHECK(rsx_d3d12_feedback_snapshot(&context, 2, &source));
        CHECK(event_count == 7 && copies == 2 && creates == 1);
        CHECK(events[3].kind == 'B' && events[3].resource == source.resource &&
              events[3].before == D3D12_RESOURCE_STATE_RENDER_TARGET &&
              events[3].after == D3D12_RESOURCE_STATE_COPY_SOURCE);
        CHECK(events[4].kind == 'B' && events[4].resource == context.slots[2].resource &&
              events[4].before == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE &&
              events[4].after == D3D12_RESOURCE_STATE_COPY_DEST);
        CHECK(events[5].kind == 'C' && events[6].kind == 'B');
        CHECK(!draw(&context, 2, &source));
        CHECK(source_store[0].pixels[0] == 12 && source_store[0].pixels[1] == 22);
        CHECK(context.last_hr == S_OK && !context.last_error && !bad);
    } else if (!strcmp(argv[1], "copy-source")) {
        CHECK(rsx_d3d12_feedback_reserve(&context, 0, &source));
        state = source_store[0].physical_state = D3D12_RESOURCE_STATE_COPY_SOURCE;
        CHECK(rsx_d3d12_feedback_snapshot(&context, 0, &source));
        CHECK(event_count == 2 && events[0].kind == 'C' && events[1].kind == 'B');
        CHECK(state == D3D12_RESOURCE_STATE_COPY_SOURCE && !bad);
    } else if (!strcmp(argv[1], "stable")) {
        ID3D12GraphicsCommandList* command = context.command_list;
        context.command_list = NULL;
        CHECK(rsx_d3d12_feedback_reserve(&context, 0, &source));
        CHECK(rsx_d3d12_feedback_reserve(&context, 31, &source));
        CHECK(creates == 2 && !event_count && !copies);
        CHECK(context.slots[0].resource && context.slots[31].resource &&
              context.slots[0].resource != context.slots[31].resource &&
              context.slots[0].resource != source.resource &&
              context.slots[31].resource != source.resource);
        CHECK(context.slots[31].source == source.resource && context.slots[31].width == 2 &&
              context.slots[31].height == 1 && context.slots[31].format == DXGI_FORMAT_R8G8B8A8_UNORM &&
              context.slots[31].state == D3D12_RESOURCE_STATE_COPY_DEST);
        ID3D12Resource* first = context.slots[31].resource;
        CHECK(rsx_d3d12_feedback_reserve(&context, 31, &source));
        CHECK(context.slots[31].resource == first && creates == 2 && !event_count && !releases && !bad);
        context.command_list = command;
    } else if (!strcmp(argv[1], "invalid")) {
        CHECK(!rsx_d3d12_feedback_reserve(NULL, 0, &source));
        CHECK(!rejected_without_commands(&context, 32, &source, 1));
        CHECK(!rejected_without_commands(&context, UINT32_MAX, &source, 1));
        CHECK(!rejected_without_commands(&context, 0, NULL, 1));
        rsx_d3d12_linear_resource changed = source;
        changed.resource = NULL; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.state = NULL; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.width = 0; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.height = 0; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.width = 16385; changed.view.pitch = 65540;
        CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.height = 16385; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.pitch = 4; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.format_id = 0x86; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.bytes_per_pixel = 8; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.view.location = 0; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        changed = source; changed.format = DXGI_FORMAT_R32_FLOAT; CHECK(!rejected_without_commands(&context, 0, &changed, 1));
        ID3D12Device* device = context.device;
        context.device = NULL; CHECK(!rejected_without_commands(&context, 0, &source, 1));
        context.device = device;
        CHECK(!creates && !event_count && state == D3D12_RESOURCE_STATE_RENDER_TARGET && !bad);
    } else if (!strcmp(argv[1], "description")) {
        D3D12_RESOURCE_DESC original = source_store[0].desc;
        source_store[0].desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original; source_store[0].desc.Width = 1;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original; source_store[0].desc.Height = 2;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original; source_store[0].desc.DepthOrArraySize = 2;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original; source_store[0].desc.MipLevels = 2;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original; source_store[0].desc.Format = DXGI_FORMAT_R32_FLOAT;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original; source_store[0].desc.SampleDesc.Count = 4;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original; source_store[0].desc.SampleDesc.Quality = 1;
        CHECK(!rejected_without_commands(&context, 0, &source, 1));
        source_store[0].desc = original;
        CHECK(!creates && !event_count && !bad);
    } else if (!strcmp(argv[1], "snapshot-invalid")) {
        CHECK(!rsx_d3d12_feedback_snapshot(NULL, 0, &source));
        CHECK(!rejected_without_commands(&context, 0, &source, 0));
        CHECK(rsx_d3d12_feedback_reserve(&context, 0, &source));
        CHECK(!rejected_without_commands(&context, 32, &source, 0));
        CHECK(!rejected_without_commands(&context, 0, NULL, 0));
        rsx_d3d12_linear_resource changed = source;
        changed.resource = NULL; CHECK(!rejected_without_commands(&context, 0, &changed, 0));
        changed = source; changed.state = NULL; CHECK(!rejected_without_commands(&context, 0, &changed, 0));
        changed = source; changed.state = &context.slots[0].state;
        CHECK(!rejected_without_commands(&context, 0, &changed, 0));
        changed = source; changed.resource = context.slots[0].resource;
        CHECK(!rejected_without_commands(&context, 0, &changed, 0));
        ID3D12GraphicsCommandList* command = context.command_list;
        context.command_list = NULL; CHECK(!rejected_without_commands(&context, 0, &source, 0));
        context.command_list = command;
        source_store[0].desc.SampleDesc.Count = 4;
        CHECK(!rejected_without_commands(&context, 0, &source, 0));
        source_store[0].desc.SampleDesc.Count = 1;
        CHECK(!event_count && !copies && state == D3D12_RESOURCE_STATE_RENDER_TARGET &&
              context.slots[0].state == D3D12_RESOURCE_STATE_COPY_DEST && !bad);
    } else if (!strcmp(argv[1], "identity")) {
        CHECK(rsx_d3d12_feedback_reserve(&context, 2, &source));
        ID3D12Resource* first = context.slots[2].resource;
        rsx_d3d12_linear_resource changed = source;
        changed.resource = &source_store[1].api;
        CHECK(!rejected_without_commands(&context, 2, &changed, 1));
        CHECK(!rejected_without_commands(&context, 2, &changed, 0));
        changed = source; changed.view.width = 1;
        CHECK(!rejected_without_commands(&context, 2, &changed, 1));
        CHECK(!rejected_without_commands(&context, 2, &changed, 0));
        changed = source; changed.view.height = 2;
        CHECK(!rejected_without_commands(&context, 2, &changed, 1));
        CHECK(!rejected_without_commands(&context, 2, &changed, 0));
        changed = source; changed.format = DXGI_FORMAT_R32_FLOAT;
        CHECK(!rejected_without_commands(&context, 2, &changed, 1));
        CHECK(!rejected_without_commands(&context, 2, &changed, 0));
        changed = source; changed.resource = first;
        CHECK(!rejected_without_commands(&context, 2, &changed, 1));
        CHECK(context.slots[2].resource == first && context.slots[2].source == source.resource &&
              context.slots[2].width == 2 && context.slots[2].height == 1 && !releases && !bad);
    } else if (!strcmp(argv[1], "creation-failure")) {
        CHECK(rsx_d3d12_feedback_reserve(&context, 0, &source));
        ID3D12Resource* first = context.slots[0].resource;
        fail_create = 2;
        CHECK(!rsx_d3d12_feedback_reserve(&context, 31, &source));
        CHECK(context.last_hr == E_OUTOFMEMORY && context.last_error && *context.last_error);
        CHECK(!context.slots[31].resource && !context.slots[31].source && !context.slots[31].width &&
              !context.slots[31].height && !context.slots[31].format);
        CHECK(context.slots[0].resource == first && !releases && !event_count && !copies);
        null_create = 3;
        CHECK(!rsx_d3d12_feedback_reserve(&context, 31, &source));
        CHECK(FAILED(context.last_hr) && context.last_error && !context.slots[31].resource && !releases);
        CHECK(rsx_d3d12_feedback_reserve(&context, 31, &source));
        CHECK(context.last_hr == S_OK && !context.last_error && context.slots[31].resource &&
              context.slots[0].resource == first && !event_count && !bad);
    } else if (!strcmp(argv[1], "release")) {
        rsx_d3d12_feedback_release(NULL);
        CHECK(rsx_d3d12_feedback_reserve(&context, 0, &source));
        CHECK(rsx_d3d12_feedback_reserve(&context, 31, &source));
        rsx_d3d12_feedback_release(&context);
        CHECK(releases == 2 && !bad && !source_store[0].released && !source_store[1].released);
        for (unsigned i = 0; i < 32; ++i) {
            CHECK(!context.slots[i].resource && !context.slots[i].source && !context.slots[i].state &&
                  !context.slots[i].width && !context.slots[i].height && !context.slots[i].format);
        }
        rsx_d3d12_feedback_release(&context);
        CHECK(releases == 2 && !bad);
        CHECK(rsx_d3d12_feedback_reserve(&context, 31, &source));
        CHECK(creates == 3 && !bad);
    } else return 2;
    rsx_d3d12_feedback_release(&context);
    CHECK(!bad && !source_store[0].released && !source_store[1].released);
    puts("PASS D3D12 feedback helper");
    return 0;
}
#else
int main(void)
{
    puts("FAIL D3D12 feedback helper not implemented");
    return 1;
}
#endif
