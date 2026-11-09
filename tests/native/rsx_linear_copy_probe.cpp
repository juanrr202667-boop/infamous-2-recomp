// Standalone real D3D12 copy validation. No game data or shader compiler.
// The expected source indices are hand-derived from guest byte addresses.
// Building this file does not authorize running its GPU command list.
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <dxgi1_4.h>
#include <d3d12.h>
#include <d3d12sdklayers.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "rsx_d3d12_linear_copy.h"

template<class T> struct Owned {
    T* p = nullptr;
    Owned() = default;
    Owned(const Owned&) = delete;
    Owned& operator=(const Owned&) = delete;
    ~Owned() { if (p) p->lpVtbl->Release(p); }
    void** raw() { return reinterpret_cast<void**>(&p); }
};

struct Event {
    HANDLE handle = nullptr;
    ~Event() { if (handle) CloseHandle(handle); }
};

static bool checked(HRESULT result, const char* stage)
{
    if (SUCCEEDED(result)) return true;
    std::fprintf(stderr, "FAIL %s: HRESULT=0x%08lX\n", stage,
                 static_cast<unsigned long>(result));
    return false;
}

static D3D12_RESOURCE_DESC texture(UINT width, UINT height)
{
    D3D12_RESOURCE_DESC result = {};
    result.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    result.Width = width;
    result.Height = height;
    result.DepthOrArraySize = 1;
    result.MipLevels = 1;
    result.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    result.SampleDesc.Count = 1;
    result.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
    result.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
    return result;
}

static D3D12_RESOURCE_DESC buffer(UINT64 bytes)
{
    D3D12_RESOURCE_DESC result = {};
    result.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    result.Width = bytes;
    result.Height = 1;
    result.DepthOrArraySize = 1;
    result.MipLevels = 1;
    result.SampleDesc.Count = 1;
    result.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    return result;
}

static bool create_resource(ID3D12Device* device, D3D12_HEAP_TYPE type,
        const D3D12_RESOURCE_DESC& description, D3D12_RESOURCE_STATES state,
        Owned<ID3D12Resource>& output)
{
    D3D12_HEAP_PROPERTIES heap = {};
    heap.Type = type;
    return checked(device->lpVtbl->CreateCommittedResource(device, &heap,
        D3D12_HEAP_FLAG_NONE, &description, state, nullptr, IID_ID3D12Resource,
        output.raw()), "CreateCommittedResource");
}

static D3D12_TEXTURE_COPY_LOCATION subresource(ID3D12Resource* resource)
{
    D3D12_TEXTURE_COPY_LOCATION result = {};
    result.pResource = resource;
    result.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    result.SubresourceIndex = 0;
    return result;
}

static D3D12_TEXTURE_COPY_LOCATION placed(ID3D12Resource* resource,
        UINT64 offset, UINT width, UINT height)
{
    D3D12_TEXTURE_COPY_LOCATION result = {};
    result.pResource = resource;
    result.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    result.PlacedFootprint.Offset = offset;
    result.PlacedFootprint.Footprint.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    result.PlacedFootprint.Footprint.Width = width;
    result.PlacedFootprint.Footprint.Height = height;
    result.PlacedFootprint.Footprint.Depth = 1;
    result.PlacedFootprint.Footprint.RowPitch = D3D12_TEXTURE_DATA_PITCH_ALIGNMENT;
    return result;
}

static void transition(ID3D12GraphicsCommandList* command, ID3D12Resource* resource,
        D3D12_RESOURCE_STATES& state, D3D12_RESOURCE_STATES wanted)
{
    if (state == wanted) return;
    D3D12_RESOURCE_BARRIER barrier = {};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition.pResource = resource;
    barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    barrier.Transition.StateBefore = state;
    barrier.Transition.StateAfter = wanted;
    command->lpVtbl->ResourceBarrier(command, 1, &barrier);
    state = wanted;
}

static bool inspect_messages(ID3D12InfoQueue* info)
{
    if (!info) {
        std::puts("STATUS validation=unavailable; pixel comparison remains required");
        return true;
    }
    UINT64 errors = 0, warnings = 0;
    const UINT64 count = info->lpVtbl->GetNumStoredMessages(info);
    for (UINT64 i = 0; i < count; ++i) {
        SIZE_T bytes = 0;
        if (!checked(info->lpVtbl->GetMessage(info, i, nullptr, &bytes), "GetMessage size"))
            return false;
        std::vector<unsigned char> storage(bytes);
        auto* message = reinterpret_cast<D3D12_MESSAGE*>(storage.data());
        if (!checked(info->lpVtbl->GetMessage(info, i, message, &bytes), "GetMessage"))
            return false;
        if (message->Severity == D3D12_MESSAGE_SEVERITY_ERROR ||
            message->Severity == D3D12_MESSAGE_SEVERITY_CORRUPTION) ++errors;
        if (message->Severity == D3D12_MESSAGE_SEVERITY_WARNING) ++warnings;
        if (i < 64) std::fprintf(stderr, "[D3D12-IQ][sev=%d id=%d] %s\n",
            message->Severity, message->ID, message->pDescription);
    }
    std::printf("STATUS validation_messages=%llu errors=%llu warnings=%llu\n",
        static_cast<unsigned long long>(count), static_cast<unsigned long long>(errors),
        static_cast<unsigned long long>(warnings));
    return errors == 0;
}

struct CopyCase {
    const char* name;
    UINT width, height, pitch;
    UINT64 offset;
    UINT expected_indices[8];
};

int main(int argc, char** argv)
{
    const bool warp = argc == 2 && std::strcmp(argv[1], "--warp") == 0;
    if (argc != 1 && !warp) {
        std::fprintf(stderr, "Usage: rsx_linear_copy_probe.exe [--warp]\n");
        return 2;
    }
    constexpr UINT64 base = 0x1000;
    constexpr UINT64 block = 1024; // 4 rows at 256 bytes; offsets are 512-aligned.
    const CopyCase cases[3] = {
        {"same-pitch", 2, 2, 16, base,      {0, 1, 4, 5}},
        {"different-pitch", 2, 4, 8, base, {0, 1, 2, 3, 4, 5, 6, 7}},
        {"interior-offset", 2, 4, 8, base + 12, {3, 4, 5, 6, 7, 8, 9, 10}},
    };
    std::uint32_t pattern[16];
    for (UINT i = 0; i < 16; ++i) pattern[i] = 0xA0302010u + i * 0x01010101u;

    Owned<ID3D12Debug> debug;
    const HRESULT debug_result = D3D12GetDebugInterface(IID_ID3D12Debug, debug.raw());
    if (SUCCEEDED(debug_result)) debug.p->lpVtbl->EnableDebugLayer(debug.p);
    std::printf("STATUS debug_layer=%s HRESULT=0x%08lX\n",
        debug.p ? "enabled" : "unavailable", static_cast<unsigned long>(debug_result));

    Owned<IDXGIFactory4> factory;
    Owned<IDXGIAdapter1> adapter;
    Owned<ID3D12Device> device;
    if (!checked(CreateDXGIFactory1(IID_IDXGIFactory4, factory.raw()), "CreateDXGIFactory1"))
        return 2;
    if (warp) {
        if (!checked(factory.p->lpVtbl->EnumWarpAdapter(factory.p, IID_IDXGIAdapter1,
                     adapter.raw()), "EnumWarpAdapter") ||
            !checked(D3D12CreateDevice(reinterpret_cast<IUnknown*>(adapter.p),
                     D3D_FEATURE_LEVEL_11_0, IID_ID3D12Device, device.raw()),
                     "D3D12CreateDevice(WARP)")) return 2;
    } else {
        for (UINT index = 0; !device.p; ++index) {
            Owned<IDXGIAdapter1> candidate;
            HRESULT enumerated = factory.p->lpVtbl->EnumAdapters1(factory.p, index, &candidate.p);
            if (enumerated == DXGI_ERROR_NOT_FOUND) break;
            if (!checked(enumerated, "EnumAdapters1")) return 2;
            DXGI_ADAPTER_DESC1 description = {};
            if (!checked(candidate.p->lpVtbl->GetDesc1(candidate.p, &description), "GetDesc1"))
                return 2;
            if (description.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) continue;
            Owned<ID3D12Device> candidate_device;
            const HRESULT supported = D3D12CreateDevice(reinterpret_cast<IUnknown*>(candidate.p),
                D3D_FEATURE_LEVEL_11_0, IID_ID3D12Device, candidate_device.raw());
            if (SUCCEEDED(supported)) {
                adapter.p = candidate.p; candidate.p = nullptr;
                device.p = candidate_device.p; candidate_device.p = nullptr;
                std::printf("STATUS hardware_adapter=%u vendor=0x%04X device=0x%04X\n",
                            index, description.VendorId, description.DeviceId);
            }
        }
        if (!device.p) {
            std::fprintf(stderr, "FAIL no hardware adapter supports D3D12; no silent WARP fallback\n");
            return 2;
        }
    }
    Owned<ID3D12InfoQueue> info;
    const HRESULT info_result = device.p->lpVtbl->QueryInterface(device.p,
        IID_ID3D12InfoQueue, info.raw());
    std::printf("STATUS adapter=%s info_queue=%s HRESULT=0x%08lX\n",
        warp ? "WARP" : "hardware", info.p ? "available" : "unavailable",
        static_cast<unsigned long>(info_result));

    Owned<ID3D12CommandQueue> queue;
    Owned<ID3D12CommandAllocator> allocator;
    Owned<ID3D12Fence> fence;
    D3D12_COMMAND_QUEUE_DESC queue_description = {};
    queue_description.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    if (!checked(device.p->lpVtbl->CreateCommandQueue(device.p, &queue_description,
                 IID_ID3D12CommandQueue, queue.raw()), "CreateCommandQueue") ||
        !checked(device.p->lpVtbl->CreateCommandAllocator(device.p, D3D12_COMMAND_LIST_TYPE_DIRECT,
                 IID_ID3D12CommandAllocator, allocator.raw()), "CreateCommandAllocator") ||
        !checked(device.p->lpVtbl->CreateFence(device.p, 0, D3D12_FENCE_FLAG_NONE,
                 IID_ID3D12Fence, fence.raw()), "CreateFence")) return 2;
    Event completed;
    completed.handle = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!completed.handle) {
        std::fprintf(stderr, "FAIL CreateEventW: WinError=%lu\n", GetLastError());
        return 2;
    }

    // Keep these resources alive until the command list is released or its
    // submitted work has completed. No resource destruction during submission.
    Owned<ID3D12Resource> textures[4], upload, readback;
    D3D12_RESOURCE_STATES states[4] = {
        D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COPY_DEST,
        D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COPY_DEST};
    if (!create_resource(device.p, D3D12_HEAP_TYPE_DEFAULT, texture(4, 4), states[0], textures[0]) ||
        !create_resource(device.p, D3D12_HEAP_TYPE_UPLOAD, buffer(4 * block),
                         D3D12_RESOURCE_STATE_GENERIC_READ, upload) ||
        !create_resource(device.p, D3D12_HEAP_TYPE_READBACK, buffer(3 * block),
                         D3D12_RESOURCE_STATE_COPY_DEST, readback)) return 2;
    for (UINT i = 0; i < 3; ++i)
        if (!create_resource(device.p, D3D12_HEAP_TYPE_DEFAULT,
                             texture(cases[i].width, cases[i].height), states[i + 1],
                             textures[i + 1])) return 2;

    unsigned char* mapped = nullptr;
    const D3D12_RANGE no_reads = {0, 0};
    if (!checked(upload.p->lpVtbl->Map(upload.p, 0, &no_reads,
                 reinterpret_cast<void**>(&mapped)), "Map upload")) return 2;
    std::memset(mapped, 0xCD, static_cast<SIZE_T>(4 * block));
    for (UINT y = 0; y < 4; ++y)
        std::memcpy(mapped + y * 256, pattern + y * 4, 4 * sizeof(pattern[0]));
    constexpr std::uint32_t sentinel = 0x5A5A5A5Au;
    for (UINT i = 0; i < 3; ++i)
        for (UINT y = 0; y < cases[i].height; ++y)
            for (UINT x = 0; x < cases[i].width; ++x)
                std::memcpy(mapped + (i + 1) * block + y * 256 + x * 4,
                            &sentinel, sizeof(sentinel));
    const D3D12_RANGE uploaded = {0, static_cast<SIZE_T>(4 * block)};
    upload.p->lpVtbl->Unmap(upload.p, 0, &uploaded);

    Owned<ID3D12GraphicsCommandList> command;
    if (!checked(device.p->lpVtbl->CreateCommandList(device.p, 0, D3D12_COMMAND_LIST_TYPE_DIRECT,
                 allocator.p, nullptr, IID_ID3D12GraphicsCommandList, command.raw()),
                 "CreateCommandList")) return 2;
    for (UINT i = 0; i < 4; ++i) {
        const UINT width = i ? cases[i - 1].width : 4;
        const UINT height = i ? cases[i - 1].height : 4;
        auto destination = subresource(textures[i].p);
        auto source = placed(upload.p, i * block, width, height);
        command.p->lpVtbl->CopyTextureRegion(command.p, &destination, 0, 0, 0, &source, nullptr);
        transition(command.p, textures[i].p, states[i], i ? D3D12_RESOURCE_STATE_RENDER_TARGET
                                                        : D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    }

    const rsx_linear_view source_view = {base, 4, 4, 16, 4, 0x85, 1};
    rsx_d3d12_linear_resource resources[4] = {};
    resources[0] = {textures[0].p, &states[0], source_view, DXGI_FORMAT_R8G8B8A8_UNORM};
    for (UINT i = 0; i < 3; ++i) {
        const rsx_linear_view view = {cases[i].offset, cases[i].width,
            cases[i].height, cases[i].pitch, 4, 0x85, 1};
        resources[i + 1] = {textures[i + 1].p, &states[i + 1], view,
                           DXGI_FORMAT_R8G8B8A8_UNORM};
        rsx_d3d12_linear_copy_context context = {command.p, resources, 4};
        // Geometry callback carries only a span; the production emitter takes
        // stable source/destination IDs as well, supplied by this local relay.
        struct Relay {
            rsx_d3d12_linear_copy_context* context;
            UINT dst;
            static int emit(void* opaque, const rsx_linear_copy_span* span) {
                auto* relay = static_cast<Relay*>(opaque);
                return rsx_d3d12_linear_copy_emit(relay->context, 0, relay->dst, span);
            }
        } relay = {&context, i + 1};
        const int result = rsx_linear_foreach_overlap(&source_view, &view, Relay::emit, &relay);
        if (result != RSX_LINEAR_OK) {
            std::fprintf(stderr, "FAIL plan %s: result=%d\n", cases[i].name, result);
            inspect_messages(info.p);
            return 1;
        }
        transition(command.p, textures[i + 1].p, states[i + 1], D3D12_RESOURCE_STATE_COPY_SOURCE);
        auto destination = placed(readback.p, i * block, view.width, view.height);
        auto source = subresource(textures[i + 1].p);
        command.p->lpVtbl->CopyTextureRegion(command.p, &destination, 0, 0, 0, &source, nullptr);
    }
    if (!checked(command.p->lpVtbl->Close(command.p), "Close command list")) {
        inspect_messages(info.p);
        return 2;
    }
    ID3D12CommandList* commands[] = {reinterpret_cast<ID3D12CommandList*>(command.p)};
    queue.p->lpVtbl->ExecuteCommandLists(queue.p, 1, commands);
    // If completion cannot be established, keep COM objects alive until this
    // standalone process exits rather than releasing resources still in flight.
    if (!checked(queue.p->lpVtbl->Signal(queue.p, fence.p, 1), "Signal fence") ||
        !checked(fence.p->lpVtbl->SetEventOnCompletion(fence.p, 1, completed.handle),
                 "SetEventOnCompletion")) std::exit(2);
    const DWORD wait = WaitForSingleObject(completed.handle, 10000);
    if (wait != WAIT_OBJECT_0) {
        std::fprintf(stderr, "FAIL GPU fence wait=%lu removed=0x%08lX\n", wait,
            static_cast<unsigned long>(device.p->lpVtbl->GetDeviceRemovedReason(device.p)));
        inspect_messages(info.p);
        std::exit(2);
    }

    const D3D12_RANGE read = {0, static_cast<SIZE_T>(3 * block)};
    if (!checked(readback.p->lpVtbl->Map(readback.p, 0, &read,
                 reinterpret_cast<void**>(&mapped)), "Map readback")) return 2;
    bool correct = true;
    UINT checked_pixels = 0;
    for (UINT i = 0; i < 3; ++i) {
        bool case_correct = true;
        for (UINT y = 0; y < cases[i].height; ++y)
            for (UINT x = 0; x < cases[i].width; ++x) {
                std::uint32_t actual = 0;
                std::memcpy(&actual, mapped + i * block + y * 256 + x * 4, sizeof(actual));
                const UINT expected_index = cases[i].expected_indices[y * cases[i].width + x];
                if (actual != pattern[expected_index]) {
                    std::fprintf(stderr, "FAIL %s pixel=(%u,%u) actual=0x%08X expected=A[%u]=0x%08X\n",
                        cases[i].name, x, y, actual, expected_index, pattern[expected_index]);
                    case_correct = false;
                }
                ++checked_pixels;
            }
        std::printf("%s case=%s pixels=%u\n", case_correct ? "PASS" : "FAIL",
                    cases[i].name, cases[i].width * cases[i].height);
        correct = correct && case_correct;
    }
    readback.p->lpVtbl->Unmap(readback.p, 0, &no_reads);
    const bool clean = inspect_messages(info.p);
    if (!correct || !clean) return 1;
    std::printf("PASS production linear copies: exact_rgba_pixels=%u depth=1 adapter=%s\n",
                checked_pixels, warp ? "WARP" : "hardware");
    return 0;
}
