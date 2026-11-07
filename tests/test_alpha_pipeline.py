"""Mock end-to-end Alpha 0.1 builds on synthetic inputs, plus workroot, report, launcher, and scanner tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_w2u as sw  # noqa: E402
from b2er import emulator, sentinels  # noqa: E402
from b2er.assembly import BuildError, pipeline, reports, workroot  # noqa: E402
from b2er.gamedata import encounters as enc  # noqa: E402
from b2er.nds import arm9  # noqa: E402
from b2er.nds.narc import Narc  # noqa: E402
from b2er.nds.rom import Rom  # noqa: E402


def tree_state(root: Path) -> dict:
    state = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            info = path.lstat()
            entry = (info.st_mode, info.st_size, info.st_mtime_ns)
            if stat.S_ISREG(info.st_mode):
                entry += (hashlib.sha256(path.read_bytes()).hexdigest(),)
            state[str(path.relative_to(root))] = entry
    return state


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.checkout = cls.tmp / "W2U checkout"
        cls.facts = sw.make_checkout(cls.checkout)
        cls.white, cls.black = sw.make_roms(cls.tmp / "my roms")
        cls.pins = sw.make_pins(cls.tmp / "pins", cls.facts, cls.white, cls.black)
        cls.counter = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def request(self, seed: str = "example-seed", **overrides) -> pipeline.BuildRequest:
        type(self).counter += 1
        n = self.counter
        fields = dict(white2=self.white, black2=self.black, w2u=self.checkout, seed=seed,
                      output_dir=self.tmp / f"out {n}", workroot_base=self.tmp / "work", builder="mock",
                      pins_dir=self.pins, patch_pins=sw.patch_pins(self.facts))
        fields.update(overrides)
        return pipeline.BuildRequest(**fields)

    def test_end_to_end_success(self) -> None:
        inputs_before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in (self.white, self.black)}
        checkout_before = tree_state(self.checkout)
        req = self.request()
        result = pipeline.run(req)
        out = req.output_dir
        names = sorted(p.name for p in out.iterdir())
        label = result["label"]
        self.assertEqual(names, sorted(f"{label}.{s}" for s in ("nds", "manifest.json", "plan.json", "settings.json",
                                                              "verification.json", "spoiler.txt")))
        manifest = json.loads((out / f"{label}.manifest.json").read_text())
        rom_bytes = (out / f"{label}.nds").read_bytes()
        self.assertEqual(manifest["output"]["sha256"], hashlib.sha256(rom_bytes).hexdigest())
        self.assertEqual(manifest["verification"], {"build_completed": True, "structural_verification": "passed",
                                                    "runtime_acceptance": "not yet performed"})
        self.assertFalse(manifest["species_model"]["requirement_met"])
        self.assertEqual(manifest["species_model"]["missing_required_national_dex"], [1024, 1025])
        for item in manifest["features"].values():
            self.assertFalse(str(item).startswith("skipped"), manifest["features"])
        # Finished ROM properties.
        rom = Rom(rom_bytes)
        self.assertEqual(arm9.classify(rom.arm9()).state, "uncompressed")
        self.assertFalse([p for p in rom.paths() if "MainMenuSkip" in p])
        self.assertIn("patches/White2Upgrade.dll", rom.paths())
        self.assertEqual(rom.integrity_problems(), [])
        # Inputs and source checkout untouched; workroot removed.
        for path, (data, mtime, ino) in inputs_before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino), (data, mtime, ino))
        self.assertEqual(tree_state(self.checkout), checkout_before)
        self.assertEqual(list((self.tmp / "work").iterdir()), [])

    def test_outputs_are_sanitized(self) -> None:
        req = self.request()
        result = pipeline.run(req)
        local = [str(self.tmp), str(self.tmp.resolve()), "my roms", "W2U checkout"]
        for name in result["outputs"].values():
            if name.endswith(".nds"):
                continue
            text = (req.output_dir / name).read_text()
            for fragment in local:
                self.assertNotIn(fragment, text, f"{name} leaks {fragment!r}")
            self.assertNotIn("SYNTHTITLE", text)

    def test_reproducible_and_seed_sensitive(self) -> None:
        a = pipeline.run(self.request("same-seed"))
        b = pipeline.run(self.request("same-seed"))
        c = pipeline.run(self.request("other-seed"))
        self.assertEqual((a["build_key"], a["sha256"]), (b["build_key"], b["sha256"]))
        self.assertNotEqual(a["sha256"], c["sha256"])
        self.assertNotEqual(a["build_key"], c["build_key"])

    def test_diagnostic_build(self) -> None:
        req = self.request("diag", diagnostic=True)
        result = pipeline.run(req)
        checks = {c["name"]: c for c in result["verification"]["checks"]}
        self.assertEqual(checks["route19_fixture"]["status"], "pass")
        rom = Rom((req.output_dir / result["outputs"]["rom"]).read_bytes())
        record = Narc.parse(rom.read_path("a/1/2/7")).members[2]
        self.assertEqual(len(record), 928)
        self.assertEqual(enc.verify_route19_fixture(record), [])
        trdata = Narc.parse(rom.read_path("a/0/9/1")).members
        self.assertEqual({m[2] for m in trdata}, {0})  # no Doubles conversion in diagnostic mode
        self.assertIn("Route19-Diagnostic", result["label"])

    def test_preflight_aggregates_problems(self) -> None:
        bad_rom = self.tmp / "not a rom.nds"
        bad_rom.write_bytes(b"\x00" * 64)
        bad_settings = self.tmp / "settings.json"
        bad_settings.write_text(json.dumps({"trainers": {"keep_type_themes": True}}))
        with self.assertRaises(BuildError) as ctx:
            pipeline.run(self.request("", white2=bad_rom, settings_path=bad_settings))
        message = str(ctx.exception)
        for fragment in ("--seed", "keep_type_themes", "clean ROM white2"):
            self.assertIn(fragment, message)

    def test_dirty_checkout_rejected(self) -> None:
        stray = self.checkout / "stray.txt"
        stray.write_text("untracked")
        try:
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(self.request())
            self.assertIn("untracked_file", str(ctx.exception))
        finally:
            stray.unlink()

    def test_swapped_inputs_rejected(self) -> None:
        with self.assertRaises(BuildError) as ctx:
            pipeline.run(self.request(white2=self.black, black2=self.white))
        self.assertIn("wrong_hash", str(ctx.exception))

    def test_failure_keeps_workroot_and_writes_no_outputs(self) -> None:
        req = self.request(patch_pins={**sw.patch_pins(self.facts), "pmc_arm9": "0" * 64})
        with self.assertRaises(BuildError):
            pipeline.run(req)
        self.assertFalse(req.output_dir.exists())
        kept = [p for p in (self.tmp / "work").iterdir()]
        self.assertTrue(kept)
        for path in kept:
            workroot.remove(path, self.tmp / "work")

    def test_existing_outputs_not_overwritten(self) -> None:
        req = self.request("collide")
        pipeline.run(req)
        again = self.request("collide", output_dir=req.output_dir)
        with self.assertRaises(FileExistsError):
            pipeline.run(again)
        for path in (self.tmp / "work").iterdir():
            workroot.remove(path, self.tmp / "work")


class WorkrootAndReportTests(unittest.TestCase):
    def test_remove_requires_marker_and_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            root = workroot.create(base, "label")
            (root / "ro").mkdir()
            (root / "ro" / "f").write_text("x")
            (root / "ro").chmod(0o500)
            unmarked = base / "unmarked"
            unmarked.mkdir()
            with self.assertRaises(BuildError):
                workroot.remove(unmarked, base)
            with self.assertRaises(BuildError):
                workroot.remove(root, Path(tmp))
            workroot.remove(root, base)
            self.assertFalse(root.exists())
            self.assertTrue(unmarked.exists())

    def test_sanitizer(self) -> None:
        reports.assert_sanitized({"a": ["SPECIES_X", "relative/path"]}, ["/home/someone"])
        for bad in ({"a": "/abs/path"}, {"a": "C:\\Users\\x"}, {"k": "see /home/someone/file"}):
            with self.assertRaises(ValueError):
                reports.assert_sanitized(bad, ["/home/someone"])


class ToolTests(unittest.TestCase):
    def test_sentinel_scan(self) -> None:
        image = bytearray(0x100)
        image[0x10:0x14] = (1024).to_bytes(4, "little")
        image[0x40:0x46] = bytes([0x01, 0x23, 0x9B, 0x02, 0x01, 0x33])  # movs r3,#1; lsls r3,r3,#10; adds r3,#1
        symbols = sentinels.load_symbols("  - Name: PML_Test\n    Segment: 0x0\n    Address: 0x2000001\n")
        found = sentinels.scan(bytes(image), symbols=symbols)
        self.assertIn((0x02000010, "literal word", 1024), [(c.address, c.kind, c.value) for c in found])
        self.assertIn((0x02000040, "thumb movs+lsls+adds", 1025), [(c.address, c.kind, c.value) for c in found])
        self.assertTrue(all(c.symbol.startswith("PML_Test") for c in found))
        self.assertIn("Bad Egg", sentinels.report(found))

    def test_log_scan(self) -> None:
        text = "boot ok\nARM9: data abort (0203A086)\nsomething\nprefetch abort at 0x0\n"
        self.assertEqual(len(emulator.scan_log(text)), 2)
        self.assertEqual(emulator.scan_log("normal boot\n"), [])
        self.assertIn("target remote localhost:3333", emulator.gdb_script())

    def test_smoke_with_fake_emulator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rom = tmp / "game.nds"
            rom.write_bytes(b"synthetic")
            fake = tmp / "fake_emu.py"
            fake.write_text("import sys,time\nprint('ARM9: data abort (0203A086)', flush=True)\ntime.sleep(30)\n")
            result = emulator.launch(rom, tmp / "logs", smoke_seconds=2, emulator=f"{sys.executable} {fake}")
            self.assertEqual(result.status, "fault")
            self.assertIn("NOT gameplay", result.summary())
            quiet = tmp / "quiet.py"
            quiet.write_text("import time\nprint('booting', flush=True)\ntime.sleep(30)\n")
            ok = emulator.launch(rom, tmp / "logs", smoke_seconds=2, emulator=f"{sys.executable} {quiet}")
            self.assertEqual(ok.status, "no-fault-observed")

    def test_cli_entry_points(self) -> None:
        result = subprocess.run([sys.executable, "-I", str(REPO_ROOT / "scripts" / "launch_alpha.py"), "/nonexistent.nds"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        result = subprocess.run(["bash", str(REPO_ROOT / "Build-Alpha-0.1.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--white2", result.stdout)
        result = subprocess.run(["bash", str(REPO_ROOT / "Build-Alpha-0.1.sh")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
