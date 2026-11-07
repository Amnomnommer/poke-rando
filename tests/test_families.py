"""Synthetic tests for engine-family containers (GB/GBC, GBA, 3DS) and the coverage claim audit."""

from __future__ import annotations

import copy
import random
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2er import coverage, identify  # noqa: E402
from b2er.adapters import default_registry  # noqa: E402
from b2er.families import ctr, gb, gba  # noqa: E402
from b2er.nds import FormatError  # noqa: E402


class GbTests(unittest.TestCase):
    def image(self) -> bytearray:
        data = bytearray(0x8000)
        data[0x134:0x13F] = b"POKEMON RED"
        data[0x148] = 0
        gb.refresh(data)
        return data

    def test_checksums_validate_and_refresh(self) -> None:
        data = self.image()
        self.assertEqual(gb.validate(bytes(data)), [])
        h = gb.parse(bytes(data))
        self.assertEqual((h.title, h.destination, h.rom_size), ("POKEMON RED", "Japan", 0x8000))
        data[0x5000] ^= 0xFF
        self.assertIn("global checksum mismatch (image modified or bad dump)", gb.validate(bytes(data)))
        gb.refresh(data)
        self.assertEqual(gb.validate(bytes(data)), [])

    def test_size_and_header_problems(self) -> None:
        data = self.image()
        data[0x148] = 1  # declares 64 KiB
        gb.refresh(data)
        self.assertTrue(any("declares" in p for p in gb.validate(bytes(data))))
        self.assertTrue(gb.validate(b"\x00" * 100))


class GbaTests(unittest.TestCase):
    def test_header_checksum(self) -> None:
        data = bytearray(0x40000)
        data[0xA0:0xAC] = b"POKEMON EMER"
        data[0xAC:0xB0] = b"BPEE"
        data[0xB2] = 0x96
        gba.refresh(data)
        self.assertEqual(gba.validate(bytes(data)), [])
        data[0xBD] ^= 1
        self.assertEqual(gba.validate(bytes(data)), ["header complement checksum mismatch"])
        self.assertTrue(gba.validate(bytes(data[:0x40001])))

    def test_lz77_round_trip_and_known_stream(self) -> None:
        rng = random.Random(7)
        for size in (0, 1, 17, 300, 5000):
            payload = bytes(rng.randrange(4) for _ in range(size))
            packed = gba.lz77_compress(payload)
            self.assertEqual(gba.lz77_decompress(packed), payload)
            self.assertEqual(len(packed) % 4, 0)
        # Hand-assembled stream: literals "ab", then a reference (length 4, displacement 2) -> "ababab".
        stream = bytes([0x10, 6, 0, 0, 0b00100000, ord("a"), ord("b"), 0x10, 0x01])
        self.assertEqual(gba.lz77_decompress(stream), b"ababab")
        with self.assertRaises(FormatError):
            gba.lz77_decompress(bytes([0x10, 4, 0, 0, 0x80, 0x00, 0x05]))  # reference before output start


class CtrTests(unittest.TestCase):
    def ncsd(self, product: bytes, encrypted: bool) -> bytes:
        data = bytearray(0x1000)
        data[0x100:0x104] = b"NCSD"
        struct.pack_into("<II", data, 0x120, 2, 4)
        base = 0x400
        data[base + 0x100:base + 0x104] = b"NCCH"
        data[base + 0x150:base + 0x150 + len(product)] = product
        data[base + 0x18F] = 0x00 if encrypted else 0x04
        struct.pack_into("<H", data, base + 0x112, 3)
        return bytes(data)

    def test_ncsd_and_crypto_state(self) -> None:
        part = ctr.parse_ncsd(self.ncsd(b"CTR-P-EKJA", False))[0]
        self.assertEqual((part.product_code, part.encrypted, part.version), ("CTR-P-EKJA", False, 3))
        self.assertEqual(ctr.validate(self.ncsd(b"CTR-P-EKJA", False)), [])
        self.assertIn("encrypted", ctr.validate(self.ncsd(b"CTR-P-EKJA", True))[0])
        with self.assertRaises(FormatError):
            ctr.parse_ncsd(b"\x00" * 0x400)

    def test_identify_reports_encryption(self) -> None:
        header = identify.probe_3ds(self.ncsd(b"CTR-P-BNDA", True), 0x1000)
        self.assertEqual((header.game_code, header.integrity["ncch_encrypted"]), ("BNDA", True))

    def test_garc_round_trip(self) -> None:
        files = [{0: b"alpha"}, {0: b"", 1: b"beta beta"}, {}, {3: bytes(range(50))}]
        for version in (0x0400, 0x0600):
            data = ctr.Garc(files, version).build()
            parsed = ctr.Garc.parse(data)
            self.assertEqual(parsed.files, files)
            self.assertEqual(parsed.build(), data)
        unaligned = ctr.Garc(files, 0x0400, alignment=1).build()
        self.assertEqual(ctr.Garc.parse(unaligned).build(), unaligned)

    def test_garc_rejects_malformed(self) -> None:
        data = ctr.Garc([{0: b"abc"}]).build()
        for bad in (b"XXXX" + data[4:], data[:-1], data.replace(b"OTAF", b"OOPS")):
            with self.assertRaises(FormatError):
                ctr.Garc.parse(bad)


class CoverageAuditTests(unittest.TestCase):
    def test_current_claims_are_backed(self) -> None:
        self.assertEqual(coverage.audit(default_registry()), [])

    def test_overclaims_are_rejected(self) -> None:
        data = copy.deepcopy(coverage.load())
        data["pinned_releases"][0]["status"] = "feature-complete"  # White 2 lacks hidden items, shops, #1025
        problems = coverage.audit(default_registry(), data)
        self.assertTrue(any("hidden_item_randomization" in p for p in problems), problems)
        data = copy.deepcopy(coverage.load())
        data["pinned_releases"][1]["status"] = "buildable"  # Black 2 is recognition-only
        self.assertTrue(any("repacking" in p for p in coverage.audit(default_registry(), data)))
        data["pinned_releases"].append({"id": "x", "fingerprint": "gen4-platinum", "game_code": "CPUE", "approved_input": "x",
                                        "adapter": "nds-gen4.gen4-platinum", "status": "buildable", "evidence": "none"})
        self.assertTrue(any(p.startswith("x:") for p in coverage.audit(default_registry(), data)))

    def test_invalid_status_vocabulary(self) -> None:
        data = copy.deepcopy(coverage.load())
        data["pinned_releases"][0]["status"] = "supported"
        path = Path(__file__).resolve().parent / "_tmp_coverage.json"
        import json
        path.write_text(json.dumps(data))
        try:
            with self.assertRaises(ValueError):
                coverage.load(path)
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
