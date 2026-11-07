"""Synthetic tests for DSi-enhanced component extraction, pre-assembly checks, and the ROMBuilder output guard.

Real failure: CTRMap's ROMBuilder threw `File __ROM/arm9i.bin does not exist!` for the DSi-enhanced
White 2 base, apparently still exited 0, and the footer script then ran on an empty output. Every
fixture here is generated; nothing is taken from a ROM.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_w2u as sw  # noqa: E402
from b2er.assembly import BuildError, ctrmap_dsi, patches, stage  # noqa: E402
from b2er.nds import FormatError, arm9, dsi  # noqa: E402
from b2er.nds.narc import Narc  # noqa: E402
from b2er.nds.rom import build_rom  # noqa: E402

ARM9I = bytes((i * 7) & 0xFF for i in range(0x6000))
ARM7I = bytes((i * 11 + 1) & 0xFF for i in range(0x2400))
FILES = {"a/0/1/6": Narc([b"x"]).build(), "a/1/2/7": b"enc"}


def dsi_rom(**kw) -> bytes:
    return build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), files=FILES, overlays=[(0, b"ov0", False)],
                     dsi={"arm9i": ARM9I, "arm7i": ARM7I, **kw})


def ntr_rom() -> bytes:
    return build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), files=FILES, overlays=[(0, b"ov0", False)])


def tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


class DsiParsingTests(unittest.TestCase):
    def test_layout_and_exact_bytes(self) -> None:
        data = dsi_rom()
        layout = dsi.parse(data)
        self.assertEqual(layout.unit_code & 0x02, 0x02)
        self.assertEqual(layout.arm9i[1], len(ARM9I))
        self.assertEqual(layout.arm7i[1], len(ARM7I))
        self.assertEqual(layout.modcrypt1, (layout.arm9i[0], 0x4000))
        raw = dsi.extract_raw(data)
        self.assertEqual(raw["arm9i.bin"], ARM9I)
        self.assertEqual(raw["arm7i.bin"], ARM7I)
        self.assertEqual(raw["twlblowfishtable.bin"], data[layout.arm9i[0] - 0x3000:layout.arm9i[0]])
        self.assertEqual(len(raw["twlblowfishtable.bin"]), dsi.BLOWFISH_TABLE_SIZE)

    def test_ntr_only_has_no_dsi_components(self) -> None:
        data = ntr_rom()
        self.assertIsNone(dsi.parse(data))
        self.assertEqual(dsi.extract_raw(data), {})
        self.assertEqual(dsi.required_components(data[:0x1000]), ["header.bin", "arm9.bin", "arm7.bin", "banner.bin"])

    def test_out_of_bounds_and_truncated_regions_refused(self) -> None:
        good = dsi_rom()

        def mutated(offset: int, value: int) -> bytes:
            data = bytearray(good)
            struct.pack_into("<I", data, offset, value)
            return bytes(data)

        layout = dsi.parse(good)
        cases = {
            "arm9i past end": mutated(0x1CC, len(good)),
            "arm7i past end": mutated(0x1D0, len(good) - 4),
            "empty arm9i": mutated(0x1CC, 0),
            "empty arm7i": mutated(0x1DC, 0),
            "arm9i inside header": mutated(0x1C0, 0x200),
            "modcrypt not at arm9i": mutated(0x220, layout.arm9i[0] + 0x10),
            "modcrypt larger than arm9i": mutated(0x224, len(ARM9I) + 1),
            "truncated image": good[: layout.arm7i[0] + 16],
            "header only": good[:0x800],
        }
        for label, data in cases.items():
            with self.subTest(case=label), self.assertRaises(FormatError):
                dsi.extract_raw(data)
        no_room = bytearray(good)
        struct.pack_into("<H", no_room, 0x92, (layout.arm9i[0] - 0x100) // 0x80000 + 1)
        with self.assertRaises(FormatError):
            dsi.parse(bytes(no_room))


class StagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ctrmap_layout_paths_and_provider(self) -> None:
        seen = {}

        def provider(components):  # stands in for CTRMap's area-1 transform
            seen.update(components)
            arm9i = bytearray(components["arm9i.bin"])
            arm9i[:0x4000] = bytes(b ^ 0x5A for b in arm9i[:0x4000])
            return bytes(arm9i)

        dest = self.tmp / "IRDO_Extracted"
        report = stage.extract_base(dsi_rom(), dest, provider)
        expected = {"header.bin", "arm9.bin", "arm7.bin", "y9.bin", "y7.bin", "banner.bin", "arm9i.bin", "arm7i.bin",
                    "twlblowfishtable.bin", "overlay/overlay_0000.bin", "data/a/0/1/6", "data/a/1/2/7"}
        self.assertEqual(set(tree(dest)), expected)
        self.assertTrue(report["dsi_enhanced"])
        self.assertEqual((dest / "arm7i.bin").read_bytes(), ARM7I)
        arm9i = (dest / "arm9i.bin").read_bytes()
        self.assertEqual(arm9i[0x4000:], ARM9I[0x4000:])
        self.assertEqual(arm9i[:0x4000], bytes(b ^ 0x5A for b in ARM9I[:0x4000]))
        self.assertEqual(len((dest / "header.bin").read_bytes()), 0x1000)
        self.assertEqual(seen["arm9i.bin"], ARM9I)
        self.assertEqual(dsi.missing_components([dest]), [])

    def test_dsi_rom_requires_provider_and_bad_regions_write_nothing(self) -> None:
        with self.assertRaises(BuildError):
            stage.extract_base(dsi_rom(), self.tmp / "a")
        self.assertFalse((self.tmp / "a").exists())
        bad = bytearray(dsi_rom())
        struct.pack_into("<I", bad, 0x1DC, len(bad))
        with self.assertRaises(FormatError):
            stage.extract_base(bytes(bad), self.tmp / "b", lambda c: c["arm9i.bin"])
        self.assertFalse((self.tmp / "b").exists())
        with self.assertRaises(BuildError):
            stage.extract_base(dsi_rom(), self.tmp / "c", lambda c: c["arm9i.bin"][:-1])

    def test_ntr_only_rom_needs_no_dsi_components(self) -> None:
        dest = self.tmp / "ntr"
        report = stage.extract_base(ntr_rom(), dest)
        self.assertFalse(report["dsi_enhanced"])
        self.assertFalse(any((dest / n).exists() for n in dsi.DSI_COMPONENTS))
        self.assertEqual(dsi.missing_components([dest]), [])

    def test_missing_components_reported_before_assembly(self) -> None:
        dest = self.tmp / "base"
        stage.extract_base(dsi_rom(), dest, lambda c: c["arm9i.bin"])
        for name in dsi.DSI_COMPONENTS + ("banner.bin", "arm7.bin"):
            with self.subTest(component=name):
                moved = dest / name
                held = moved.read_bytes()
                moved.unlink()
                self.assertIn(name, dsi.missing_components([dest]))
                moved.write_bytes(held)
        (dest / "arm7i.bin").write_bytes(b"short")
        self.assertTrue(any(m.startswith("arm7i.bin (size 5") for m in dsi.missing_components([dest])))
        overlay_root = self.tmp / "vfs"
        overlay_root.mkdir()
        (overlay_root / "arm7i.bin").write_bytes(ARM7I)  # an overlay copy satisfies presence (CTRMap merges vfs over base)
        self.assertEqual(dsi.missing_components([overlay_root, dest]), [])
        os.remove(dest / "y9.bin")
        self.assertIn("arm9ovltable.bin or y9.bin", dsi.missing_components([dest]))

    def test_overlay_id_must_equal_table_index(self) -> None:
        mismatched = build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), files=FILES, overlays=[(36, b"ov36", False)])
        with self.assertRaises(BuildError) as ctx:
            stage.extract_base(mismatched, self.tmp / "m")
        self.assertIn("index naming and W2U's ID references would disagree", str(ctx.exception))

    def test_repeated_extraction_is_deterministic(self) -> None:
        rom = dsi_rom()
        stage.extract_base(rom, self.tmp / "one", lambda c: c["arm9i.bin"])
        stage.extract_base(rom, self.tmp / "two", lambda c: c["arm9i.bin"])
        self.assertEqual(tree(self.tmp / "one"), tree(self.tmp / "two"))


class CtrmapCrossCheckTests(unittest.TestCase):
    def test_compare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            theirs = Path(tmp)
            ours = {"arm7i.bin": ARM7I, "twlblowfishtable.bin": b"B" * 0x3000, "header.bin": b"H" * 0x1000}
            for name, data in ours.items():
                (theirs / name).write_bytes(data)
            decrypted = bytes(0x4000) + ARM9I[0x4000:]
            (theirs / "arm9i.bin").write_bytes(decrypted)
            arm9i, problems = ctrmap_dsi.compare(ours, theirs, ARM9I, 0x4000)
            self.assertEqual((arm9i, problems), (decrypted, []))
            (theirs / "arm9i.bin").write_bytes(bytes(0x4000) + bytes(len(ARM9I) - 0x4000))
            self.assertIn("arm9i.bin differs outside modcrypt area 1", ctrmap_dsi.compare(ours, theirs, ARM9I, 0x4000)[1])
            (theirs / "arm7i.bin").write_bytes(ARM7I[:-1] + b"\x00")
            self.assertTrue(any("arm7i.bin differs" in p for p in ctrmap_dsi.compare(ours, theirs, ARM9I, 0x4000)[1]))
            (theirs / "twlblowfishtable.bin").unlink()
            self.assertIn("CTRMap extraction lacks twlblowfishtable.bin", ctrmap_dsi.compare(ours, theirs, ARM9I, 0x4000)[1])


class AssembledOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.header = self.tmp / "header.bin"
        self.good = dsi_rom()
        self.header.write_bytes(self.good[:0x1000])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, data: bytes | None) -> list[str]:
        out = self.tmp / "White2Upgrade.nds"
        if out.exists():
            out.unlink()
        if data is not None:
            out.write_bytes(data)
        return dsi.check_assembled_file(out, self.header)

    def test_output_validation(self) -> None:
        self.assertEqual(self.check(self.good), [])
        self.assertIn("ROMBuilder produced no output file", self.check(None)[0])
        self.assertIn("empty", self.check(b"")[0])
        self.assertIn("at least 512", self.check(b"\x00" * 100)[0])
        self.assertTrue(any("structurally invalid" in p for p in self.check(self.good[:0x2000])))
        self.assertTrue(any("DSi-enhanced status differs" in p for p in self.check(ntr_rom())))
        other = bytearray(self.good)
        other[0x0C:0x10] = b"IREO"
        self.assertTrue(any("game code" in p for p in self.check(bytes(other))))
        directory = self.tmp / "dir-output.nds"
        directory.mkdir()
        self.assertIn("not a regular file", dsi.check_assembled_file(directory, self.header)[0])


FAKE_JAVA = """import os, sys
args = sys.argv[1:]
out = args[args.index("--output") + 1]
mode = os.environ["FAKE_ROMBUILDER"]
print("java.io.FileNotFoundException: File __ROM/arm9i.bin does not exist!" if mode != "valid" else "built")
if mode == "empty":
    open(out, "wb").close()
elif mode == "small":
    open(out, "wb").write(b"\\x00" * 64)
elif mode == "valid":
    open(out, "wb").write(open(os.environ["GOOD_ROM"], "rb").read())
sys.exit(0)  # CTRMap's CLI reported failure without a non-zero exit
"""
MARKER_SCRIPT = "import pathlib, sys\npathlib.Path(sys.argv[0] + '.ran').write_text('ran')\n"


class CompoundCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "W2U"
        sw.write(self.root, "meson.build", "rom = custom_target(\n" + sw.SYNTHETIC_ROM_COMMAND + ")\n")
        self.good = self.tmp / "good.nds"
        self.good.write_bytes(dsi_rom())
        self.header = self.tmp / "header.bin"
        self.header.write_bytes(self.good.read_bytes()[:0x1000])
        patches.guard_rombuilder(self.root, REPO, self.header)
        text = (self.root / "meson.build").read_text()
        self.command = re.search(r"'-c',\n    '(.*?)',\n    'rombuilder'", text, re.S).group(1)
        self.java = self.tmp / "fake-java"
        self.java.write_text(f"#!/bin/sh\nexec {sys.executable} {self.tmp / 'fake_java.py'} \"$@\"\n")
        self.java.chmod(0o755)
        (self.tmp / "fake_java.py").write_text(FAKE_JAVA)
        self.footer = self.root / "tools" / "footer.py"
        self.finalize = self.root / "tools" / "finalize.py"
        self.footer.write_text(MARKER_SCRIPT)
        self.finalize.write_text(MARKER_SCRIPT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_mode(self, mode: str) -> subprocess.CompletedProcess:
        for marker in (Path(str(self.footer) + ".ran"), Path(str(self.finalize) + ".ran")):
            if marker.exists():
                marker.unlink()
        out = self.tmp / "White2Upgrade.nds"
        if out.exists():
            out.unlink()
        env = {**os.environ, "JAVA": str(self.java), "FAKE_ROMBUILDER": mode, "GOOD_ROM": str(self.good)}
        return subprocess.run(["/bin/sh", "-eu", "-c", self.command, "rombuilder", "ctrmap.jar", "project.cmproj", str(out),
                               str(self.footer), str(self.finalize)], capture_output=True, text=True, env=env)

    def test_footer_and_finalize_never_run_after_failed_assembly(self) -> None:
        for mode in ("missing", "empty", "small"):
            with self.subTest(mode=mode):
                result = self.run_mode(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ROMBuilder output rejected", result.stderr)
                self.assertFalse(Path(str(self.footer) + ".ran").exists(), "footer ran after failed assembly")
                self.assertFalse(Path(str(self.finalize) + ".ran").exists(), "finalize ran after failed assembly")

    def test_valid_assembly_continues_to_footer_and_finalize(self) -> None:
        result = self.run_mode("valid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(str(self.footer) + ".ran").exists())
        self.assertTrue(Path(str(self.finalize) + ".ran").exists())

    def test_guard_anchor_must_be_unique(self) -> None:
        for text in ("no builder here\n", "rom = custom_target(\n" + sw.SYNTHETIC_ROM_COMMAND * 2 + ")\n"):
            root = self.tmp / f"r{len(text)}"
            sw.write(root, "meson.build", text)
            with self.assertRaises(BuildError):
                patches.guard_rombuilder(root, REPO, self.header)


if __name__ == "__main__":
    unittest.main()
