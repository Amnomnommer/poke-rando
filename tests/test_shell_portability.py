"""Regression: staged W2U Meson shell fragments must run under POSIX /bin/sh (dash), not only Bash.

Real failure: `cp "$file" "$dst/$((10#$entry))"` in the icon staging step aborts under dash with
`arithmetic expression: expecting EOF: "10#00000000"`. These tests execute the patched fragment with
/bin/sh and check every zero-padded value, including 08/09 that octal parsing would also break.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_w2u as sw  # noqa: E402
from b2er.assembly import BuildError, patches  # noqa: E402

SH = "/bin/sh"
POSIX_SHELLS = [s for s in (SH, shutil.which("dash"), shutil.which("busybox")) if s]


def fragment(text: str) -> str:
    start = text.index("'''") + 3
    return text[start : text.index("'''", start)]


class ShellPortabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "W2U"
        sw.write(self.root, "data/graphics/meson.build", sw.GRAPHICS_MESON)
        self.pins = {"data/graphics/meson.build": hashlib.sha256(sw.GRAPHICS_MESON.encode()).hexdigest()}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_fragment(self, code: str, names: list[str], shell: str = SH) -> tuple[subprocess.CompletedProcess, list[str]]:
        src, dst = self.tmp / "src", self.tmp / "dst"
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir()
        for name in names:
            (src / name).write_text(name)
        argv = [shell, "sh", "-eu", "-c", code] if shell.endswith("busybox") else [shell, "-eu", "-c", code]
        result = subprocess.run(argv + ["stage", str(src), str(dst)], capture_output=True, text=True)
        return result, sorted(os.listdir(dst)) if dst.is_dir() else []

    def test_unpatched_fragment_fails_under_posix_sh(self) -> None:
        if subprocess.run([SH, "-c", "echo $((10#08))"], capture_output=True).returncode == 0:
            self.skipTest("/bin/sh accepts Bash base-prefix arithmetic here")
        result, _ = self.run_fragment(fragment(sw.GRAPHICS_MESON), ["007_00000000.png"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("10#", result.stderr)

    def test_patched_fragment_converts_zero_padded_values_under_posix_sh(self) -> None:
        report = patches.make_shell_portable(self.root, self.pins)
        self.assertEqual(report["data/graphics/meson.build"]["sites"], 1)
        code = fragment((self.root / "data/graphics/meson.build").read_text())
        self.assertNotIn("10#", code)
        names = ["007_00000000.png", "007_00000007.png", "007_00000008.png", "007_00000009.pal", "007_00000123.png",
                 "007_00001000.png", "notes.txt"]
        expected = sorted(["0", "7", "8", "9", "123", "1000", "notes.txt"])
        for shell in POSIX_SHELLS:
            with self.subTest(shell=shell):
                result, staged = self.run_fragment(code, names, shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(staged, expected)
                self.assertEqual((self.tmp / "dst" / "8").read_text(), "007_00000008.png")
                self.assertEqual((self.tmp / "dst" / "0").read_text(), "007_00000000.png")

    def test_patched_fragment_is_deterministic(self) -> None:
        patches.make_shell_portable(self.root, self.pins)
        first = (self.root / "data/graphics/meson.build").read_bytes()
        again = self.tmp / "again"
        sw.write(again, "data/graphics/meson.build", sw.GRAPHICS_MESON)
        patches.make_shell_portable(again, self.pins)
        self.assertEqual((again / "data/graphics/meson.build").read_bytes(), first)

    def test_unexpected_names_pass_through_unchanged(self) -> None:
        patches.make_shell_portable(self.root, self.pins)
        code = fragment((self.root / "data/graphics/meson.build").read_text())
        result, staged = self.run_fragment(code, ["007_0000000.png", "007_000000x1.png", "008_00000001.png"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(staged, ["007_0000000.png", "007_000000x1.png", "008_00000001.png"])  # not entry-shaped

    def test_non_decimal_entry_fails_closed(self) -> None:
        lines = patches.posix_decimal_lines("", "entry", "n")
        script = 'entry="$1"\n' + "\n".join(lines) + '\nprintf "%s" "$n"\n'
        for value, want in (("00000000", "0"), ("00000008", "8"), ("00000123", "123")):
            out = subprocess.run([SH, "-eu", "-c", script, "t", value], capture_output=True, text=True)
            self.assertEqual((out.returncode, out.stdout), (0, want))
        for bad in ("", "12a4", "-1", "1 2"):
            out = subprocess.run([SH, "-eu", "-c", script, "t", bad], capture_output=True, text=True)
            self.assertNotEqual(out.returncode, 0, bad)
            self.assertIn("non-decimal entry", out.stderr)

    def test_guards(self) -> None:
        with self.assertRaises(BuildError):  # pinned pre-image mismatch
            patches.make_shell_portable(self.root, {"data/graphics/meson.build": "0" * 64})
        sw.write(self.root, "src/meson.build", "cmd = '$((10#$x))'\n")
        with self.assertRaises(BuildError) as ctx:  # any unpatched site anywhere in the staged tree fails the build
            patches.make_shell_portable(self.root, self.pins)
        self.assertIn("src/meson.build", str(ctx.exception))
        plain = self.tmp / "plain"
        sw.write(plain, "data/graphics/meson.build", "nothing to patch\n")
        with self.assertRaises(BuildError):
            patches.make_shell_portable(plain, {"data/graphics/meson.build": hashlib.sha256(b"nothing to patch\n").hexdigest()})

    def test_pinned_upstream_preimage_is_recorded(self) -> None:
        self.assertEqual(patches.BASH_ARITHMETIC_SITES,
                         {"data/graphics/meson.build": "f7bbd02453057f882c47e07f4b4249f46c62541103639962ebd2f4e7f717c033"})


if __name__ == "__main__":
    unittest.main()
