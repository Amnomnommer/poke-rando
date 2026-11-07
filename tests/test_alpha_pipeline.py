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
from b2er import pipeline  # noqa: E402
from b2er.assembly import BuildError, reports, workroot  # noqa: E402
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
        fields = dict(rom=self.white, seed=seed, auxiliary={"w2u": self.checkout},
                      output_dir=self.tmp / f"out {self.counter}", workroot_base=self.tmp / "work", pins_dir=self.pins,
                      species_max=1023, options={"builder": "mock", "patch_pins": sw.patch_pins(self.facts)})
        fields.update(overrides)
        return pipeline.BuildRequest(**fields)

    def assertRefused(self, fragment: str, **overrides) -> None:
        req = self.request(**overrides)
        with self.assertRaises(BuildError) as ctx:
            pipeline.run(req)
        self.assertIn(fragment, str(ctx.exception))
        self.assertFalse(req.output_dir.exists(), "a refused build must not emit outputs")

    def test_end_to_end_success(self) -> None:
        inputs_before = {p: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in (self.white, self.black)}
        checkout_before = tree_state(self.checkout)
        req = self.request()
        result = pipeline.run(req)
        label = result["label"]
        names = sorted(p.name for p in req.output_dir.iterdir())
        self.assertEqual(names, sorted(f"{label}.{s}" for s in ("nds", "manifest.json", "plan.json", "settings.json",
                                                              "verification.json", "spoiler.txt")))
        manifest = json.loads((req.output_dir / f"{label}.manifest.json").read_text())
        rom_bytes = (req.output_dir / f"{label}.nds").read_bytes()
        self.assertEqual(manifest["output"]["sha256"], hashlib.sha256(rom_bytes).hexdigest())
        self.assertEqual(manifest["identity"]["status"], "clean_known")
        self.assertEqual(manifest["identity"]["fingerprint"], "gen5-white2")
        self.assertEqual(manifest["adapter"]["id"], "gen5.white2")
        self.assertEqual(manifest["verification"], {"build_completed": True, "structural_verification": "passed",
                                                    "runtime_acceptance": "not yet performed"})
        self.assertIn("species limited to #1..#1023 by request", manifest["warnings"])
        self.assertIn("hidden_item_randomization", manifest["unsupported_capabilities"])
        for key in ("seed", "settings", "tool", "provenance", "capabilities", "transformations"):
            self.assertIn(key, manifest)
        rom = Rom(rom_bytes)
        self.assertEqual(arm9.classify(rom.arm9()).state, "uncompressed")
        self.assertFalse([p for p in rom.paths() if "MainMenuSkip" in p])
        self.assertIn("patches/White2Upgrade.dll", rom.paths())
        self.assertEqual(rom.integrity_problems(), [])
        checks = {c["name"] for c in result["verification"]["checks"]}
        self.assertTrue({"encounters_match_plan", "trainers_match_plan", "major_battles_doubles", "script_patches_present",
                         "encounter_species_in_range", "arm9_compression_state", "development_plugin_absent"} <= checks)
        for path, (data, mtime, ino) in inputs_before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino), (data, mtime, ino))
        self.assertEqual(tree_state(self.checkout), checkout_before)
        self.assertEqual(list((self.tmp / "work").iterdir()), [])

    def test_outputs_are_sanitized(self) -> None:
        req = self.request()
        result = pipeline.run(req)
        for name in result["outputs"].values():
            if name.endswith(".nds"):
                continue
            text = (req.output_dir / name).read_text()
            for fragment in (str(self.tmp), str(self.tmp.resolve()), "my roms", "W2U checkout"):
                self.assertNotIn(fragment, text, f"{name} leaks {fragment!r}")
            self.assertNotIn("SYNTHTITLE", text)

    def test_byte_identical_rebuild_and_seed_sensitivity(self) -> None:
        a = pipeline.run(self.request("same-seed"))
        b = pipeline.run(self.request("same-seed"))
        c = pipeline.run(self.request("other-seed"))
        self.assertEqual((a["build_key"], a["sha256"]), (b["build_key"], b["sha256"]))
        self.assertNotEqual(a["sha256"], c["sha256"])

    def test_diagnostic_build(self) -> None:
        req = self.request("diag", diagnostic=True)
        result = pipeline.run(req)
        checks = {c["name"]: c for c in result["verification"]["checks"]}
        self.assertEqual(checks["route19_fixture"]["status"], "pass")
        rom = Rom((req.output_dir / result["outputs"]["rom"]).read_bytes())
        record = Narc.parse(rom.read_path("a/1/2/7")).members[2]
        self.assertEqual(enc.verify_route19_fixture(record), [])
        self.assertEqual({m[2] for m in Narc.parse(rom.read_path("a/0/9/1")).members}, {0})
        self.assertIn("diagnostic", result["label"])

    def test_full_species_range_refused_without_modification(self) -> None:
        self.assertRefused("national_dex_max=1025", species_max=None)
        self.assertEqual(list((self.tmp / "work").iterdir()), [])

    def test_unsupported_feature_refused(self) -> None:
        self.assertRefused("hidden_item_randomization: unavailable",
                           settings_override={"items": {"hidden": "shuffle"}})

    def test_dynamic_capability_refusal(self) -> None:
        bare = self.tmp / "bare white 2.nds"
        files = {"a/0/5/6": Narc([b"\x00"]).build(), "a/1/2/7": Narc([b""]).build(), "a/0/9/1": Narc([b""]).build(),
                 "a/0/9/2": Narc([b""]).build()}
        bare.write_bytes(sw.build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), files=files))
        pins = sw.make_pins(self.tmp / "bare pins", self.facts, bare, self.black)
        req = self.request(rom=bare, pins_dir=pins)
        with self.assertRaises(BuildError) as ctx:
            pipeline.run(req)
        self.assertIn("starter_randomization: unavailable", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())
        for path in (self.tmp / "work").iterdir():
            workroot.remove(path, self.tmp / "work")

    def test_modified_and_unsupported_roms_refused(self) -> None:
        modified = self.tmp / "modified.nds"
        data = bytearray(self.white.read_bytes())
        data[-1] ^= 0xFF
        modified.write_bytes(bytes(data))
        self.assertRefused("recognized_modified", rom=modified)
        self.assertRefused("support=recognized", rom=self.black)  # clean and recognized, but its release is not buildable
        junk = self.tmp / "junk.bin"
        junk.write_bytes(b"\x00" * 4096)
        self.assertRefused("status=unknown", rom=junk)

    def test_preflight_aggregates_problems(self) -> None:
        bad_settings = self.tmp / "settings.json"
        bad_settings.write_text(json.dumps({"trainers": {"keep_type_themes": True}}))
        stray = self.checkout / "stray.txt"
        stray.write_text("untracked")
        try:
            req = self.request("", settings_path=bad_settings, auxiliary={})
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        finally:
            stray.unlink()
        message = str(ctx.exception)
        for fragment in ("--seed", "keep_type_themes", "missing required input --w2u"):
            self.assertIn(fragment, message)

    def test_single_rom_selects_white2_without_black2(self) -> None:
        req = self.request("single")
        result = pipeline.run(req)
        manifest = result["manifest"]
        self.assertEqual((manifest["adapter"]["id"], manifest["identity"]["fingerprint"]), ("gen5.white2", "gen5-white2"))
        self.assertEqual(manifest["provenance"]["auxiliary_sha256"], {})
        self.assertEqual(result["verification"]["structural_verification"], "passed")

    def test_optional_black2_is_verified_when_supplied(self) -> None:
        result = pipeline.run(self.request("with-b2", auxiliary={"w2u": self.checkout, "black2": self.black}))
        self.assertEqual(set(result["manifest"]["provenance"]["auxiliary_sha256"]), {"black2"})
        modified = self.tmp / "modified black 2.nds"
        data = bytearray(self.black.read_bytes())
        data[-1] ^= 1
        modified.write_bytes(bytes(data))
        self.assertRefused("auxiliary black2: status=recognized_modified", auxiliary={"w2u": self.checkout, "black2": modified})
        self.assertRefused("does not take inputs", auxiliary={"w2u": self.checkout, "bogus": self.black})

    def test_refusals_happen_before_workspace_creation(self) -> None:
        platinum = self.tmp / "platinum.nds"
        platinum.write_bytes(sw.build_rom(game_code=b"CPUE", arm9=arm9.synthetic_arm9(), files={"x": b"y"}))
        modified = self.tmp / "modified white 2.nds"
        data = bytearray(self.white.read_bytes())
        data[100000 % len(data)] ^= 1
        modified.write_bytes(bytes(data))
        unknown = self.tmp / "unknown.bin"
        unknown.write_bytes(b"\x00" * 8192)
        for rom, fragment in ((self.black, "support=recognized"), (platinum, "recognized_unverified"),
                              (modified, "recognized_modified"), (unknown, "status=unknown")):
            with self.subTest(fragment=fragment):
                base = self.tmp / f"never created {fragment}"
                before = rom.read_bytes()
                req = self.request(rom=rom, workroot_base=base)
                with self.assertRaises(BuildError) as ctx:
                    pipeline.run(req)
                self.assertIn(fragment, str(ctx.exception))
                self.assertFalse(base.exists(), "no workspace may be created for a refused ROM")
                self.assertFalse(req.output_dir.exists())
                self.assertEqual(rom.read_bytes(), before)

    def test_black2_target_guard(self) -> None:
        from b2er.assembly import builders
        original = builders.MockBuilder.build

        def build_and_run_black2_target(self_builder, workroot_path, w2u):
            out = original(self_builder, workroot_path, w2u)
            (w2u / "build-alpha" / "black2upgrade-data.tar.gz").write_bytes(b"synthetic")
            return out

        builders.MockBuilder.build = build_and_run_black2_target
        try:
            self.assertRefused("a Black 2 package target ran")
        finally:
            builders.MockBuilder.build = original
        for path in (self.tmp / "work").iterdir():
            workroot.remove(path, self.tmp / "work")

    def test_cli_single_rom_argument(self) -> None:
        cli = [sys.executable, "-I", str(REPO_ROOT / "scripts" / "b2er_cli.py"), "--pins-dir", str(self.pins), "build"]
        for extra in ([], [str(self.white), "--rom", str(self.white)]):
            out = subprocess.run(cli + extra + ["--seed", "x"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 2, out.stderr)
        out = subprocess.run(cli + [str(self.black), "--seed", "x", "--w2u", str(self.checkout), "--species-max", "1023",
                                    "--builder", "mock", "--workroot-base", str(self.tmp / "cli never")],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("support=recognized", out.stderr)
        self.assertFalse((self.tmp / "cli never").exists())

    def test_dirty_checkout_rejected(self) -> None:
        stray = self.checkout / "stray.txt"
        stray.write_text("untracked")
        try:
            self.assertRefused("untracked_file")
        finally:
            stray.unlink()

    def test_failure_keeps_workroot_and_writes_no_outputs(self) -> None:
        req = self.request(options={"builder": "mock", "patch_pins": {**sw.patch_pins(self.facts), "pmc_arm9": "0" * 64}})
        with self.assertRaises(BuildError):
            pipeline.run(req)
        self.assertFalse(req.output_dir.exists())
        kept = list((self.tmp / "work").iterdir())
        self.assertTrue(kept)
        for path in kept:
            workroot.remove(path, self.tmp / "work")

    def test_existing_outputs_not_overwritten(self) -> None:
        req = self.request("collide")
        pipeline.run(req)
        with self.assertRaises(FileExistsError):
            pipeline.run(self.request("collide", output_dir=req.output_dir))
        for path in (self.tmp / "work").iterdir():
            workroot.remove(path, self.tmp / "work")

    def test_cli_identify_and_capabilities(self) -> None:
        cli = [sys.executable, "-I", str(REPO_ROOT / "scripts" / "b2er_cli.py"), "--pins-dir", str(self.pins)]
        out = subprocess.run(cli + ["identify", str(self.white), "--json"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        report = json.loads(out.stdout)
        self.assertEqual((report["status"], report["adapter"], report["generation"]), ("clean_known", "gen5.white2", 5))
        out = subprocess.run(cli + ["identify", str(self.black), "--json"], capture_output=True, text=True)
        self.assertEqual(json.loads(out.stdout)["adapter"], "gen5.black2")
        junk = self.tmp / "cli junk.bin"
        junk.write_bytes(b"\x00" * 4096)
        out = subprocess.run(cli + ["identify", str(junk)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("unknown", out.stdout)
        out = subprocess.run(cli + ["capabilities", "--json"], capture_output=True, text=True)
        matrix = json.loads(out.stdout)
        white2 = next(t for t in matrix["nds-gen5"]["titles"] if t["fingerprint"] == "gen5-white2")
        self.assertEqual(white2["capabilities"]["wild_randomization"]["level"], "implemented")
        self.assertEqual(white2["title_status"], "buildable")
        self.assertEqual(report["support_status"], "buildable")


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
        self.assertIn("--rom", result.stdout)
        result = subprocess.run(["bash", str(REPO_ROOT / "Build-Alpha-0.1.sh")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
