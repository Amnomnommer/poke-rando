"""Generated-data tests for exact NARC extents and the CTRMap `.arc` overlay pre-merge.

Real failure: `encounters_decodable: NARC declares 40804 bytes but is 41500`. Running the pinned CTRMap on
generated data reproduced it: ROMBuilder's `.arc` overlay merge copies the shorter rebuilt archive over the
base archive without truncating, so the base archive's stale tail (696 = 3 x 232 bytes here) stays inside
the ROM FAT allocation. The fix merges with CTRMap's own DiskFile path before ROMBuilder; NARC parsing
stays strict.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_w2u as sw  # noqa: E402
from b2er import pipeline  # noqa: E402
from b2er.assembly import BuildError, builders, narc_premerge as npm, patches  # noqa: E402
from b2er.nds import FormatError, arm9  # noqa: E402
from b2er.nds.narc import Narc  # noqa: E402
from b2er.nds.rom import Rom, build_rom  # noqa: E402

BASE_MEMBERS = [bytes([i]) * 928 for i in range(3)]
NEW_MEMBERS = [bytes([0x10]) * 928, bytes([0x11]) * 232, bytes([0x12]) * 928]


def sorted_children(directory: Path) -> list[Path]:
    return sorted((p for p in directory.iterdir() if p.name != npm.DOT_ARC), key=lambda p: int(p.name))


def disk_merge(pairs) -> None:
    """Emulates CTRMap's DirectNARC.setData on a DiskFile target: full rebuild, then a truncating move."""
    for target, directory in pairs:
        Path(target).write_bytes(Narc([p.read_bytes() for p in sorted_children(Path(directory))]).build())


def vfs_merge(pairs) -> None:
    """Emulates the defective ROMBuilder path: FSUtil.copy over the existing file without truncation."""
    for target, directory in pairs:
        new = Narc([p.read_bytes() for p in sorted_children(Path(directory))]).build()
        with open(target, "r+b") as handle:
            handle.write(new)


class NarcExtentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.narc = Narc(NEW_MEMBERS).build()

    def test_exact_size_valid_narc(self) -> None:
        self.assertEqual(npm.narc_extent(self.narc, "x"), len(self.narc))
        self.assertEqual(Narc.parse(self.narc).members, NEW_MEMBERS)

    def test_alignment_padding_lies_inside_the_declared_extent(self) -> None:
        odd = Narc([b"\x01" * 5, b"\x02" * 3]).build()
        self.assertEqual(struct.unpack_from("<I", odd, 8)[0], len(odd))
        self.assertEqual(Narc.parse(odd).members, [b"\x01" * 5, b"\x02" * 3])

    def test_stale_or_non_padding_trailing_bytes_rejected(self) -> None:
        base = Narc(BASE_MEMBERS).build()
        stale = self.narc + base[len(self.narc):]
        self.assertEqual(len(stale) - len(self.narc), 696)
        for data in (stale, self.narc + b"\xFF" * 4, self.narc + b"\x00"):
            with self.assertRaises(FormatError) as ctx:
                Narc.parse(data)
            self.assertIn(f"NARC declares {len(self.narc)} bytes but is {len(data)}", str(ctx.exception))
            with self.assertRaises(npm.PremergeError):
                npm.narc_extent(data, "a/1/2/7")

    def test_declared_size_larger_than_stored(self) -> None:
        with self.assertRaises(FormatError):
            Narc.parse(self.narc[:-4])
        with self.assertRaises(npm.PremergeError):
            npm.narc_extent(self.narc[:-4], "x")

    def test_malformed_section_boundaries(self) -> None:
        bad = bytearray(self.narc)
        struct.pack_into("<I", bad, 0x14, 0xFFFF)  # BTAF section size
        with self.assertRaises(FormatError):
            Narc.parse(bytes(bad))
        bad = bytearray(self.narc)
        bad[0x10:0x14] = b"XXXX"
        with self.assertRaises(FormatError):
            Narc.parse(bytes(bad))

    def test_deterministic_packing(self) -> None:
        self.assertEqual({Narc(NEW_MEMBERS).build() for _ in range(3)}, {self.narc})


class RomFatBoundaryTests(unittest.TestCase):
    def rom(self) -> bytearray:
        return bytearray(build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), overlays=[(0, b"ov0", False)],
                                   files={"a/1/2/7": Narc(NEW_MEMBERS).build(), "a/0/9/1": b"T" * 20, "z": b"tail"}))

    def test_extraction_preserves_exact_file_boundaries(self) -> None:
        rom = Rom(bytes(self.rom()))
        self.assertEqual(rom.read_path("a/1/2/7"), Narc(NEW_MEMBERS).build())
        self.assertEqual(rom.read_path("a/0/9/1"), b"T" * 20)
        self.assertEqual(rom.read_path("z"), b"tail")

    def test_fat_entry_too_long_or_too_short_is_rejected(self) -> None:
        for delta in (696, 4, -4):
            data = self.rom() + b"\xFF" * 1024  # room for an over-long FAT entry inside the image
            rom = Rom(bytes(data))
            fid = rom.file_id("a/1/2/7")
            fat = rom.field("fat_offset")
            start, end = rom.fat()[fid]
            struct.pack_into("<I", data, fat + fid * 8 + 4, end + delta)
            stored = Rom(bytes(data)).read_path("a/1/2/7")
            self.assertEqual(len(stored), end - start + delta)
            with self.assertRaises(FormatError):
                Narc.parse(stored)


class PremergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.base = self.root / "IRDO_Extracted" / "data" / "a" / "1" / "2" / "7"
        self.base.parent.mkdir(parents=True)
        self.base.write_bytes(Narc(BASE_MEMBERS).build())
        self.w2u = self.root / "w2u"
        self.overlay = self.w2u / "vfs" / "data" / "a" / "1" / "2" / "7"
        self.overlay.mkdir(parents=True)
        (self.overlay / ".arc").write_text(".arc\ncompress default auto\n")
        for i, member in enumerate(NEW_MEMBERS):
            (self.overlay / str(i)).write_bytes(member)
        new_only = self.w2u / "vfs" / "data" / "a" / "9" / "9" / "9"
        new_only.mkdir(parents=True)
        (new_only / ".arc").write_text(".arc\n")
        (new_only / "0").write_bytes(b"new")
        self.cmproj = self.w2u / "White2Upgrade.cmproj"
        self.cmproj.write_text('LastROMPath: ""\nUserDataPath: data\nVFSBase: ../IRDO_Extracted\nVFSOverlay: vfs\n')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_overlay_replaced_by_exact_merge(self) -> None:
        base_before = self.base.read_bytes()
        report = npm.premerge(self.cmproj, self.w2u / "work", disk_merge)
        self.assertFalse(self.overlay.exists(), "the folded overlay directory is removed")
        self.assertEqual(self.base.read_bytes(), Narc(NEW_MEMBERS).build())
        (entry,) = report["merged"]
        self.assertEqual((entry["path"], entry["base_size"], entry["merged_size"], entry["stale_tail_avoided"]),
                         ("data/a/1/2/7", len(base_before), len(base_before) - 696, 696))
        self.assertTrue((self.w2u / "vfs" / "data" / "a" / "9" / "9" / "9").is_dir(), "archives without a base are left to CTRMap")
        self.assertEqual(json.loads((self.w2u / "work" / npm.REPORT).read_text()), report)
        self.assertNotIn("\\x", json.dumps(report))

    def test_non_truncating_merge_is_refused(self) -> None:
        with self.assertRaises(npm.PremergeError) as ctx:
            npm.premerge(self.cmproj, self.w2u / "work", vfs_merge)
        self.assertIn("data/a/1/2/7: NARC declares", str(ctx.exception))
        self.assertTrue(self.overlay.is_dir(), "the staged overlay is untouched on failure")
        self.assertEqual(self.base.read_bytes(), Narc(BASE_MEMBERS).build(), "the base is untouched on failure")

    def test_deterministic_and_symlinks_refused(self) -> None:
        first = npm.premerge(self.cmproj, self.w2u / "work", disk_merge)
        self.base.write_bytes(Narc(BASE_MEMBERS).build())
        self.overlay.mkdir()
        (self.overlay / ".arc").write_text(".arc\ncompress default auto\n")
        for i, member in enumerate(NEW_MEMBERS):
            (self.overlay / str(i)).write_bytes(member)
        self.assertEqual(npm.premerge(self.cmproj, self.w2u / "work2", disk_merge), first)
        # A symlinked overlay directory is never followed, so nothing outside the staged tree is merged.
        outside = self.root / "outside"
        outside.mkdir()
        (outside / ".arc").write_text(".arc\n")
        (outside / "0").write_bytes(b"x")
        os.symlink(outside, self.w2u / "vfs" / "data" / "linked")
        (self.root / "IRDO_Extracted" / "data" / "linked").write_bytes(Narc(BASE_MEMBERS).build())
        report = npm.premerge(self.cmproj, self.w2u / "work3", disk_merge)
        self.assertEqual([e["path"] for e in report["merged"]], [])
        self.assertTrue((outside / ".arc").is_file())

    def test_base_that_is_not_a_valid_narc_is_refused(self) -> None:
        self.base.write_bytes(Narc(BASE_MEMBERS).build() + b"\x00" * 8)
        with self.assertRaises(npm.PremergeError) as ctx:
            npm.premerge(self.cmproj, self.w2u / "work", disk_merge)
        self.assertIn("base data/a/1/2/7", str(ctx.exception))

    def test_meson_step_runs_before_rombuilder(self) -> None:
        (self.w2u / "meson.build").write_text("project('S')\nrom = custom_target(\n" + sw.SYNTHETIC_ROM_COMMAND + ")\n")
        report = patches.premerge_narcs(self.w2u, REPO)
        text = (self.w2u / "meson.build").read_text()
        step = text.index("b2er_premerge_narcs.py")
        self.assertLess(step, text.index("ctrmap.cli.ROMBuilder"))
        self.assertEqual(text.count("b2er_premerge_narcs.py"), 1)
        self.assertEqual(report["script"], patches.NARC_PREMERGE_SCRIPT)
        # Run the generated step with a stand-in `java` that performs the DiskFile merge.
        fake = self.root / "fake-java"
        fake.write_text("#!/usr/bin/env python3\nimport sys\nsys.path.insert(0, %r)\nfrom pathlib import Path\n"
                        "import test_narc_premerge as t\nargs = sys.argv[4:]\n"
                        "t.disk_merge([(Path(args[i]), Path(args[i + 1])) for i in range(0, len(args), 2)])\n"
                        % str(Path(__file__).resolve().parent))
        fake.chmod(0o755)
        result = subprocess.run([sys.executable, "-I", str(self.w2u / patches.NARC_PREMERGE_SCRIPT), "jar", str(self.cmproj),
                                 str(fake)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pre-merged 1", result.stdout)
        self.assertEqual(self.base.read_bytes(), Narc(NEW_MEMBERS).build())
        (self.w2u / "meson.build").write_text("project('S')\n")
        with self.assertRaises(BuildError):
            patches.premerge_narcs(self.w2u, REPO)


class PipelineNarcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.checkout = cls.tmp / "W2U"
        cls.facts = sw.make_checkout(cls.checkout)
        cls.white, cls.black = sw.make_roms(cls.tmp / "roms")
        cls.pins = sw.make_pins(cls.tmp / "pins", cls.facts, cls.white, cls.black)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def request(self, name: str) -> pipeline.BuildRequest:
        return pipeline.BuildRequest(rom=self.white, seed="narc", auxiliary={"w2u": self.checkout},
                                     output_dir=self.tmp / f"out-{name}", workroot_base=self.tmp / f"work-{name}", pins_dir=self.pins,
                                     species_max=1023, options={"builder": "mock", "patch_pins": sw.patch_pins(self.facts)})

    def test_encounter_verification_passes_with_exact_extents(self) -> None:
        result = pipeline.run(self.request("ok"))
        checks = {c["name"]: c["status"] for c in result["verification"]["checks"]}
        self.assertEqual((checks["encounters_decodable"] if "encounters_decodable" in checks else "pass"), "pass")
        self.assertEqual(checks["narc_extents_exact"], "pass")
        self.assertIn("narc_premerge", result["manifest"]["transformations"]["prepare"])

    def test_stale_tail_in_rebuilt_rom_withholds_outputs(self) -> None:
        class StaleNarc(Narc):
            def build(self) -> bytes:  # the defective non-truncating write: old bytes after the archive
                return super().build() + b"\x02" * 696

        req = self.request("stale")
        with mock.patch.object(builders, "Narc", StaleNarc):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("NARC declares", str(ctx.exception))
        self.assertIn("but is", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())


if __name__ == "__main__":
    unittest.main()
