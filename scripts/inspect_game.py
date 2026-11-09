"""Read-only PS3 disc inventory. Never extracts or modifies game data."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct
import zlib


def _range(data: bytes, offset: int, length: int) -> bytes:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise ValueError(f"field outside supplied data: offset={offset} length={length}")
    return data[offset : offset + length]


def parse_self_header(data: bytes) -> dict:
    _range(data, 0, 0x70)
    magic, version, key_rev, header_type, _, header_len, elf_size = struct.unpack_from(
        ">IIHHIQQ", data
    )
    if magic != 0x53434500 or header_type != 1:
        raise ValueError("not a PS3 SELF")
    offsets = struct.unpack_from(">8Q", data, 0x20)
    elf_off, seginfo_off = offsets[2], offsets[5]
    if _range(data, elf_off, 6) != b"\x7fELF\x02\x02":
        raise ValueError("SELF lacks embedded big-endian ELF64 header")
    machine = struct.unpack_from(">H", data, elf_off + 18)[0]
    phnum = struct.unpack_from(">H", data, elf_off + 0x38)[0]
    _range(data, seginfo_off, phnum * 0x20)
    encrypted = 0
    segments = []
    for i in range(phnum):
        off, size, compression, _, _, encryption = struct.unpack_from(
            ">QQIIII", data, seginfo_off + i * 0x20
        )
        encrypted += bool(encryption and size)
        segments.append({
            "index": i,
            "file_offset": off,
            "stored_size": size,
            "compression": compression,
            "encryption": encryption,
        })
    return {
        "container": "SELF",
        "version": version,
        "key_revision": f"0x{key_rev:04X}",
        "header_length": header_len,
        "declared_elf_size": elf_size,
        "elf_machine": {21: "PowerPC64"}.get(machine, f"unknown ({machine})"),
        "program_header_count": phnum,
        "encrypted_segments": encrypted,
        "recompilable_elf": key_rev == 0x8000 and encrypted == 0,
        "segments": segments,
    }


def parse_sfo(data: bytes) -> dict:
    _range(data, 0, 20)
    magic, _, key_table, data_table, count = struct.unpack_from("<4s4I", data)
    if magic != b"\x00PSF":
        raise ValueError("not a PARAM.SFO")
    _range(data, 20, count * 16)
    result = {}
    for i in range(count):
        key_off, fmt, length, _, value_off = struct.unpack_from(
            "<HHIII", data, 20 + i * 16
        )
        key_start = key_table + key_off
        _range(data, key_start, 1)
        key_end = data.find(b"\0", key_start)
        if key_end < 0:
            raise ValueError("unterminated SFO key")
        key = data[key_start:key_end].decode("utf-8", "replace")
        raw = _range(data, data_table + value_off, length)
        if fmt == 0x404 and length == 4:
            value = struct.unpack("<I", raw)[0]
        elif fmt in (0x204, 0x004):
            value = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
        else:
            value = {"format": f"0x{fmt:04X}", "hex": raw.hex()}
        result[key] = value
    return result


def parse_psarc_header(data: bytes) -> dict:
    _range(data, 0, 32)
    magic, version, compression, toc_length, entry_size, file_count, block_size, flags = (
        struct.unpack_from(">4sI4sIIIII", data)
    )
    if magic != b"PSAR":
        raise ValueError("not a PSARC")
    return {
        "version": f"0x{version:08X}",
        "compression": compression.decode("ascii", "replace"),
        "toc_length": toc_length,
        "toc_entry_size": entry_size,
        "file_count": file_count,
        "block_size": block_size,
        "flags": flags,
    }


def parse_pack_prefix(data: bytes, declared_length: int | None = None) -> dict:
    """Inspect a PACK header and complete index table from a bounded prefix."""
    _range(data, 0, 8)
    magic, version_size = struct.unpack_from(">II", data)
    version, header_size = version_size >> 16, version_size & 0xFFFF
    if magic != 0x5041434B or version not in (8, 9):
        raise ValueError("unsupported PACK signature or version")
    if header_size != {8: 0x70, 9: 0x80}[version]:
        raise ValueError("unexpected PACK header size")
    _range(data, 0, header_size)
    table_offset, table_length = struct.unpack_from(">II", data, 6 * 4)
    payload_offset, payload_length = struct.unpack_from(">II", data, 10 * 4)
    table_end = table_offset + table_length
    if table_offset != header_size or table_length < 24 or table_length % 4:
        raise ValueError("invalid PACK table coordinates")
    _range(data, table_offset, table_length)
    empty_stub = payload_offset == 0 and payload_length == 0
    if not empty_stub and table_end != payload_offset:
        raise ValueError("PACK payload does not follow the table")
    if declared_length is not None:
        expected = table_end if empty_stub else payload_offset + payload_length
        if declared_length != expected:
            raise ValueError("PACK coordinates disagree with declared member length")

    words = struct.unpack_from(f">{table_length // 4}I", data, table_offset)
    descriptor_count, resource_count, range_count = words[1], words[3], words[5]
    range_width = 5 if version == 8 else 4
    if (words[0], words[2], words[4]) != (0, 0, 0):
        raise ValueError("PACK table has nonzero reserved header fields")
    if 6 + descriptor_count * 7 + resource_count * 4 + range_count * range_width != len(words):
        raise ValueError("PACK table record counts disagree with its length")

    cursor = 6
    tags: Counter[str] = Counter()
    for _ in range(descriptor_count):
        tag, _, _, reserved0, reserved1, first_resource, count = words[cursor:cursor + 7]
        if reserved0 or reserved1 or first_resource + count > resource_count:
            raise ValueError("invalid PACK descriptor")
        tags[f"0x{tag:08X}"] += 1
        cursor += 7
    types: Counter[str] = Counter()
    stored = 0
    for _ in range(resource_count):
        type_flags, byte_length, logical_offset, reserved = words[cursor:cursor + 4]
        if reserved:
            raise ValueError("invalid PACK resource reserved field")
        types[f"0x{type_flags:08X}"] += 1
        stored += logical_offset + byte_length <= payload_length
        cursor += 4
    expected_first = 0
    for _ in range(range_count):
        first, count, _, reserved = words[cursor:cursor + 4]
        if first != expected_first or reserved:
            raise ValueError("invalid PACK range record")
        if version == 8 and words[cursor + 4] != 0xFFFFFFFF:
            raise ValueError("invalid PACK v8 range sentinel")
        expected_first = first + count
        cursor += range_width
    return {
        "version": version,
        "header_size": header_size,
        "table_offset": table_offset,
        "table_length": table_length,
        "payload_offset": payload_offset,
        "payload_length": payload_length,
        "empty_stub": empty_stub,
        "descriptor_count": descriptor_count,
        "resource_count": resource_count,
        "range_record_count": range_count,
        "stored_resource_count": stored,
        "descriptor_tags": dict(sorted(tags.items())),
        "resource_type_flags": dict(sorted(types.items())),
    }


def read_psarc_manifest(path: Path) -> list[str]:
    """Read only the PSARC name manifest; do not materialize archive members."""
    with path.open("rb") as stream:
        header = parse_psarc_header(stream.read(32))
        if header["flags"] & 4:
            raise ValueError("encrypted PSARC table is unsupported")
        if header["compression"] != "zlib" or header["toc_entry_size"] != 30:
            raise ValueError("unsupported PSARC layout")
        count = header["file_count"]
        block_size = header["block_size"]
        toc_length = header["toc_length"]
        if count < 1 or block_size < 1 or toc_length > 64 * 1024 * 1024:
            raise ValueError("invalid PSARC table dimensions")
        table = stream.read(toc_length - 32)
        _range(table, 0, count * 30)
        first = table[:30]
        if first[:16] != bytes(16):
            raise ValueError("first PSARC entry is not a manifest")
        chunk_index = int.from_bytes(first[16:20], "big")
        manifest_length = int.from_bytes(first[20:25], "big")
        data_offset = int.from_bytes(first[25:30], "big")
        if manifest_length > 64 * 1024 * 1024:
            raise ValueError("manifest exceeds inspection limit")
        chunk_count = (manifest_length + block_size - 1) // block_size
        chunk_width = ((block_size - 1).bit_length() + 7) // 8
        chunk_table = table[count * 30:]
        _range(chunk_table, chunk_index * chunk_width, chunk_count * chunk_width)
        stream.seek(data_offset)
        chunks = []
        for index in range(chunk_count):
            expected = min(block_size, manifest_length - len(chunks) * block_size)
            start = (chunk_index + index) * chunk_width
            size = int.from_bytes(chunk_table[start:start + chunk_width], "big") or block_size
            blob = stream.read(size)
            if len(blob) != size:
                raise ValueError("truncated PSARC manifest chunk")
            try:
                decoded = zlib.decompress(blob)
            except zlib.error:
                decoded = blob
            if len(decoded) != expected:
                raise ValueError("PSARC manifest chunk length mismatch")
            chunks.append(decoded)
        names = b"".join(chunks).decode("utf-8", "replace").splitlines()
        if len(names) != count - 1:
            raise ValueError(f"manifest has {len(names)} names for {count - 1} entries")
        return names


def _psarc_member_descriptor(path: Path, member_name: str) -> tuple[dict, bytes]:
    names = read_psarc_manifest(path)
    try:
        member_index = names.index(member_name) + 1
    except ValueError as exc:
        raise ValueError(f"member not found: {member_name}") from exc
    with path.open("rb") as stream:
        header = parse_psarc_header(stream.read(32))
        stream.seek(32 + member_index * 30)
        descriptor = stream.read(30)
        _range(descriptor, 0, 30)
    return header, descriptor


def read_psarc_member_length(path: Path, member_name: str) -> int:
    """Read the uncompressed length from a named PSARC member descriptor."""
    _, descriptor = _psarc_member_descriptor(path, member_name)
    return int.from_bytes(descriptor[20:25], "big")


def read_psarc_member_prefix(path: Path, member_name: str, length: int = 64) -> bytes:
    """Read the first decoded chunk of one named PSARC member into memory."""
    header, descriptor = _psarc_member_descriptor(path, member_name)
    with path.open("rb") as stream:
        chunk_index = int.from_bytes(descriptor[16:20], "big")
        member_length = int.from_bytes(descriptor[20:25], "big")
        data_offset = int.from_bytes(descriptor[25:30], "big")
        block_size = header["block_size"]
        if length < 0 or length > block_size:
            raise ValueError("requested prefix exceeds one PSARC chunk")
        if member_length == 0:
            return b""
        chunk_width = ((block_size - 1).bit_length() + 7) // 8
        stream.seek(32 + header["file_count"] * 30 + chunk_index * chunk_width)
        stored = stream.read(chunk_width)
        _range(stored, 0, chunk_width)
        stored_size = int.from_bytes(stored, "big") or block_size
        stream.seek(data_offset)
        blob = stream.read(stored_size)
        if len(blob) != stored_size:
            raise ValueError("truncated PSARC member chunk")
        try:
            decoded = zlib.decompress(blob)
        except zlib.error:
            decoded = blob
        if len(decoded) != min(block_size, member_length):
            raise ValueError("PSARC member chunk length mismatch")
        return decoded[:length]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root: Path) -> dict:
    games = [p for p in root.rglob("PS3_GAME") if p.is_dir()]
    if len(games) != 1:
        raise ValueError(f"expected exactly one PS3_GAME under {root}; found {len(games)}")
    disc = games[0].parent
    counts: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    files = []
    for path in sorted(p for p in disc.rglob("*") if p.is_file()):
        rel = path.relative_to(disc).as_posix()
        size = path.stat().st_size
        suffix = path.suffix.lower() or "(none)"
        counts[suffix] += 1
        sizes[suffix] += size
        with path.open("rb") as stream:
            prefix = stream.read(4096)
        entry = {"path": rel, "bytes": size, "extension": suffix, "magic_hex": prefix[:8].hex()}
        if path.name == "EBOOT.BIN":
            entry["self"] = parse_self_header(prefix)
            entry["sha256"] = _sha256(path)
        elif path.name == "PARAM.SFO":
            entry["sfo"] = parse_sfo(path.read_bytes())
            entry["sha256"] = _sha256(path)
        elif suffix in (".psarc", ".psarc_s") and prefix.startswith(b"PSAR"):
            entry["psarc"] = parse_psarc_header(prefix)
            try:
                entry["psarc"]["member_names"] = read_psarc_manifest(path)
            except ValueError as exc:
                entry["psarc"]["manifest_error"] = str(exc)
        files.append(entry)
    return {
        "source_root": str(root.resolve()),
        "disc_root": str(disc.resolve()),
        "total_files": len(files),
        "total_bytes": sum(sizes.values()),
        "counts_by_extension": dict(sorted(counts.items())),
        "bytes_by_extension": dict(sorted(sizes.items())),
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inventory(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{report['total_files']} files, {report['total_bytes']} bytes -> {args.output}")


if __name__ == "__main__":
    main()
