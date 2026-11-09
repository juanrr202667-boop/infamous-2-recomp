#!/usr/bin/env python3
from pathlib import Path

def patch_func_001D5AE4():
    path = Path("build/game/recomp/ppu_recomp_003.cpp")
    content = path.read_text(encoding="utf-8", errors="replace")

    target = "void func_001D5AE4(ppu_context* ctx) {\n        ctx->gpr[0] = vm_read32(ctx->gpr[3] + 0x20);"
    target_crlf = "void func_001D5AE4(ppu_context* ctx) {\r\n        ctx->gpr[0] = vm_read32(ctx->gpr[3] + 0x20);"

    replacement_body = (
        "void func_001D5AE4(ppu_context* ctx) {\r\n"
        "        static int s_skip = -1;\r\n"
        "        if (s_skip < 0) { const char* e = getenv(\"I2_SKIP_INTRO\"); s_skip = (e && strcmp(e, \"1\") == 0) ? 1 : 0; }\r\n"
        "        if (s_skip) { ctx->gpr[3] = 1; return; }\r\n"
        "        ctx->gpr[0] = vm_read32(ctx->gpr[3] + 0x20);"
    )

    if target_crlf in content:
        content = content.replace(target_crlf, replacement_body, 1)
        path.write_text(content, encoding="utf-8")
        print("[+] Patched func_001D5AE4 with opt-in skip check (CRLF) successfully.")
        return True
    elif target in content:
        replacement_lf = replacement_body.replace("\r\n", "\n")
        content = content.replace(target, replacement_lf, 1)
        path.write_text(content, encoding="utf-8")
        print("[+] Patched func_001D5AE4 with opt-in skip check (LF) successfully.")
        return True
    else:
        print("[!] Target pattern not found or already patched.")
        return False

if __name__ == "__main__":
    patch_func_001D5AE4()
