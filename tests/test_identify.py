"""Synthetic identification tests: platform probes, fingerprints, exact-hash authority, refusal of unknown/modified ROMs."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2er import identify  # noqa: E402
from b2er.adapters import AdapterRegistry, default_registry  # noqa: E402
from b2er.adapters.gen5.black2 import Black2Adapter  # noqa: E402
from b2er.nds import arm9  # noqa: E402
from b2er.nds.rom import build_rom  # noqa: E402


def gba(code: bytes, title: bytes = b"SYNTH", revision: int = 0) -> bytes:
    h = bytearray(0x200)
    h[0xA0:0xAC] = title.ljust(12, b"\x00")
    h[0xAC:0xB0] = code
    h[0xB0:0xB2] = b"01"
    h[0xB2] = 0x96
    h[0xBC] = revision
    h[0xBD] = (-sum(h[0xA0:0xBD]) - 0x19) & 0xFF
    return bytes(h) + bytes(0x1000)


def gb(title: bytes, cgb: bool = False, destination: int = 0) -> bytes:
    h = bytearray(0x8000)
    h[0x134:0x134 + len(title)] = title
    h[0x14A] = destination
    if cgb:
        h[0x143] = 0x80
    checksum = 0
    for b in h[0x134:0x14D]:
        checksum = (checksum - b - 1) & 0xFF
    h[0x14D] = checksum
    return bytes(h)


def three_ds(product: bytes) -> bytes:
    h = bytearray(0x4000)
    h[0x100:0x104] = b"NCSD"
    struct.pack_into("<I", h, 0x120, 4)  # partition 0 at 4 * 0x200
    h[0x800 + 0x100:0x800 + 0x104] = b"NCCH"
    h[0x800 + 0x150:0x800 + 0x150 + len(product)] = product
    return bytes(h)


class IdentifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.white = build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), files={"a/0/1/6": b"x"})
        self.pins = self.tmp / "inputs.json"
        self.pins.write_text(json.dumps({"schema_version": 1, "inputs": [
            {"id": "white2", "game_code": "IRDO", "role": "target", "sha256": hashlib.sha256(self.white).hexdigest()},
            {"id": "black2", "game_code": "IREO", "role": "x", "sha256": "0" * 64}]}))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ident(self, data: bytes, name: str = "rom.bin"):
        path = self.tmp / name
        path.write_bytes(data)
        return identify.identify(path, adapters=default_registry(), pins=self.pins)

    def test_clean_known_white2(self) -> None:
        r = self.ident(self.white).to_json()
        self.assertEqual((r["status"], r["adapter"], r["adapter_confidence"]), ("clean_known", "gen5.white2", "exact-hash"))
        self.assertEqual((r["generation"], r["version"], r["paired_version"], r["region"]), (5, "White 2", "Black 2", "USA/Europe"))
        self.assertEqual(r["platform"], "nds")
        self.assertTrue(any("SHA-256 equals" in e for e in r["evidence"]))

    def test_filename_is_irrelevant(self) -> None:
        a = self.ident(self.white, "pokemon_black_2_EUR.nds").to_json()
        b = self.ident(self.white, "random.bin").to_json()
        self.assertEqual(a["status"], b["status"])
        self.assertEqual(a["fingerprint"], "gen5-white2")

    def test_modified_supported_title_is_not_clean(self) -> None:
        data = bytearray(self.white)
        data[-1] ^= 1
        r = self.ident(bytes(data)).to_json()
        self.assertEqual(r["status"], "recognized_modified")
        self.assertEqual(r["adapter_confidence"], "header-only")
        self.assertTrue(any("not the approved clean release" in d for d in r["diagnostics"]))
        self.assertEqual(r["support_status"], "recognized")

    def test_hash_matching_with_wrong_header_is_refused(self) -> None:
        other = build_rom(game_code=b"IRDJ", arm9=arm9.synthetic_arm9(), files={"x": b"y"})  # Japanese White 2 header
        self.pins.write_text(json.dumps({"schema_version": 1, "inputs": [
            {"id": "white2", "game_code": "IRDO", "role": "t", "sha256": hashlib.sha256(other).hexdigest()}]}))
        self.assertEqual(self.ident(other).status, "recognized_modified")

    def test_other_regions_and_generations(self) -> None:
        cases = [
            (build_rom(game_code=b"IRDJ", arm9=arm9.synthetic_arm9(), files={"x": b"y"}), "gen5-white2", "Japan", "recognized_modified"),
            (build_rom(game_code=b"CPUE", arm9=arm9.synthetic_arm9(), files={"x": b"y"}), "gen4-platinum", "USA", "recognized_unverified"),
            (build_rom(game_code=b"IRBO", arm9=arm9.synthetic_arm9(), files={"x": b"y"}), "gen5-black", "USA/Europe", "recognized_unverified"),
            (gba(b"BPEE"), "gen3-emerald", "USA", "recognized_unverified"),
            (gba(b"BPRD"), "gen3-firered", "Germany", "recognized_unverified"),
            (gb(b"POKEMON RED"), "gen1-red", "Japan", "recognized_unverified"),
            (gb(b"PM_CRYSTAL", cgb=True, destination=1), "gen2-crystal", "Overseas", "recognized_unverified"),
            (three_ds(b"CTR-P-EKJA"), "gen6-x", "All regions", "recognized_unverified"),
            (three_ds(b"CTR-P-A2BA"), "gen7-ultra-moon", "All regions", "recognized_unverified"),
        ]
        for data, fp, region, status in cases:
            with self.subTest(fp=fp):
                r = self.ident(data).to_json()
                self.assertEqual((r["fingerprint"], r["region"], r["status"]), (fp, region, status))
                # Recognized titles get their family's recognition-only adapter; never more than "recognized".
                self.assertEqual(r["support_status"], "recognized")
                if status == "recognized_unverified":
                    self.assertTrue(r["adapter"].endswith(fp), r["adapter"])
                    adapter = default_registry().get(r["adapter"])
                    levels = {c.value: s.level.value for c, s in adapter.capabilities().items()}
                    self.assertEqual(levels.pop("rom_recognition"), "implemented")
                    self.assertEqual(set(levels.values()), {"unavailable"})

    def test_unknown_and_non_pokemon(self) -> None:
        self.assertEqual(self.ident(b"\x00" * 4096).status, "unknown")
        r = self.ident(build_rom(game_code=b"ZZZE", arm9=arm9.synthetic_arm9(), files={"x": b"y"}))
        self.assertEqual(r.status, "unknown")
        self.assertIn("not a known Pokémon title", r.diagnostics[0])
        self.assertEqual(self.ident(gba(b"AXXE")).status, "unknown")

    def test_switch_and_gamecube_containers(self) -> None:
        xci = bytearray(0x200)
        xci[0x100:0x104] = b"HEAD"
        r = self.ident(bytes(xci))
        self.assertEqual((r.header.platform, r.status), ("switch", "unknown"))
        gc = bytearray(0x100)
        gc[0:6] = b"GC6E01"
        struct.pack_into(">I", gc, 0x1C, 0xC2339F3D)
        self.assertEqual(self.ident(bytes(gc)).fingerprint["id"], "gen3-colosseum")

    def test_bad_header_checksums_rejected(self) -> None:
        data = bytearray(gba(b"BPEE"))
        data[0xBD] ^= 1
        self.assertEqual(self.ident(bytes(data)).status, "unknown")
        nds = bytearray(self.white)
        nds[0x15C] ^= 1  # logo CRC
        self.assertEqual(self.ident(bytes(nds)).status, "unknown")

    def test_identification_is_read_only(self) -> None:
        path = self.tmp / "ro.nds"
        path.write_bytes(self.white)
        path.chmod(0o444)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        identify.identify(path, adapters=default_registry(), pins=self.pins)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_fingerprints_and_registry_are_consistent(self) -> None:
        data = identify.load_fingerprints()
        ids = {t["id"] for t in data["titles"]}
        for adapter in default_registry().all():
            self.assertTrue(set(adapter.fingerprints) <= ids, adapter.id)
        with self.assertRaises(ValueError):
            AdapterRegistry([Black2Adapter(), Black2Adapter()])
        platinum = default_registry().for_fingerprint("gen4-platinum")
        self.assertEqual((platinum.id, platinum.family_id), ("nds-gen4.gen4-platinum", "nds-gen4"))
        self.assertIsNone(default_registry().for_fingerprint("gen3-colosseum"))  # not a required mainline title

    def test_required_coverage_is_complete(self) -> None:
        required = {"Red", "Green", "Blue", "Yellow", "Gold", "Silver", "Crystal", "Ruby", "Sapphire", "Emerald", "FireRed",
                    "LeafGreen", "Diamond", "Pearl", "Platinum", "HeartGold", "SoulSilver", "Black", "White", "Black 2",
                    "White 2", "X", "Y", "Omega Ruby", "Alpha Sapphire", "Sun", "Moon", "Ultra Sun", "Ultra Moon"}
        titles = [t for t in identify.load_fingerprints()["titles"] if t["required"]]
        self.assertEqual({t["version"] for t in titles}, required)
        registry = default_registry()
        families = {"gb": {1, 2}, "gba": {3}, "nds-gen4": {4}, "nds-gen5": {5}, "ctr-gen6": {6}, "ctr-gen7": {7}}
        for t in titles:
            self.assertIn(t["generation"], families[t["family"]], t["id"])
            self.assertIsNotNone(registry.for_fingerprint(t["id"]), t["id"])

    def test_support_ladder_claims_are_evidence_bound(self) -> None:
        from b2er import coverage
        from b2er.capabilities import SupportStatus
        data = coverage.load()
        matrix = coverage.matrix(identify.load_fingerprints(), default_registry(), data)
        buildable = [t["fingerprint"] for fam in matrix.values() for t in fam["titles"]
                     if SupportStatus(t["title_status"]).at_least(SupportStatus.BUILDABLE)]
        self.assertEqual(buildable, ["gen5-white2"])  # only the pinned, end-to-end-tested release
        for release in data["pinned_releases"]:
            self.assertTrue(release["evidence"] and release["approved_input"])
            self.assertFalse(SupportStatus(release["status"]).at_least(SupportStatus.FEATURE_COMPLETE),
                             "no release may claim feature-complete before every required capability is implemented")


if __name__ == "__main__":
    unittest.main()
