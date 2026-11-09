"""Read a bounded PACK index from one PSARC member, without extracting assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct

from scripts.inspect_game import parse_pack_prefix, read_psarc_member_length, read_psarc_member_prefix


def inspect_xpps(archive: Path, member: str) -> dict:
    declared_length = read_psarc_member_length(archive, member)
    header = read_psarc_member_prefix(archive, member, 64)
    if len(header) < 32:
        raise ValueError("PACK member header is truncated")
    table_offset, table_length = struct.unpack_from(">II", header, 6 * 4)
    prefix = read_psarc_member_prefix(archive, member, table_offset + table_length)
    result = parse_pack_prefix(prefix, declared_length=declared_length)
    return {"archive": str(archive), "member": member, "member_length": declared_length, **result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("member")
    args = parser.parse_args()
    print(json.dumps(inspect_xpps(args.archive, args.member), indent=2))


if __name__ == "__main__":
    main()
