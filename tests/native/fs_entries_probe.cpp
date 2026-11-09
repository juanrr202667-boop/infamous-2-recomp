/* Exercise the real sys_fs bridge against a private host directory. */
#include "ppu_recomp.h"
#include "ps3emu/error_codes.h"
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <set>
#include <string>
#include <vector>
#include <chrono>
#include <sys/stat.h>
#include <windows.h>

extern "C" void ppu_fs_register(void);
extern "C" void ps3_hle_call(uint32_t, ppu_context*);
extern "C" uint32_t ppu_vm_size;
extern "C" const char* ppu_vfs_root;
static uint8_t memory[0x40000];
extern "C" {
uint8_t* vm_base = memory;
const func_entry function_table[] = {{0, nullptr}};
const uint64_t function_table_count = 0;
}

namespace {
constexpr uint32_t entry_size = 52 + 2 + 256 + 2;
static_assert(entry_size == 312 && entry_size == 0x138);
constexpr uint32_t path_ea = 0x1000, entries_ea = 0x12000, count_ea = 0x11000;
struct PrivateDirectory {
    std::filesystem::path root;
    std::string path;
    PrivateDirectory() {
        root = std::filesystem::temp_directory_path() /
            ("i2-fsentries-" + std::to_string(GetCurrentProcessId()) + "-" +
             std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        if (!std::filesystem::create_directory(root)) throw std::runtime_error("private directory exists");
        std::ofstream(root / "alpha.dat", std::ios::binary) << "alpha";
        std::ofstream(root / "beta.bin", std::ios::binary) << "12345678";
        std::filesystem::create_directory(root / "child");
        path = root.string();
    }
    ~PrivateDirectory() {
        // Remove only the fixture's four known paths; never recursively delete.
        std::error_code ec;
        std::filesystem::remove(root / "alpha.dat", ec);
        std::filesystem::remove(root / "beta.bin", ec);
        std::filesystem::remove(root / "child", ec);
        std::filesystem::remove(root, ec);
    }
};
uint32_t invoke(ppu_context& ctx, uint32_t fd, uint64_t length,
                uint64_t entries = entries_ea, uint64_t count = count_ea) {
    ctx.gpr[3] = fd; ctx.gpr[4] = entries; ctx.gpr[5] = length; ctx.gpr[6] = count;
    ps3_hle_call(0x9B882495u, &ctx);
    return uint32_t(ctx.gpr[3]);
}
int fail(const char* why) { std::fprintf(stderr, "fs entries FAIL: %s\n", why); return 1; }
}

int main(int argc, char** argv)
{
    PrivateDirectory fixture;
    ppu_vfs_root = fixture.path.c_str(); ppu_vm_size = sizeof(memory);
    ppu_fs_register();
    ppu_context ctx{}; ctx.gpr[1] = 0x30000;
    std::strcpy(reinterpret_cast<char*>(memory + path_ea), "/dev_bdvd/");
    ctx.gpr[3] = path_ea; ctx.gpr[4] = count_ea + 4;
    ps3_hle_call(0x3F61245Cu, &ctx);
    if (ctx.gpr[3]) return fail("real Opendir did not open private fixture");
    uint32_t fd = vm_read32(count_ea + 4);
    // Independent directory handle through the already implemented Readdir:
    // compare every name, including dot entries, to prove no cursor consumption.
    ctx.gpr[3] = path_ea; ctx.gpr[4] = 0x10800;
    ps3_hle_call(0x3F61245Cu, &ctx);
    if (ctx.gpr[3]) return fail("baseline Opendir failed");
    uint32_t baseline_fd = vm_read32(0x10800);
    std::vector<std::string> baseline;
    for (unsigned i = 0; i < 20; ++i) {
        ctx.gpr[3] = baseline_fd; ctx.gpr[4] = 0x14000; ctx.gpr[5] = 0x15000;
        ps3_hle_call(0x5C74903Du, &ctx);
        if (ctx.gpr[3]) return fail("baseline Readdir failed");
        if (!vm_read64(0x15000)) break;
        baseline.emplace_back(reinterpret_cast<char*>(memory + 0x14002));
    }
    ctx.gpr[3] = baseline_fd; ps3_hle_call(0xFF42DCC3u, &ctx);
    bool invalid = argc > 1 && std::strcmp(argv[1], "invalid") == 0;
    if (invalid) {
        struct Bad { uint32_t fd; uint64_t bytes, entries, count; uint32_t rc; };
        const Bad cases[] = {
            {fd, 0, entries_ea, count_ea, CELL_EINVAL},
            {fd, 311, entries_ea, count_ea, CELL_EINVAL},
            {999, entry_size, entries_ea, count_ea, CELL_EBADF},
            {fd, entry_size, 0, count_ea, CELL_EFAULT},
            {fd, entry_size, entries_ea, 0, CELL_EFAULT},
            {fd, entry_size, sizeof(memory) - 300, count_ea, CELL_EFAULT},
            {fd, entry_size, entries_ea, sizeof(memory) - 3, CELL_EFAULT},
            {fd, entry_size, 0x100012000ull, count_ea, CELL_EFAULT}};
        std::memset(memory + entries_ea, 0xCC, 2 * entry_size + 8);
        vm_write32(count_ea, 0xA5A5A5A5);
        uint8_t before[2 * entry_size + 8]; std::memcpy(before, memory + entries_ea, sizeof(before));
        for (const auto& bad : cases) {
            if (invoke(ctx, bad.fd, bad.bytes, bad.entries, bad.count) != bad.rc)
                return fail("invalid call returned wrong error");
            if (vm_read32(count_ea) != 0xA5A5A5A5 || vm_read32(count_ea + 4) != fd ||
                std::memcmp(before, memory + entries_ea, sizeof(before)))
                return fail("invalid call changed buffer/count/adjacent fd");
        }
    }
    uint32_t capacity = argc > 1 && std::strcmp(argv[1], "single") == 0 ? 1 : 2;
    std::set<std::string> seen;
    std::vector<std::string> actual;
    unsigned total = 0, largest_count = 0;
    for (unsigned attempt = 0; attempt < 20; ++attempt) {
        std::memset(memory + entries_ea - 1, 0xCC, capacity * entry_size + 2);
        vm_write32(count_ea, 0xA5A5A5A5);
        if (invoke(ctx, fd, capacity * entry_size)) return fail("enumeration failed");
        uint32_t count = vm_read32(count_ea);
        if (count > capacity || vm_read32(count_ea + 4) != fd)
            return fail("count is not bounded u32 or overwrote adjacent fd");
        if (memory[entries_ea - 1] != 0xCC || memory[entries_ea + capacity * entry_size] != 0xCC)
            return fail("entry write escaped requested buffer");
        if (!count) {
            uint8_t before[2 * entry_size]; std::memcpy(before, memory + entries_ea, sizeof(before));
            vm_write32(count_ea, 99);
            if (invoke(ctx, fd, capacity * entry_size) || vm_read32(count_ea) ||
                std::memcmp(before, memory + entries_ea, sizeof(before)))
                return fail("repeated EOF did not preserve buffer and report zero");
            break;
        }
        if (count > largest_count) largest_count = count;
        total += count;
        for (uint32_t i = 0; i < count; ++i) {
            uint32_t ea = entries_ea + i * entry_size;
            uint8_t type = memory[ea + 52], length = memory[ea + 53];
            const char* name = reinterpret_cast<const char*>(memory + ea + 54);
            if (name[length] || std::strlen(name) != length || memory[ea + 310] || memory[ea + 311])
                return fail("dirent layout/name/padding differs from 312-byte ABI");
            if (!seen.insert(name).second) return fail("directory cursor repeated an entry");
            actual.emplace_back(name);
            if (std::strcmp(name, ".") == 0 || std::strcmp(name, "..") == 0) continue;
            auto host = (fixture.root / name).string();
            struct stat st{};
            if (stat(host.c_str(), &st)) return fail("returned nonexistent fixture entry");
            bool directory = (st.st_mode & S_IFDIR) != 0;
            if (type != (directory ? 1 : 2) ||
                (vm_read32(ea) & 0xF000u) != (directory ? 0x4000u : 0x8000u) ||
                vm_read64(ea + 0x24) != uint64_t(st.st_size) ||
                vm_read64(ea + 0x14) != uint64_t(st.st_mtime))
                return fail("stat/type/size/mtime did not match host entry");
        }
    }
    ctx.gpr[3] = fd; ps3_hle_call(0xFF42DCC3u, &ctx);
    if (actual != baseline) return fail("cursor consumed or skipped an entry vs real Readdir");
    if (!seen.count("alpha.dat") || !seen.count("beta.bin") || !seen.count("child") ||
        (capacity == 2 && largest_count != 2)) return fail("entries missing or multi-entry call not filled");
    std::fprintf(stderr, "fs entries passed: layout312 count32 adjacent-fd intact entries=%u maxBatch=%u invalid=%d\n",
                 total, largest_count, invalid);
    return 0;
}
