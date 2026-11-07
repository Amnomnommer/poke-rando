"""Synthetic tests for scripts/verify_rom_inputs.py and b2er.rom_inputs.

Inputs are small files generated at runtime with a placeholder title, a game
code at 0x0C, and filler bytes. Pin files are temporary and contain only the
hashes of those synthetic files. No ROM bytes or real ROM hashes are used as
fixture content.
"""

from __future__ import annotations

import copy
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
CLI = REPO_ROOT / "scripts" / "verify_rom_inputs.py"
sys.path.insert(0, str(REPO_ROOT))

from b2er import rom_inputs  # noqa: E402

MARKER = b"SYNTHETIC-INPUT-BYTES-MARKER"


def synthetic_rom(code: bytes, label: bytes, size: int = 4096) -> bytes:
    """Placeholder title, game code at 0x0C, then deterministic filler containing MARKER."""
    header = b"SYNTHTITLE\x00\x00" + code
    filler = (MARKER + label) * (size // (len(MARKER) + len(label)) + 1)
    return (header + filler)[:size]


def pin_entry(pin_id: str, code: str, data: bytes) -> dict:
    return {
        "id": pin_id,
        "title": f"synthetic {pin_id}",
        "region": "synthetic",
        "game_code": code,
        "role": "target" if pin_id == "white2" else "required-by-upstream",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": {"status": "unresolved", "value": None, "reason": f"synthetic unresolved {pin_id} size."},
        "evidence": "synthetic test fixture",
    }


def tree_state(root: Path) -> dict:
    """Mode, size, mtime, ctime, inode, and content hash of the directory and everything in it (atime excluded)."""
    state = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        entry = (info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_nlink)
        if stat.S_ISREG(info.st_mode):
            entry += (hashlib.sha256(path.read_bytes()).hexdigest(),)
        state[str(path.relative_to(root.parent))] = entry
    return state


class InputCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.inputs_dir = self.tmp / "inputs"
        self.inputs_dir.mkdir()
        self.white2_bytes = synthetic_rom(b"IRDO", b"-white")
        self.black2_bytes = synthetic_rom(b"IREO", b"-black", size=5000)
        self.white2 = self.write_input("white2.dat", self.white2_bytes)
        self.black2 = self.write_input("black2.dat", self.black2_bytes)
        self.pins = {
            "schema_version": 1,
            "description": "synthetic",
            "inputs": [pin_entry("white2", "IRDO", self.white2_bytes), pin_entry("black2", "IREO", self.black2_bytes)],
        }
        self.pins_path = self.tmp / "pins.json"
        self.save_pins()

    def tearDown(self) -> None:
        for path in self.tmp.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        self._tmp.cleanup()

    def write_input(self, name: str, data: bytes) -> Path:
        path = self.inputs_dir / name
        path.write_bytes(data)
        return path

    def save_pins(self, raw: bytes | None = None) -> None:
        self.pins_path.write_bytes(raw if raw is not None else json.dumps(self.pins).encode())

    def run_cli(self, white2: Path | None = None, black2: Path | None = None, *, json_output: bool = True, extra=()):
        args = [sys.executable, "-I", str(CLI), "--white2", str(white2 or self.white2), "--black2",
                str(black2 or self.black2), "--pins", str(self.pins_path), *extra]
        if json_output:
            args.append("--json")
        result = subprocess.run(args, capture_output=True, check=False)
        report = json.loads(result.stdout) if json_output and result.stdout else None
        return result, report

    def verify(self, expected_exit: int, white2: Path | None = None, black2: Path | None = None) -> dict:
        result, report = self.run_cli(white2, black2)
        self.assertEqual(result.returncode, expected_exit, (result.stdout + result.stderr).decode())
        self.assertEqual(report["exit_code"], expected_exit)
        return report

    def categories(self, report: dict) -> set[tuple[str, str]]:
        return {(item["role"], item["category"]) for item in report["defects"]}


class VerificationTests(InputCase):
    def test_both_inputs_verify(self) -> None:
        report = self.verify(0)
        self.assertEqual(report["result"], "verified")
        self.assertEqual(report["defects"], [])
        white, black = report["inputs"]
        self.assertEqual((white["role"], white["status"], white["observed"]["game_code"]), ("white2", "pass", "IRDO"))
        self.assertEqual((black["role"], black["status"], black["observed"]["game_code"]), ("black2", "pass", "IREO"))
        self.assertEqual(white["observed"]["sha256"], hashlib.sha256(self.white2_bytes).hexdigest())
        self.assertEqual(black["observed"]["size_bytes"], 5000)

    def test_unresolved_size_is_a_warning_and_evidence_only(self) -> None:
        report = self.verify(0)
        self.assertEqual(
            [(w["category"], w["subject"]) for w in report["warnings"]],
            [("unresolved_pin", "/inputs/white2/size_bytes"), ("unresolved_pin", "/inputs/black2/size_bytes")],
        )
        self.assertEqual(report["inputs"][0]["observed"]["size_bytes"], len(self.white2_bytes))

    def test_resolved_size_is_enforced(self) -> None:
        self.pins["inputs"][0]["size_bytes"] = {"status": "resolved", "value": len(self.white2_bytes) + 1, "reason": "synthetic"}
        self.save_pins()
        report = self.verify(1)
        self.assertEqual(self.categories(report), {("white2", rom_inputs.WRONG_SIZE)})

    def test_wrong_sha256(self) -> None:
        self.write_input("white2.dat", synthetic_rom(b"IRDO", b"-other"))
        report = self.verify(1)
        self.assertEqual(self.categories(report), {("white2", rom_inputs.WRONG_HASH)})

    def test_swapped_paths(self) -> None:
        report = self.verify(1, white2=self.black2, black2=self.white2)
        self.assertEqual(
            self.categories(report),
            {(role, category) for role in ("white2", "black2") for category in (rom_inputs.WRONG_HASH, rom_inputs.WRONG_GAME_CODE)},
        )
        self.assertTrue(any("appear swapped" in d["detail"] for d in report["defects"]))

    def test_wrong_game_code_with_matching_hash(self) -> None:
        wrong = synthetic_rom(b"ZZZZ", b"-white")
        self.write_input("white2.dat", wrong)
        self.pins["inputs"][0]["sha256"] = hashlib.sha256(wrong).hexdigest()
        self.save_pins()
        report = self.verify(1)
        self.assertEqual(self.categories(report), {("white2", rom_inputs.WRONG_GAME_CODE)})
        self.assertEqual(report["inputs"][0]["observed"]["game_code"], "ZZZZ")

    def test_truncated_header(self) -> None:
        short = b"SYNTHTITLE\x00\x00IR"
        self.write_input("white2.dat", short)
        self.pins["inputs"][0]["sha256"] = hashlib.sha256(short).hexdigest()
        self.save_pins()
        report = self.verify(1)
        self.assertEqual(self.categories(report), {("white2", rom_inputs.TRUNCATED_HEADER)})
        self.assertIsNone(report["inputs"][0]["observed"]["game_code"])

    def test_empty_file(self) -> None:
        self.write_input("white2.dat", b"")
        report = self.verify(1)
        self.assertIn(("white2", rom_inputs.TRUNCATED_HEADER), self.categories(report))

    def test_directory_rejected(self) -> None:
        directory = self.inputs_dir / "a directory"
        directory.mkdir()
        report = self.verify(1, white2=directory)
        self.assertEqual(self.categories(report), {("white2", rom_inputs.NOT_REGULAR_FILE)})
        self.assertEqual(report["inputs"][0]["observed"]["file_type"], "directory")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs unsupported")
    def test_fifo_rejected_without_blocking(self) -> None:
        fifo = self.inputs_dir / "pipe"
        os.mkfifo(fifo)
        report = self.verify(1, black2=fifo)
        self.assertEqual(self.categories(report), {("black2", rom_inputs.NOT_REGULAR_FILE)})
        self.assertEqual(report["inputs"][1]["observed"]["file_type"], "FIFO")

    def test_symlink_rejected(self) -> None:
        link = self.inputs_dir / "link.dat"
        os.symlink(self.white2, link)
        report = self.verify(1, white2=link)
        self.assertEqual(self.categories(report), {("white2", rom_inputs.NOT_REGULAR_FILE)})
        self.assertEqual(report["inputs"][0]["observed"]["file_type"], "symlink")

    def test_symlink_swapped_in_after_lstat_is_refused_by_open(self) -> None:
        real_lstat = os.lstat
        target = str(os.path.abspath(self.white2))
        moved = self.inputs_dir / "moved.dat"

        def racing_lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if os.fspath(path) == target and not moved.exists():
                os.rename(target, moved)
                os.symlink(moved, target)
            return info

        rom_inputs.os.lstat = racing_lstat
        try:
            result = rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path)
        finally:
            rom_inputs.os.lstat = real_lstat
        self.assertIn(
            ("white2", rom_inputs.NOT_REGULAR_FILE, "input became a symlink before it was opened"),
            {(d["role"], d["category"], d["detail"]) for d in result.defects},
        )

    def test_same_file_for_both_roles(self) -> None:
        report = self.verify(1, white2=self.white2, black2=self.white2)
        self.assertIn(("both", rom_inputs.DUPLICATE_IDENTITY), self.categories(report))

    def test_hard_link_for_both_roles(self) -> None:
        link = self.inputs_dir / "hardlink.dat"
        os.link(self.white2, link)
        report = self.verify(1, white2=self.white2, black2=link)
        duplicate = [d for d in report["defects"] if d["category"] == rom_inputs.DUPLICATE_IDENTITY]
        self.assertEqual(len(duplicate), 1)
        self.assertIn("hard link", duplicate[0]["detail"])

    def test_paths_containing_spaces(self) -> None:
        spaced = self.inputs_dir / "my clean roms" / "White 2 (clean).dat"
        spaced.parent.mkdir()
        spaced.write_bytes(self.white2_bytes)
        report = self.verify(0, white2=spaced)
        self.assertEqual(report["inputs"][0]["path"], str(spaced))

    def test_reports_contain_no_input_bytes(self) -> None:
        self.write_input("white2.dat", synthetic_rom(b"\x01\x02\x03\x04", b"-x"))
        for json_output in (True, False):
            result, _ = self.run_cli(json_output=json_output)
            output = result.stdout + result.stderr
            self.assertNotIn(MARKER, output)
            self.assertNotIn(b"SYNTHTITLE", output)
            self.assertNotIn(b"\x01\x02\x03\x04", output)

    def test_deterministic_output(self) -> None:
        self.write_input("black2.dat", b"short")
        for json_output in (True, False):
            first, report = self.run_cli(json_output=json_output)
            second, _ = self.run_cli(json_output=json_output)
            self.assertEqual(first.returncode, 1)
            self.assertEqual(first.stdout, second.stdout)
            if json_output:
                self.assertEqual(first.stdout.decode(), json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
                self.assertIn("local_only", report)

    def test_verification_leaves_files_and_directories_unchanged(self) -> None:
        os.link(self.white2, self.inputs_dir / "extra-link.dat")
        before = tree_state(self.inputs_dir)
        self.run_cli()
        self.run_cli(json_output=False)
        rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path)
        self.assertEqual(tree_state(self.inputs_dir), before)

    def test_read_only_inputs_and_directory_verify(self) -> None:
        for path in (self.white2, self.black2):
            path.chmod(0o444)
        self.inputs_dir.chmod(0o555)
        self.verify(0)


class ChangedDuringReadTests(InputCase):
    def changed(self, mutate) -> rom_inputs.InputVerification:
        def after_chunk(role, index):
            if role == "white2" and index == 0:
                mutate()
        return rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path, chunk_size=256, after_chunk=after_chunk)

    def assertChanged(self, result: rom_inputs.InputVerification) -> None:
        self.assertFalse(result.verified)
        self.assertIn(("white2", rom_inputs.CHANGED_DURING_READ), {(d["role"], d["category"]) for d in result.defects})

    def test_append_during_read(self) -> None:
        def append():
            with self.white2.open("ab") as handle:
                handle.write(b"appended")
        self.assertChanged(self.changed(append))

    def test_same_size_rewrite_with_restored_mtime(self) -> None:
        info = self.white2.stat()

        def rewrite():
            with self.white2.open("r+b") as handle:
                handle.seek(1000)
                handle.write(b"X")
            os.utime(self.white2, ns=(info.st_atime_ns, info.st_mtime_ns))  # ctime still changes
        self.assertChanged(self.changed(rewrite))

    def test_path_replaced_during_read(self) -> None:
        def replace():
            replacement = self.inputs_dir / "replacement.dat"
            replacement.write_bytes(self.white2_bytes)
            os.replace(replacement, self.white2)
        self.assertChanged(self.changed(replace))

    def test_unchanged_read_with_hook_verifies(self) -> None:
        self.assertTrue(self.changed(lambda: None).verified)


class LibraryApiTests(InputCase):
    def test_sequential_pre_and_post_calls_compare_equal(self) -> None:
        before = rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path)
        after = rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path)
        self.assertTrue(before.verified and after.verified)
        self.assertEqual(before.fingerprint(), after.fingerprint())
        self.assertEqual(rom_inputs.compare_fingerprints(before, after), [])
        self.assertEqual(before.report(), after.report())

    def test_sequential_calls_detect_a_change_between_them(self) -> None:
        before = rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path)
        replacement = self.inputs_dir / "replacement.dat"
        replacement.write_bytes(self.white2_bytes)  # identical bytes, different file
        os.replace(replacement, self.white2)
        after = rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path)
        self.assertTrue(after.verified)  # content is still approved...
        differences = rom_inputs.compare_fingerprints(before, after)
        self.assertIn("white2.inode changed", differences)  # ...but it is not the same file

    def test_fingerprint_contains_no_bytes(self) -> None:
        result = rom_inputs.verify_inputs(self.white2, self.black2, self.pins_path)
        self.assertNotIn(MARKER.decode(), json.dumps(result.fingerprint()))


class OperationalErrorTests(InputCase):
    def assertOperationalError(self, fragment: str, **kwargs) -> None:
        result, report = self.run_cli(**kwargs)
        self.assertEqual(result.returncode, 2, (result.stdout + result.stderr).decode())
        self.assertEqual(report["result"], "error")
        self.assertIn(fragment, report["error"])

    def test_missing_path(self) -> None:
        self.assertOperationalError("does not exist", white2=self.inputs_dir / "missing.dat")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses file permissions")
    def test_unreadable_path(self) -> None:
        self.white2.chmod(0)
        self.assertOperationalError("cannot open input", white2=self.white2)

    def test_malformed_and_duplicate_pins(self) -> None:
        good = copy.deepcopy(self.pins)

        def variant(mutator) -> bytes:
            pins = copy.deepcopy(good)
            mutator(pins)
            return json.dumps(pins).encode()

        cases = {
            "not json": (b"{ nope", "not valid UTF-8 JSON"),
            "duplicate json key": (b'{"schema_version": 1, "schema_version": 1}', "duplicate JSON keys"),
            "schema": (variant(lambda p: p.update(schema_version=2)), "schema_version must be 1"),
            "unknown key": (variant(lambda p: p.update(extra=1)), "unknown top-level keys"),
            "duplicate id": (variant(lambda p: p["inputs"].__setitem__(1, dict(p["inputs"][0]))), "duplicate input id"),
            "duplicate code": (variant(lambda p: p["inputs"][1].update(game_code="IRDO")), "duplicate game code"),
            "duplicate hash": (variant(lambda p: p["inputs"][1].update(sha256=p["inputs"][0]["sha256"])), "duplicate sha256"),
            "bad hash": (variant(lambda p: p["inputs"][0].update(sha256="ABC")), "sha256 must be"),
            "bad code": (variant(lambda p: p["inputs"][0].update(game_code="irdo")), "game_code must be"),
            "wrong role code": (
                variant(lambda p: (p["inputs"][0].update(game_code="IREX"))), "must have game code 'IRDO'"
            ),
            "missing role": (variant(lambda p: p["inputs"].pop()), "inputs must be exactly"),
            "size not status": (variant(lambda p: p["inputs"][0].update(size_bytes=123)), "status field"),
            "unresolved with value": (
                variant(lambda p: p["inputs"][0]["size_bytes"].update(value=1)), "null value"
            ),
            "resolved bool": (
                variant(lambda p: p["inputs"][0].update(size_bytes={"status": "resolved", "value": True, "reason": "x"})),
                "positive integer",
            ),
            "missing key": (variant(lambda p: p["inputs"][0].pop("sha256")), "missing keys"),
        }
        for label, (raw, fragment) in cases.items():
            with self.subTest(case=label):
                self.save_pins(raw)
                self.assertOperationalError(fragment)

    def test_missing_pin_file(self) -> None:
        self.pins_path.unlink()
        self.assertOperationalError("cannot read pin file")

    def test_invalid_arguments(self) -> None:
        for args in ([], ["--white2", str(self.white2)], ["--black2", str(self.black2)]):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-I", str(CLI), *args], capture_output=True, check=False)
                self.assertEqual(result.returncode, 2)

    def test_repository_pins_parse(self) -> None:
        pins = rom_inputs.load_input_pins(REPO_ROOT / "pins" / "inputs.json")
        self.assertEqual({role: pin.game_code for role, pin in pins.items()}, {"white2": "IRDO", "black2": "IREO"})
        self.assertTrue(all(pin.size_bytes is None for pin in pins.values()))


if __name__ == "__main__":
    unittest.main()
