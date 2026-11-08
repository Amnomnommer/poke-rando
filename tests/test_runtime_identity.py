"""Generated-data tests for runtime resource identity (numeric FAT IDs, encounter members, zone table).

Runtime evidence behind this: the diagnostic ROM showed W2U's own encounter record 128 (all Gible L100)
on Route 19 while the verifier had checked record 129 chosen by a species heuristic.
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from b2er import runtime_identity as ri  # noqa: E402
from b2er.gamedata import encounters as enc  # noqa: E402
from b2er.nds import arm9  # noqa: E402
from b2er.nds.narc import Narc  # noqa: E402
from b2er.nds.rom import Rom, build_rom  # noqa: E402


def record(species: int, level: int, seasons: int = 1) -> bytes:
    tables = {(c, k): [enc.Slot(species, 0, level, level)] * n for c, k, n in enc.TABLES}
    return enc.encode_record([enc.Container((1,) * 8, tables)] * seasons)


GIBLE = record(443, 100)
ROUTE = record(504, 3, seasons=4)


def zones(encounters: list[int]) -> bytes:
    rows = b""
    for i, e in enumerate(encounters):
        fields = [i % 7, 3, 100 + i, 200 + i, 300 + i, 400 + i, 7, 7, 7, 7, e] + [0] * 13
        rows += struct.pack("<24H", *fields)
    return Narc([rows]).build()


def make_rom(files_order: list[str], members: list[bytes], zone_map: list[int]) -> bytes:
    payloads = {"a/0/1/2": zones(zone_map), "a/1/2/7": Narc(members).build(), "a/0/9/1": b"T" * 20, "a/0/9/2": b"P" * 8,
                "a/0/0/9": b"extra file inserted before the others"}
    return build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), overlays=[(0, b"ov0", False)],
                     files={p: payloads[p] for p in files_order})


ORDER = ["a/0/1/2", "a/0/9/1", "a/0/9/2", "a/1/2/7"]
MEMBERS = [record(10 + i, 5) for i in range(128)] + [GIBLE, ROUTE] + [record(20, 9)] * 5
ZONE_MAP = [i % 135 for i in range(20)] + [128, 130, 0xFFFF]


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.clean = self.tmp / "clean.nds"
        self.clean.write_bytes(make_rom(ORDER, MEMBERS, ZONE_MAP))

    def write(self, name: str, data: bytes) -> Path:
        (self.tmp / name).write_bytes(data)
        return self.tmp / name

    def test_preserved_ids_and_member_decode(self) -> None:
        rebuilt = self.write("same.nds", make_rom(ORDER, MEMBERS, ZONE_MAP))
        r = ri.report(self.clean, rebuilt)
        self.assertEqual(r["identity"]["summary"]["ids_shifted"], 0)
        members = r["encounters"]["rebuilt"]["members"]
        self.assertEqual(len(members), 135)
        self.assertEqual((members[128]["seasons"], members[129]["seasons"]), (1, 4))
        self.assertEqual(members[129]["grass_singles"][0][0], [504, 0, 3, 3])
        self.assertEqual(r["encounters"]["all_gible_level_100_members"], [128])

    def test_shifted_numeric_ids_detected(self) -> None:
        # Path still correct, but the archive's numeric file ID moved: a loader using IDs gets other bytes.
        rebuilt = self.write("shift.nds", make_rom(["a/0/0/9", *ORDER], MEMBERS, ZONE_MAP))
        a, b = Rom(self.clean.read_bytes()), Rom(rebuilt.read_bytes())
        self.assertEqual(a.read_path("a/1/2/7"), b.read_path("a/1/2/7"))
        r = ri.report(self.clean, rebuilt)
        self.assertGreater(r["identity"]["summary"]["ids_shifted"], 0)
        row = next(x for x in r["identity"]["files"] if x["path"] == "a/1/2/7")
        self.assertTrue(row["same_bytes"])
        self.assertFalse(row["same_id"])
        self.assertIn("a/1/2/7", r["identity"]["summary"]["shifted_paths"])
        self.assertEqual(r["identity"]["summary"]["only_rebuilt"], ["a/0/0/9"])

    def test_zone_mapping_candidates(self) -> None:
        r = ri.report(self.clean, self.clean)
        z = r["zones"]["rebuilt"]
        self.assertEqual(z["zones"], len(ZONE_MAP))
        self.assertIn(20, z["encounter_column_candidates"])  # u16 #10 holds the encounter index here
        self.assertEqual(z["zones_by_candidate"]["20"]["128"], [20])
        self.assertEqual(z["zones_by_candidate"]["20"]["129"], [])

    def test_member_reordering_and_malformed_lengths(self) -> None:
        swapped = MEMBERS[:128] + [ROUTE, GIBLE] + MEMBERS[130:]
        r = ri.report(self.clean, self.write("swap.nds", make_rom(ORDER, swapped, ZONE_MAP)))
        self.assertEqual(r["encounters"]["all_gible_level_100_members"], [129])
        bad = MEMBERS[:129] + [ROUTE[:-4]] + MEMBERS[130:]
        r = ri.report(self.clean, self.write("bad.nds", make_rom(ORDER, bad, ZONE_MAP)))
        self.assertIn("decode_error", r["encounters"]["rebuilt"]["members"][129])

    def test_deterministic_and_bytes_free(self) -> None:
        a = json.dumps(ri.report(self.clean, self.clean), sort_keys=True)
        self.assertEqual(a, json.dumps(ri.report(self.clean, self.clean), sort_keys=True))
        self.assertNotIn(GIBLE.hex()[:32], a)
        self.assertNotIn(str(self.tmp), a)

    def test_cli_is_read_only(self) -> None:
        out = self.tmp / "report.json"
        before = self.clean.read_bytes()
        cmd = [sys.executable, "-I", str(REPO / "scripts" / "b2er_cli.py"), "diagnose-resources", str(self.clean), str(self.clean),
               "--output", str(out)]
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
        self.assertEqual(json.loads(out.read_text())["schema"], ri.SCHEMA)
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2, "never overwrites")
        self.assertEqual(self.clean.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()


class VerifierIdentityTests(unittest.TestCase):
    def test_numeric_id_change_withholds_outputs(self) -> None:
        from unittest import mock
        import synthetic_w2u as sw
        from b2er import pipeline
        from b2er.assembly import BuildError, builders
        tmp = Path(tempfile.mkdtemp())
        facts = sw.make_checkout(tmp / "W2U")
        white, black = sw.make_roms(tmp / "roms")
        pins = sw.make_pins(tmp / "pins", facts, white, black)
        real = builders.build_rom

        def shifted(**kw):  # same paths and bytes, but an extra early file moves every later numeric ID
            return real(**{**kw, "files": {"a/0/0/0": b"inserted", **kw["files"]}})

        req = pipeline.BuildRequest(rom=white, seed="ids", auxiliary={"w2u": tmp / "W2U"}, output_dir=tmp / "out",
                                    workroot_base=tmp / "work", pins_dir=pins, species_max=1023,
                                    options={"builder": "mock", "patch_pins": sw.patch_pins(facts)})
        with mock.patch.object(builders, "build_rom", shifted):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("runtime_archive_identity", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())
