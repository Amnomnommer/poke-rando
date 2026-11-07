"""Synthetic tests for scripts/check-repository-safety.sh.

Every fixture is generated at runtime from synthetic bytes in a throwaway Git
repository. No ROM, extracted asset, or upstream file is used or committed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD = REPO_ROOT / "scripts" / "check-repository-safety.sh"
SIZE_LIMIT = 64 * 1024


def synthetic_ds_header(game_code: bytes) -> bytes:
    """A 0x200-byte header with a placeholder title and the given game code."""
    header = bytearray(0x200)
    header[0x00:0x0C] = b"SYNTHETICTST"
    header[0x0C:0x10] = game_code
    header[0x10:0x12] = b"00"
    return bytes(header)


def synthetic_nitro(magic: bytes) -> bytes:
    """Magic, byte-order mark, and filler: the shape of a Nitro file header only."""
    return magic + b"\xff\xfe\x00\x01" + bytes(24)


class GuardRepo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def add(self, relative: str, data: bytes) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        # -f: forbidden paths are also .gitignored; the guard must still catch a forced add.
        self.git("add", "-f", "--", relative)

    def run_guard(self) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run(
            ["bash", str(GUARD)], cwd=self.root, capture_output=True, text=True, env=env, check=False
        )


class RepositorySafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = GuardRepo(Path(self._tmp.name))
        self.repo.add("README.md", b"# benign\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assertPasses(self) -> None:
        result = self.repo.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("passed", result.stdout)

    def assertRejects(self, relative: str, data: bytes, expected_reason: str) -> None:
        self.repo.add(relative, data)
        result = self.repo.run_guard()
        self.assertEqual(result.returncode, 1, f"{relative!r} was not rejected:\n{result.stdout}{result.stderr}")
        self.assertIn(repr(relative), result.stderr)
        self.assertIn(expected_reason, result.stderr)

    def assertRejectsInFreshRepo(self, relative: str, data: bytes, expected_reason: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = self.repo
            self.repo = GuardRepo(Path(tmp))
            self.repo.add("README.md", b"# benign\n")
            try:
                self.assertRejects(relative, data, expected_reason)
            finally:
                self.repo = previous

    # --- current repository and benign content ---------------------------

    def test_current_repository_passes(self) -> None:
        result = subprocess.run(["bash", str(GUARD)], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_benign_text_passes(self) -> None:
        self.repo.add("docs/notes.md", "Patrat #504, level 5 — plain text.\n".encode("utf-8"))
        self.assertPasses()

    def test_small_benign_binary_passes(self) -> None:
        self.repo.add("tests/fixtures/small.bin", bytes(range(256)) * 16)
        self.assertPasses()

    def test_large_text_is_not_size_limited(self) -> None:
        self.repo.add("docs/large.md", b"line of text\n" * (SIZE_LIMIT // 8))
        self.assertPasses()

    def test_empty_file_passes(self) -> None:
        self.repo.add("empty", b"")
        self.assertPasses()

    # --- prohibited paths --------------------------------------------------

    def test_prohibited_directories_fail(self) -> None:
        cases = [
            "dump/personal/0001.toml",
            "vfs/data/a/0/0/4/123",  # extensionless staged NARC member
            "pmc/ARM9PMC.bin",
            "IRDO_Extracted/data/a/0/1/6",
            "IRDO_Extracted_clean/ftc/arm9.txt",
            "IREO_Extracted/data/a/0/1/6",
            "NitroFS/a/0/1/6",
            "build/White2Upgrade.txt",
            "build-stripped/out.txt",
            "output/rom.txt",
            "outputs/rom.txt",
            "cache/key.txt",
            "temp/x.txt",
            "tmp/x.txt",
            "user-data/x.txt",
            "tester/input/x.txt",
            "tester/cache/x.txt",
            "tester/output/x.txt",
            "tester/results/x.txt",
            "nested/deeper/VFS/member",  # any depth, any case
        ]
        for relative in cases:
            with self.subTest(path=relative):
                self.assertRejectsInFreshRepo(relative, b"synthetic\n", "forbidden directory")

    def test_prohibited_extensions_and_names_fail(self) -> None:
        cases = {
            "game.nds": "forbidden extension '.nds'",
            "save.SAV": "forbidden extension '.sav'",
            "archive.narc": "forbidden extension '.narc'",
            "plugin.dll": "forbidden extension '.dll'",
            "ftc/arm9.bin": "forbidden file name 'arm9.bin'",
            "ftc/y9.bin": "forbidden file name 'y9.bin'",
        }
        for relative, reason in cases.items():
            with self.subTest(path=relative):
                self.assertRejectsInFreshRepo(relative, b"synthetic\n", reason)

    # --- content policy -------------------------------------------------------

    def test_each_forbidden_magic_fails_in_both_byte_orders(self) -> None:
        fourccs = [
            "NARC", "NCGR", "NCLR", "NSCR", "NCER", "NANR", "NMCR", "NMAR",
            "NFTR", "SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "STRM",
        ]
        for fourcc in fourccs:
            for magic in (fourcc, fourcc[::-1]):
                with self.subTest(magic=magic):
                    # Extensionless and innocuously named: only the content check can catch it.
                    self.assertRejectsInFreshRepo("assets/member", synthetic_nitro(magic.encode()), f"Nitro {fourcc} magic")

    def test_synthetic_irdo_header_fails(self) -> None:
        self.assertRejects("fixture.dat", synthetic_ds_header(b"IRDO"), "White 2 (IRDO)")

    def test_synthetic_ireo_header_fails(self) -> None:
        self.assertRejects("fixture.dat", synthetic_ds_header(b"IREO"), "Black 2 (IREO)")

    def test_other_game_code_header_passes(self) -> None:
        self.repo.add("fixture.dat", synthetic_ds_header(b"ZZZZ"))
        self.assertPasses()

    def test_binary_at_size_limit_passes(self) -> None:
        self.repo.add("fixture.dat", b"\x00" * SIZE_LIMIT)
        self.assertPasses()

    def test_binary_over_size_limit_fails(self) -> None:
        self.assertRejects("fixture.dat", b"\x00" * (SIZE_LIMIT + 1), "exceeds the 65536-byte limit")

    def test_allowlisted_binary_passes_only_with_matching_hash(self) -> None:
        data = b"\x00\x01" * SIZE_LIMIT
        allowlist = {
            "schema_version": 1,
            "binaries": [
                {"path": "fixture.dat", "sha256": hashlib.sha256(data).hexdigest(), "reason": "synthetic test"}
            ],
        }
        self.repo.add("scripts/repository-safety-allowlist.json", json.dumps(allowlist).encode())
        self.repo.add("fixture.dat", data)
        self.assertPasses()
        self.assertRejects("fixture.dat", data + b"\x00", "not allowlisted")

    # --- entry types and filenames ----------------------------------------------

    def test_submodule_gitlink_fails(self) -> None:
        self.repo.git(
            "update-index", "--add", "--cacheinfo",
            "160000,9489aebf42a870fa6a60816c273b0aa22fe5c1fe,external/w2u",
        )
        result = self.repo.run_guard()
        self.assertEqual(result.returncode, 1)
        self.assertIn("submodule", result.stderr)

    def test_filenames_with_spaces_are_handled(self) -> None:
        self.repo.add("docs/name with spaces.md", b"benign\n")
        self.assertPasses()
        self.assertRejects("assets/sprite bank copy", synthetic_nitro(b"RGCN"), "Nitro NCGR magic")

    def test_filename_with_newline_is_handled(self) -> None:
        self.repo.add("docs/line\nbreak.md", b"benign\n")
        self.assertPasses()
        self.assertRejects("vfs/line\nbreak", b"synthetic\n", "forbidden directory 'vfs/'")

    def test_staged_content_is_scanned_not_working_tree(self) -> None:
        self.repo.add("assets/member", synthetic_nitro(b"NARC"))
        (self.repo.root / "assets" / "member").write_bytes(b"benign now\n")  # unstaged edit
        result = self.repo.run_guard()
        self.assertEqual(result.returncode, 1, "the staged NARC must still be rejected")


if __name__ == "__main__":
    unittest.main()
