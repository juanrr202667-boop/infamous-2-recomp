"""Production inflater and queue, synthetic CPU inputs, no game or GPU."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"
PAYLOAD = b"Catalog integrity: literal output.\n" * 30 + b"".join(bytes([i]) * (i + 1) for i in range(64))


def compressed(kind, wrapped):
    level, strategy = {"stored": (0, zlib.Z_DEFAULT_STRATEGY),
                       "fixed": (9, zlib.Z_FIXED),
                       "dynamic": (9, zlib.Z_DEFAULT_STRATEGY)}[kind]
    compressor = zlib.compressobj(level, zlib.DEFLATED, 15 if wrapped else -15,
                                  8, strategy)
    stream = compressor.compress(PAYLOAD) + compressor.flush()
    # This assertion protects the fixture classification, not production code.
    assert ((stream[2 if wrapped else 0] >> 1) & 3) == {"stored": 0, "fixed": 1, "dynamic": 2}[kind]
    return stream


def literal_dynamic(*, incomplete=False, oversubscribed=False, repeat_overflow=False, distance_oversubscribed=False):
    """Hand-encoded dynamic block: literal A followed by EOB."""
    bits = []

    def number(value, width):
        bits.extend((value >> i) & 1 for i in range(width))

    def code(value, width):
        bits.extend((value >> i) & 1 for i in range(width - 1, -1, -1))

    # BFINAL=1, BTYPE=2, HLIT=257, HDIST=1 or 3, HCLEN=18.
    number(5, 3); number(0, 5); number(2 if distance_oversubscribed else 0, 5); number(14, 4)
    order = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1)
    lengths = {0: 1, 1: 1}
    if incomplete:
        lengths = {0: 1, 2: 1}
    if oversubscribed:
        lengths[18] = 1
    if repeat_overflow:
        lengths = {0: 1, 1: 2, 16: 2}
    for symbol in order:
        number(lengths.get(symbol, 0), 3)
    for i in range(257):
        length = (2 if incomplete else 1) if i in (65, 256) else 0
        code(2 if repeat_overflow and length else int(bool(length)), 2 if repeat_overflow and length else 1)
    if repeat_overflow:
        code(3, 2); number(0, 2)  # Repeat previous length 3 times into 1 remaining slot.
    else:
        for _ in range(3 if distance_oversubscribed else 1):
            code(1 if distance_oversubscribed else 0, 1)
    code(0, 2 if incomplete else 1)  # A
    code(1, 2 if incomplete else 1)  # EOB
    while len(bits) % 8:
        bits.append(0)
    return bytes(sum(bits[i + bit] << bit for bit in range(8)) for i in range(0, len(bits), 8))


class FastCatalogIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-catalog-integrity-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        # LFQueue uses only the public context ABI. No lifted game code required.
        (directory / "ppu_recomp.h").write_text('#include "ppu_context.h"\n', encoding="utf-8")
        cls.executable = directory / "probe.exe"
        includes = [directory, ROOT / "src/port", SDK / "include", SDK / "runtime/ppu",
                    SDK / "runtime/platform", SDK / "runtime/spu", SDK / "libs/spurs"]
        sources = [ROOT / "tests/native/fast_catalog_integrity_probe.cpp",
                   ROOT / "src/port/fast_inflate.cpp", ROOT / "src/port/lfqueue.cpp"]
        result = subprocess.run([compiler, "/nologo", "/std:c++20", "/EHsc", "/O1",
                                 "/D_CRT_SECURE_NO_WARNINGS",
                                 *("/I" + str(path) for path in includes),
                                 *(str(path) for path in sources),
                                 "/Fe" + str(cls.executable), "/Fo" + str(directory) + os.sep],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def inflate(self, stream, length):
        result = subprocess.run([str(self.executable), "inflate", stream.hex(), str(length)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ok, guarded, output = result.stdout.strip().split(" ", 2)
        self.assertEqual(guarded, "1", "production decoder wrote outside destination")
        return ok == "1", bytes.fromhex(output)

    def test_stored_fixed_dynamic_raw_and_zlib_match_literal_reference(self):
        # Catches incorrect decoding, including overlapping back references.
        for kind in ("stored", "fixed", "dynamic"):
            for wrapped in (False, True):
                with self.subTest(kind=kind, wrapped=wrapped):
                    stream = compressed(kind, wrapped)
                    self.assertEqual(zlib.decompress(stream, 15 if wrapped else -15), PAYLOAD)
                    ok, output = self.inflate(stream, len(PAYLOAD))
                    self.assertTrue(ok)
                    self.assertEqual(output, PAYLOAD)

    def test_destination_length_must_equal_produced_length(self):
        # Catches success with uninitialized tail or output overflow.
        for kind in ("stored", "fixed", "dynamic"):
            for wrapped in (False, True):
                for delta in (-1, 1):
                    with self.subTest(kind=kind, wrapped=wrapped, delta=delta):
                        self.assertFalse(self.inflate(compressed(kind, wrapped), len(PAYLOAD) + delta)[0])

    def test_multiple_blocks_preserve_output_across_stored_alignment(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                compressor = zlib.compressobj(wbits=15 if wrapped else -15)
                stream = (compressor.compress(PAYLOAD[:1000]) + compressor.flush(zlib.Z_SYNC_FLUSH) +
                          compressor.compress(PAYLOAD[1000:]) + compressor.flush())
                self.assertEqual(zlib.decompress(stream, 15 if wrapped else -15), PAYLOAD)
                self.assertEqual(self.inflate(stream, len(PAYLOAD)), (True, PAYLOAD))

    def test_truncation_never_fabricates_missing_bits(self):
        # Catches zero padding at input EOF, including a missing final EOB byte.
        for kind in ("stored", "fixed", "dynamic"):
            for wrapped in (False, True):
                stream = compressed(kind, wrapped)
                for cut in (1, 2, 4, len(stream) // 2):
                    with self.subTest(kind=kind, wrapped=wrapped, cut=cut):
                        self.assertFalse(self.inflate(stream[:-cut], len(PAYLOAD))[0])

    def test_zlib_checksum_corruption_is_rejected(self):
        # Catches skipped Adler-32 validation after otherwise valid output.
        stream = bytearray(compressed("fixed", True))
        stream[-1] ^= 1
        with self.assertRaises(zlib.error):
            zlib.decompress(stream)
        self.assertFalse(self.inflate(stream, len(PAYLOAD))[0])

    def test_trailing_input_is_rejected(self):
        # Catches reporting success before the complete descriptor is consumed.
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                self.assertFalse(self.inflate(compressed("fixed", wrapped) + b"\x00", len(PAYLOAD))[0])

    def test_unsupported_zlib_dictionary_and_window_are_rejected(self):
        # Catches accepting wrapper metadata the decoder cannot implement.
        compressor = zlib.compressobj(zdict=b"unused dictionary")
        dictionary_stream = compressor.compress(b"Z") + compressor.flush()
        self.assertFalse(self.inflate(dictionary_stream, 1)[0])
        stream = bytearray(compressed("fixed", True))
        stream[0] = 0x88  # CINFO=8 exceeds the RFC 1950 maximum of 7.
        stream[1] = (-int(stream[0]) * 256) % 31
        with self.assertRaises(zlib.error):
            zlib.decompress(stream)
        self.assertFalse(self.inflate(stream, len(PAYLOAD))[0])

    def test_malformed_blocks_are_rejected(self):
        # Reserved block type, bad stored complement, empty dynamic tree.
        for stream in (b"\x07", b"\x01\x01\x00\x00\x00X", b"\x05\x00\x00\x00"):
            with self.subTest(stream=stream.hex()):
                with self.assertRaises(zlib.error):
                    zlib.decompress(stream, -15)
                self.assertFalse(self.inflate(stream, 1)[0])

    def test_dynamic_literal_only_block_with_empty_distance_tree(self):
        stream = literal_dynamic()
        self.assertEqual(zlib.decompress(stream, -15), b"A")
        self.assertEqual(self.inflate(stream, 1), (True, b"A"))

    def test_invalid_huffman_trees_and_repeat_overflow_are_rejected(self):
        # Catches acceptance of invalid prefix spaces and clipped repeat runs.
        for variant in ("incomplete", "oversubscribed", "repeat_overflow", "distance_oversubscribed"):
            with self.subTest(variant=variant):
                stream = literal_dynamic(**{variant: True})
                with self.assertRaises(zlib.error):
                    zlib.decompress(stream, -15)
                self.assertFalse(self.inflate(stream, 1)[0])

    def test_input_and_output_overlap_is_rejected_before_writes(self):
        result = subprocess.run([str(self.executable), "overlap"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dict(item.split("=") for item in result.stdout.split()), {"ok": "0", "intact": "1"})

    def queue(self, setting, mode="push"):
        environment = dict(os.environ)
        environment.pop("I2_FAST_CATALOG", None)
        if setting is not None:
            environment["I2_FAST_CATALOG"] = setting
        result = subprocess.run([str(self.executable), "queue", mode], env=environment,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return dict(item.split("=") for item in result.stdout.strip().split()), result.stderr

    def assert_original_queue(self, setting, mode="push"):
        state, diagnostic = self.queue(setting, mode)
        self.assertEqual(state, {"result": "00000000", "completed": "0", "ticket": "1",
                                 "reserved": "0", "copied": "1", "intact": "1", "same": "0",
                                 "notifications": "1", "events": "0", "signals": "0"})
        return diagnostic

    def test_catalog_default_and_unrecognized_values_preserve_original_queue(self):
        # Catches default opt-in and any destination write by the host shortcut.
        for setting in (None, "", "0", "false", "garbage", "TRUE"):
            with self.subTest(setting=setting):
                self.assertNotIn("[i2-lfqueue-fast]", self.assert_original_queue(setting))

    def test_explicit_opt_in_uses_original_queue_without_synthetic_completion(self):
        # Catches forged completed/pop state, event flags, or broad wakeup.
        for setting in ("1", "true"):
            with self.subTest(setting=setting):
                self.assertIn("[i2-lfqueue-fast]", self.assert_original_queue(setting))

    def test_unavailable_acceleration_diagnostic_is_limited_to_once(self):
        state, diagnostic = self.queue("1", "repeat")
        self.assertEqual(diagnostic.count("[i2-lfqueue-fast]"), 1)
        self.assertEqual(state["completed"], "0")
        self.assertEqual(state["ticket"], "2")
        self.assertEqual(state["intact"], "1")
        self.assertEqual(state["notifications"], "2")
        self.assertEqual(state["events"], "0")

    def test_invalid_descriptor_stays_opaque_and_destination_intact(self):
        # Catches the removed compressed-byte fallback and unsafe descriptor reads.
        self.assert_original_queue("1", "invalid-descriptor")

    def test_original_queue_errors_do_not_publish_or_notify(self):
        # Catches success/mutation after the original queue rejects a push.
        for mode, error in (("unsupported", "80410723"), ("invalid-queue", "80410722"),
                            ("invalid-bounds", "80410732")):
            with self.subTest(mode=mode):
                state, _ = self.queue("1", mode)
                self.assertEqual(state["result"], error)
                self.assertEqual(state["same"], "1")
                self.assertEqual(state["intact"], "1")
                self.assertEqual(state["copied"], "0")
                self.assertEqual(state["notifications"], "0")
                self.assertEqual(state["events"], "0")
                self.assertEqual(state["signals"], "0")


if __name__ == "__main__":
    unittest.main()
