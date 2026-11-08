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


class WildSetupProbeTests(unittest.TestCase):
    def test_probe_brackets_the_w2u_hook(self) -> None:
        from b2er import emulator
        script = emulator.wild_setup_probe()
        self.assertEqual((emulator.WILD_SETUP_HOOK, emulator.WILD_SETUP_AFTER), (0x0201809A, 0x0201809E))
        self.assertIn("break *0x0201809a", script)
        self.assertIn("break *0x0201809e", script)
        self.assertIn("x/8hx *(unsigned int*)($r4+0x34)", script)
        self.assertTrue(script.rstrip().endswith("quit"))


if __name__ == "__main__":
    unittest.main()


class VerifierIdentityTests(unittest.TestCase):
    """Writer and verifier resolve Route 19 independently; numeric FAT IDs may shift (runtime uses paths)."""

    @classmethod
    def setUpClass(cls) -> None:
        import synthetic_w2u as sw
        cls.sw = sw
        cls.tmp = Path(tempfile.mkdtemp())
        cls.facts = sw.make_checkout(cls.tmp / "W2U")
        cls.white, cls.black = sw.make_roms(cls.tmp / "roms")
        cls.pins = sw.make_pins(cls.tmp / "pins", cls.facts, cls.white, cls.black)
        cls.n = 0

    def request(self, diagnostic=True):
        from b2er import pipeline
        type(self).n += 1
        return pipeline.BuildRequest(rom=self.white, seed="ids", auxiliary={"w2u": self.tmp / "W2U"}, output_dir=self.tmp / f"out{self.n}",
                                     workroot_base=self.tmp / f"work{self.n}", pins_dir=self.pins, species_max=1023, diagnostic=diagnostic,
                                     options={"builder": "mock", "patch_pins": self.sw.patch_pins(self.facts)})

    def test_numeric_id_shift_is_accepted_when_paths_and_zones_hold(self) -> None:
        from unittest import mock
        from b2er import pipeline
        from b2er.assembly import builders
        real = builders.build_rom

        def shifted(**kw):  # an extra early file moves every later numeric ID, as W2U's added files do (+5)
            return real(**{**kw, "files": {"a/0/0/0": b"inserted", **kw["files"]}})

        req = self.request()
        with mock.patch.object(builders, "build_rom", shifted):
            result = pipeline.run(req)
        rom = Rom((req.output_dir / result["outputs"]["rom"]).read_bytes())
        self.assertNotEqual(rom.file_id("a/1/2/7"), Rom(self.white.read_bytes()).file_id("a/1/2/7"))
        checks = {c["name"]: c["status"] for c in result["verification"]["checks"]}
        self.assertEqual((checks["zone_encounter_mapping_preserved"], checks["route19_runtime_mapping"]), ("pass", "pass"))

    def test_writer_choice_is_not_trusted(self) -> None:
        from unittest import mock
        from b2er import pipeline
        from b2er.adapters.gen5 import white2
        from b2er.assembly import BuildError
        real = white2.White2Adapter.resolve_diagnostic_area

        def wrong(self_, model, ctx=None):  # a writer that patches the wrong record (like the old heuristic)
            area, evidence = real(self_, model, ctx)
            return 0, {**evidence, "area": 0}

        req = self.request()
        with mock.patch.object(white2.White2Adapter, "resolve_diagnostic_area", wrong):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("route19_runtime_mapping", str(ctx.exception))
        self.assertIn("zone 437 -> record 2", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())

    def test_rebuilt_zone_mapping_change_is_rejected(self) -> None:
        from unittest import mock
        from b2er import pipeline
        from b2er.assembly import BuildError, builders
        real = builders.build_rom
        moved = self.sw.synthetic_zone_table({10: 0, 11: 1, 437: 3, 12: 2, 13: 4})

        def remapped(**kw):  # the ROM the game would run maps Route 19 to a record that was not patched
            return real(**{**kw, "files": {**kw["files"], "a/0/1/2": moved}})

        req = self.request()
        with mock.patch.object(builders, "build_rom", remapped):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("zone_encounter_mapping_preserved", str(ctx.exception))
        self.assertIn("route19_runtime_mapping", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())

    def test_proven_mapping_for_approved_rom_is_enforced(self) -> None:
        import hashlib
        from unittest import mock
        from b2er import pipeline
        from b2er.assembly import BuildError
        from b2er.gamedata import zones
        self.assertEqual(zones.PROVEN_ROUTE19["3e50aec3db401332175a5d2b5fe2a68ac1a05ec63995dba9d1506b1b51837446"], (437, 128))
        digest = hashlib.sha256(self.white.read_bytes()).hexdigest()
        req = self.request()
        with mock.patch.dict(zones.PROVEN_ROUTE19, {digest: (437, 128)}):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("proven mapping is (437, 128)", str(ctx.exception))


class ZoneTableTests(unittest.TestCase):
    def test_mask_none_and_one_to_one(self) -> None:
        import synthetic_w2u as sw
        from b2er.gamedata import zones
        from b2er.nds import FormatError
        table = sw.synthetic_zone_table()
        self.assertEqual(zones.resolve(table, 5), 2)
        indices = zones.encounter_indices(table)
        self.assertEqual(indices[437], 2)
        self.assertIsNone(indices[0])  # 0xDFFF (flags | 0x1FFF) is "no encounters"
        self.assertEqual(indices[12], 3)  # 0xC000 flags are masked off
        for records in ({10: 0, 11: 1, 437: 2, 12: 2, 13: 4}, {10: 0, 437: 2}, {10: 0, 11: 1, 437: 2, 12: 3, 13: 9}):
            with self.assertRaises(FormatError):
                zones.resolve(sw.synthetic_zone_table(records), 5)
        with self.assertRaises(FormatError):
            zones.resolve(sw.synthetic_zone_table({10: 0, 11: 1, 12: 2, 13: 3, 14: 4}), 5)  # zone 437 has none
        with self.assertRaises(FormatError):
            zones.zone_table(Narc([b"\x00" * 0x31]).build())

    def test_fixture_season_count_follows_the_mapped_record(self) -> None:
        one = enc.encode_record([enc.Container((1,) * 8, {(c, k): (enc.fixture_grass_slots() if (c, k) == ("GRASS", "SINGLES")
                                                                    else [enc.Slot(504, 0, 5, 5)] * n) for c, k, n in enc.TABLES})])
        self.assertEqual(enc.verify_route19_fixture(one, 1), [])
        self.assertTrue(enc.verify_route19_fixture(one, 4))
        self.assertTrue(enc.verify_route19_fixture(one * 4, 1))


class WildSetupProbeTests(unittest.TestCase):
    def test_probe_brackets_the_w2u_hook(self) -> None:
        from b2er import emulator
        script = emulator.wild_setup_probe()
        self.assertEqual((emulator.WILD_SETUP_HOOK, emulator.WILD_SETUP_AFTER), (0x0201809A, 0x0201809E))
        self.assertIn("break *0x0201809a", script)
        self.assertIn("break *0x0201809e", script)
        self.assertIn("x/8hx *(unsigned int*)($r4+0x34)", script)
        self.assertTrue(script.rstrip().endswith("quit"))


if __name__ == "__main__":
    unittest.main()


class VerifierIdentityTests(unittest.TestCase):
    """Writer and verifier resolve Route 19 independently; numeric FAT IDs may shift (runtime uses paths)."""

    @classmethod
    def setUpClass(cls) -> None:
        import synthetic_w2u as sw
        cls.sw = sw
        cls.tmp = Path(tempfile.mkdtemp())
        cls.facts = sw.make_checkout(cls.tmp / "W2U")
        cls.white, cls.black = sw.make_roms(cls.tmp / "roms")
        cls.pins = sw.make_pins(cls.tmp / "pins", cls.facts, cls.white, cls.black)
        cls.n = 0

    def request(self, diagnostic=True):
        from b2er import pipeline
        type(self).n += 1
        return pipeline.BuildRequest(rom=self.white, seed="ids", auxiliary={"w2u": self.tmp / "W2U"}, output_dir=self.tmp / f"out{self.n}",
                                     workroot_base=self.tmp / f"work{self.n}", pins_dir=self.pins, species_max=1023, diagnostic=diagnostic,
                                     options={"builder": "mock", "patch_pins": self.sw.patch_pins(self.facts)})

    def test_numeric_id_shift_is_accepted_when_paths_and_zones_hold(self) -> None:
        from unittest import mock
        from b2er import pipeline
        from b2er.assembly import builders
        real = builders.build_rom

        def shifted(**kw):  # an extra early file moves every later numeric ID, as W2U's added files do (+5)
            return real(**{**kw, "files": {"a/0/0/0": b"inserted", **kw["files"]}})

        req = self.request()
        with mock.patch.object(builders, "build_rom", shifted):
            result = pipeline.run(req)
        rom = Rom((req.output_dir / result["outputs"]["rom"]).read_bytes())
        self.assertNotEqual(rom.file_id("a/1/2/7"), Rom(self.white.read_bytes()).file_id("a/1/2/7"))
        checks = {c["name"]: c["status"] for c in result["verification"]["checks"]}
        self.assertEqual((checks["zone_encounter_mapping_preserved"], checks["route19_runtime_mapping"]), ("pass", "pass"))

    def test_writer_choice_is_not_trusted(self) -> None:
        from unittest import mock
        from b2er import pipeline
        from b2er.adapters.gen5 import white2
        from b2er.assembly import BuildError
        real = white2.White2Adapter.resolve_diagnostic_area

        def wrong(self_, model, ctx=None):  # a writer that patches the wrong record (like the old heuristic)
            area, evidence = real(self_, model, ctx)
            return 0, {**evidence, "area": 0}

        req = self.request()
        with mock.patch.object(white2.White2Adapter, "resolve_diagnostic_area", wrong):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("route19_runtime_mapping", str(ctx.exception))
        self.assertIn("zone 437 -> record 2", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())

    def test_rebuilt_zone_mapping_change_is_rejected(self) -> None:
        from unittest import mock
        from b2er import pipeline
        from b2er.assembly import BuildError, builders
        real = builders.build_rom
        moved = self.sw.synthetic_zone_table({10: 0, 11: 1, 437: 3, 12: 2, 13: 4})

        def remapped(**kw):  # the ROM the game would run maps Route 19 to a record that was not patched
            return real(**{**kw, "files": {**kw["files"], "a/0/1/2": moved}})

        req = self.request()
        with mock.patch.object(builders, "build_rom", remapped):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("zone_encounter_mapping_preserved", str(ctx.exception))
        self.assertIn("route19_runtime_mapping", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())

    def test_proven_mapping_for_approved_rom_is_enforced(self) -> None:
        import hashlib
        from unittest import mock
        from b2er import pipeline
        from b2er.assembly import BuildError
        from b2er.gamedata import zones
        self.assertEqual(zones.PROVEN_ROUTE19["3e50aec3db401332175a5d2b5fe2a68ac1a05ec63995dba9d1506b1b51837446"], (437, 128))
        digest = hashlib.sha256(self.white.read_bytes()).hexdigest()
        req = self.request()
        with mock.patch.dict(zones.PROVEN_ROUTE19, {digest: (437, 128)}):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("proven mapping is (437, 128)", str(ctx.exception))


class ZoneTableTests(unittest.TestCase):
    def test_mask_none_and_one_to_one(self) -> None:
        import synthetic_w2u as sw
        from b2er.gamedata import zones
        from b2er.nds import FormatError
        table = sw.synthetic_zone_table()
        self.assertEqual(zones.resolve(table, 5), 2)
        indices = zones.encounter_indices(table)
        self.assertEqual(indices[437], 2)
        self.assertIsNone(indices[0])  # 0xDFFF (flags | 0x1FFF) is "no encounters"
        self.assertEqual(indices[12], 3)  # 0xC000 flags are masked off
        for records in ({10: 0, 11: 1, 437: 2, 12: 2, 13: 4}, {10: 0, 437: 2}, {10: 0, 11: 1, 437: 2, 12: 3, 13: 9}):
            with self.assertRaises(FormatError):
                zones.resolve(sw.synthetic_zone_table(records), 5)
        with self.assertRaises(FormatError):
            zones.resolve(sw.synthetic_zone_table({10: 0, 11: 1, 12: 2, 13: 3, 14: 4}), 5)  # zone 437 has none
        with self.assertRaises(FormatError):
            zones.zone_table(Narc([b"\x00" * 0x31]).build())

    def test_fixture_season_count_follows_the_mapped_record(self) -> None:
        one = enc.encode_record([enc.Container((1,) * 8, {(c, k): (enc.fixture_grass_slots() if (c, k) == ("GRASS", "SINGLES")
                                                                    else [enc.Slot(504, 0, 5, 5)] * n) for c, k, n in enc.TABLES})])
        self.assertEqual(enc.verify_route19_fixture(one, 1), [])
        self.assertTrue(enc.verify_route19_fixture(one, 4))
        self.assertTrue(enc.verify_route19_fixture(one * 4, 1))


class _null:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


class WildSetupProbeTests(unittest.TestCase):
    def test_probe_brackets_the_w2u_hook(self) -> None:
        from b2er import emulator
        script = emulator.wild_setup_probe()
        self.assertEqual((emulator.WILD_SETUP_HOOK, emulator.WILD_SETUP_AFTER), (0x0201809A, 0x0201809E))
        self.assertIn("break *0x0201809a", script)
        self.assertIn("break *0x0201809e", script)
        self.assertIn("x/8hx *(unsigned int*)($r4+0x34)", script)
        self.assertTrue(script.rstrip().endswith("quit"))


if __name__ == "__main__":
    unittest.main()
