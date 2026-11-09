#!/usr/bin/env python3
"""
scripts/create_mock_savegame.py
Generates a deterministic initial save compatible with cellSaveData HLE and BCUS98125.
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Required destinations (runner VFS and the HLE's fixed root)
TARGET_DIRS = [
    ROOT / "build" / "game" / "vfs" / "hdd0" / "home" / "00000001" / "savedata" / "BCUS98125-AUTO",
    ROOT / "gamedata" / "dev_hdd0" / "home" / "00000001" / "savedata" / "BCUS98125-AUTO",
]

SOURCE_ICON = ROOT / "build" / "game" / "vfs" / "PS3_GAME" / "USRDIR" / "ui" / "xmb" / "savedata" / "icon.png"

def generate_save():
    print("[AG-SAVE-03] Generating deterministic save data for inFAMOUS 2...")

    # 1. Prepare STATE payload (32 KB, magic 0x00000052)
    state_payload = bytearray(32768)
    state_payload[0:4] = (0x00000052).to_bytes(4, byteorder="big")

    # 2. Prepare PARAM.SFO (HLE text format)
    sfo_content = (
        "TITLE=inFAMOUS™ 2\n"
        "SUB_TITLE=New Game\n"
        "DETAIL=Deterministic Initial Save\n"
        "ATTRIBUTE=0\n"
        "LIST_PARAM=\n"
    ).encode("utf-8")

    # 3. Prepare PARAM.PFD (HLE SECUREFILE list format)
    pfd_content = b"STATE\n"

    for target in TARGET_DIRS:
        target.mkdir(parents=True, exist_ok=True)
        print(f"[*] Populating directory: {target}")

        # Write PARAM.SFO
        with open(target / "PARAM.SFO", "wb") as f:
            f.write(sfo_content)

        # Write PARAM.PFD
        with open(target / "PARAM.PFD", "wb") as f:
            f.write(pfd_content)

        # Write STATE
        with open(target / "STATE", "wb") as f:
            f.write(state_payload)

        # Copy ICON0.PNG
        if SOURCE_ICON.exists():
            shutil.copyfile(SOURCE_ICON, target / "ICON0.PNG")
            print(f"    [+] ICON0.PNG copied ({SOURCE_ICON.stat().st_size} bytes)")
        else:
            print("    [!] Original icon not found in USRDIR; generating a placeholder PNG...")
            dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            with open(target / "ICON0.PNG", "wb") as f:
                f.write(dummy_png)

    print("[AG-SAVE-03] Completed successfully.")

if __name__ == "__main__":
    generate_save()
