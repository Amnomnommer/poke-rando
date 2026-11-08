"""Generated-data tests for the staged correction of W2U's wild-battle setup hook (player-name corruption).

Runtime proof (melonDS GDB, five wild encounters): before W2U's hook at BtlSetup_SetWildNormal+0x3A,
setup+0x34 pointed to the player info (UTF-16 "A", 0xFFFF); after it, setup+0x34 equalled setup+0x24, the
party pointer, so the Poke Ball message printed party data as the player's name.
"""

from __future__ import annotations

import re
import shutil
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
from b2er.assembly import BuildError, patches  # noqa: E402

LLVM_MC = shutil.which("llvm-mc")


def instructions(text: str, label: str = patches.WILD_SETUP_LABEL) -> list[str]:
    """Non-comment instruction lines of the hook, from its label up to the next directive."""
    body = text.split(label, 1)[1]
    out = []
    for line in body.splitlines():
        line = line.split("@", 1)[0].strip()
        if line.startswith("."):
            break
        if line:
            out.append(re.sub(r"\s+", " ", line))
    return out


def decode_thumb_ldr_str_imm(code: bytes) -> list[tuple[str, int, int, int]]:
    """Independent decoder for 16-bit Thumb LDR/STR (immediate, word): (op, Rt, Rn, offset)."""
    out = []
    for (half,) in struct.iter_unpack("<H", code[: len(code) // 2 * 2]):
        if half & 0xF000 == 0x6000:
            out.append(("ldr" if half & 0x0800 else "str", half & 7, (half >> 3) & 7, ((half >> 6) & 0x1F) * 4))
    return out


class WildSetupHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        self.path = self.root / patches.WILD_SETUP_HOOK
        self.path.write_text(sw.SYNTHETIC_WILD_SETUP_HOOK)
        self.pin = patches.sha256(sw.SYNTHETIC_WILD_SETUP_HOOK.encode())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_offending_write_present_in_pinned_preimage(self) -> None:
        self.assertEqual(instructions(sw.SYNTHETIC_WILD_SETUP_HOOK),
                         ["ldr r1, [r4, #0x24]", "str r1, [r4, #0x34]", "ldr r0, [sp, #0x20]", "movs r7, #0", "bx lr"])
        self.assertEqual(decode_thumb_ldr_str_imm(patches.WILD_SETUP_PREIMAGE_CODE),
                         [("ldr", 1, 4, 0x24), ("str", 1, 4, 0x34)])
        self.assertEqual(patches.WILD_SETUP_HOOK_SHA256, "1d386e4d79ec8a930f374f3a6ed3679d1de593f3f35ecb5c4a720af28e3469ea")

    def test_correction_preserves_0x34_and_leaves_0x24_untouched(self) -> None:
        report = patches.repair_wild_setup_hook(self.root, self.pin)
        text = self.path.read_text()
        self.assertEqual(instructions(text), ["ldr r0, [sp, #0x20]", "movs r7, #0", "bx lr"])
        self.assertNotRegex(text.split("@ b2er", 1)[0] + text.split("pointer (wild-battle", 1)[1], r"\[r4, #0x(24|34)\]")
        self.assertEqual(report["removed"], ["ldr r1, [r4, #0x24]", "str r1, [r4, #0x34]"])
        self.assertEqual(decode_thumb_ldr_str_imm(patches.WILD_SETUP_CORRECTED_CODE), [])  # no setup+0x24/+0x34 access

    def test_unrelated_lines_unchanged(self) -> None:
        before = sw.SYNTHETIC_WILD_SETUP_HOOK.splitlines()
        patches.repair_wild_setup_hook(self.root, self.pin)
        after = self.path.read_text().splitlines()
        removed = [line for line in before if line not in after]
        added = [line for line in after if line not in before]
        self.assertEqual(removed, ["    ldr r1, [r4, #0x24]", "    str r1, [r4, #0x34]"])
        self.assertTrue(all(line.strip().startswith("@ b2er") or line.strip().startswith("@ pointer") for line in added), added)

    def test_missing_or_changed_site_refused(self) -> None:
        with self.assertRaises(BuildError):
            patches.repair_wild_setup_hook(self.root, "0" * 64)  # pinned preimage changed
        variants = {
            "missing": sw.SYNTHETIC_WILD_SETUP_HOOK.replace(patches.WILD_SETUP_OFFENDING, ""),
            "other offset": sw.SYNTHETIC_WILD_SETUP_HOOK.replace("#0x34]", "#0x30]"),
            "relabelled": sw.SYNTHETIC_WILD_SETUP_HOOK.replace("SetWildNormal_0x3A:", "SetWildNormal_0x3C:"),
            "replay changed": sw.SYNTHETIC_WILD_SETUP_HOOK.replace("movs r7, #0", "movs r7, #1"),
            "extra instruction": sw.SYNTHETIC_WILD_SETUP_HOOK.replace("    @ replay\n", "    adds r2, #1\n"),
        }
        for name, text in variants.items():
            self.path.write_text(text)
            with self.assertRaises(BuildError, msg=name):
                patches.repair_wild_setup_hook(self.root, None)
            self.assertEqual(self.path.read_text(), text, f"{name}: refused input left unchanged")

    def test_final_code_check(self) -> None:
        corrected = b"\x00" * 8 + patches.WILD_SETUP_CORRECTED_CODE + b"\x00" * 8
        self.assertEqual(patches.wild_setup_code_problems(corrected), [])
        self.assertTrue(patches.wild_setup_code_problems(b"\x00" * 8 + patches.WILD_SETUP_PREIMAGE_CODE))
        self.assertTrue(patches.wild_setup_code_problems(b"\x00" * 32))
        self.assertTrue(patches.wild_setup_code_problems(None))

    @unittest.skipUnless(LLVM_MC, "llvm-mc not installed")
    def test_assembled_staged_correction(self) -> None:
        def assemble(text: str) -> bytes:
            result = subprocess.run([LLVM_MC, "-triple=thumbv5te-none-eabi", "-show-encoding"], input=text,
                                    capture_output=True, text=True, check=True)
            return bytes(int(b, 16) for enc in re.findall(r"encoding: \[([^\]]*)\]", result.stdout) for b in enc.split(","))
        self.assertEqual(assemble(sw.SYNTHETIC_WILD_SETUP_HOOK), patches.WILD_SETUP_PREIMAGE_CODE)
        patches.repair_wild_setup_hook(self.root, self.pin)
        code = assemble(self.path.read_text())
        self.assertEqual(code, patches.WILD_SETUP_CORRECTED_CODE)
        self.assertEqual(patches.wild_setup_code_problems(code), [])


class PipelineTests(unittest.TestCase):
    def test_staged_tree_corrected_and_checkout_untouched(self) -> None:
        from b2er import pipeline
        tmp = Path(tempfile.mkdtemp())
        facts = sw.make_checkout(tmp / "W2U")
        white, black = sw.make_roms(tmp / "roms")
        pins = sw.make_pins(tmp / "pins", facts, white, black)
        req = pipeline.BuildRequest(rom=white, seed="hook", auxiliary={"w2u": tmp / "W2U"}, output_dir=tmp / "out",
                                    workroot_base=tmp / "work", pins_dir=pins, species_max=1023, keep_workroot=True,
                                    options={"builder": "mock", "patch_pins": sw.patch_pins(facts)})
        result = pipeline.run(req)
        self.assertEqual((tmp / "W2U" / patches.WILD_SETUP_HOOK).read_text(), sw.SYNTHETIC_WILD_SETUP_HOOK)
        staged = list((tmp / "work").rglob("battle_setup_patch.s"))
        self.assertEqual(len(staged), 1)
        self.assertEqual(instructions(staged[0].read_text()), ["ldr r0, [sp, #0x20]", "movs r7, #0", "bx lr"])
        prepare = result["manifest"]["transformations"]["prepare"]["wild_setup_hook"]
        self.assertEqual(prepare["removed"], ["ldr r1, [r4, #0x24]", "str r1, [r4, #0x34]"])
        checks = {c["name"]: c["status"] for c in result["verification"]["checks"]}
        self.assertEqual(checks["wild_setup_hook_corrected"], "pass")


if __name__ == "__main__":
    unittest.main()
