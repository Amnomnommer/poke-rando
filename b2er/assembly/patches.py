"""Deterministic project patch set applied to the staged W2U copy (never to the user's checkout).

Every patch checks its pre-image SHA-256 (pinned to W2U 9489aeb) and its
post-condition. No upstream source text is stored here beyond the identifiers
the edits remove.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..nds import arm9 as arm9_tools
from . import BuildError

MAIN_MENU_SKIP = "MainMenuSkip(1).dll"
MAIN_MENU_SKIP_SHA256 = "cd1da75cfb740ee1e5b9d3abacd23bf8209e3b6f536cbdd7bc336995518ca67f"
MESON_BUILD_SHA256 = "fe478fb12a361e4cabec69ff23f2740817069a2e8e7a62d63ac531e0eed4acd3"
CMPROJ_EXAMPLE_SHA256 = "3f51489d8c5b7cba766304f6f75c3d384521a7b6bf1c023ea4079325986958a0"
PMC_ARM9_SHA256 = "8c6c2a4b93527d7654838cf712604e7eba73e0ad7628a7239323d668d96e6431"
TOKEN = "main_menu_skip_patch"
# Staged Meson files whose `sh -c` fragments use Bash-only base-prefix arithmetic, with their pinned pre-images.
BASH_ARITHMETIC_SITES = {"data/graphics/meson.build": "f7bbd02453057f882c47e07f4b4249f46c62541103639962ebd2f4e7f717c033"}
BASE10_EXPRESSION = re.compile(r"\$\(\(10#\$([A-Za-z_][A-Za-z0-9_]*)\)\)")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expect(path: Path, digest: str | None) -> bytes:
    if not path.is_file():
        raise BuildError(f"patch target missing: {path.name}")
    data = path.read_bytes()
    if digest is not None and sha256(data) != digest:
        raise BuildError(f"patch pre-image mismatch for {path.name}: expected {digest[:12]}..., found {sha256(data)[:12]}...")
    return data


def remove_main_menu_skip(root: Path, meson_sha256: str | None = MESON_BUILD_SHA256,
                          plugin_sha256: str = MAIN_MENU_SKIP_SHA256) -> dict:
    """Delete every production copy of the development-only plugin and its Meson input reference."""
    removed = []
    for path in sorted(root.rglob(MAIN_MENU_SKIP)):
        if path.is_file() and not path.is_symlink():
            if sha256(path.read_bytes()) != plugin_sha256:
                raise BuildError(f"{path.relative_to(root)} is not the pinned development plugin")
            path.unlink()
            removed.append(str(path.relative_to(root)))
    meson = root / "meson.build"
    text = _expect(meson, meson_sha256).decode("utf-8")
    lines = text.splitlines(keepends=True)
    assignment = [i for i, line in enumerate(lines) if line.lstrip().startswith(TOKEN + " =")]
    reference = [i for i, line in enumerate(lines) if line.strip() == TOKEN + ","]
    if len(assignment) != 1 or len(reference) != 1:
        raise BuildError(f"meson.build must reference {TOKEN} exactly once as an assignment and once as an input")
    kept = [line for i, line in enumerate(lines) if i not in (assignment[0], reference[0])]
    new_text = "".join(kept)
    if TOKEN in new_text:
        raise BuildError(f"{TOKEN} still referenced after patching")
    meson.write_text(new_text, encoding="utf-8")
    remaining = [str(p.relative_to(root)) for p in root.rglob(MAIN_MENU_SKIP)]
    if remaining:
        raise BuildError(f"development plugin still present: {remaining}")
    return {"removed_files": removed, "meson_build_sha256_after": sha256(new_text.encode("utf-8"))}


def repair_pmc_arm9(root: Path, pinned: str | None = PMC_ARM9_SHA256) -> dict:
    """Clear a stale compressed-static-end in the staged prebuilt ARM9 before assembly."""
    path = root / "pmc" / "ARM9PMC.bin"
    data = _expect(path, pinned)
    fixed, state, changed = arm9_tools.repaired(data)
    if changed:
        path.write_bytes(fixed)
    after = arm9_tools.classify(fixed)
    if not after.bootable:
        raise BuildError(f"staged ARM9 is still not bootable: {after.detail}")
    return {"state_before": state.state, "detail_before": state.detail, "changed": changed,
            "field_offset": f"0x{state.params.compressed_end_field_offset:x}", "state_after": after.state,
            "sha256_after": sha256(fixed)}


def write_cmproj(root: Path, example_sha256: str | None = CMPROJ_EXAMPLE_SHA256) -> dict:
    example = _expect(root / "White2Upgrade.cmproj.example", example_sha256).decode("utf-8")
    if "VFSBase: ../IRDO_Extracted" not in example or "VFSOverlay: vfs" not in example:
        raise BuildError("cmproj example no longer names ../IRDO_Extracted and vfs")
    (root / "White2Upgrade.cmproj").write_text(example, encoding="utf-8")
    return {"vfs_base": "../IRDO_Extracted", "vfs_overlay": "vfs"}


def posix_decimal_lines(indent: str, variable: str, target: str) -> list[str]:
    """POSIX sh: require a non-empty all-digit value, then strip leading zeros (keeping one digit)."""
    return [
        f'{indent}case "${variable}" in ""|*[!0-9]*) echo "b2er: non-decimal entry \\"${variable}\\"" >&2; exit 1;; esac',
        f'{indent}{target}="${variable}"',
        f'{indent}while [ "${{#{target}}}" -gt 1 ] && [ "${{{target}#0}}" != "${target}" ]; do {target}="${{{target}#0}}"; done',
    ]


def make_shell_portable(root: Path, pins: dict | None = None) -> dict:
    """Replace Bash-only `$((10#$VAR))` in staged Meson shell fragments (run by `sh`, e.g. dash) with POSIX logic."""
    pins = BASH_ARITHMETIC_SITES if pins is None else pins
    patched = {}
    for rel, digest in sorted(pins.items()):
        path = root / rel
        text = _expect(path, digest).decode("utf-8")
        out, count = [], 0
        for line in text.splitlines(keepends=True):
            match = BASE10_EXPRESSION.search(line)
            if not match:
                out.append(line)
                continue
            if len(BASE10_EXPRESSION.findall(line)) != 1:
                raise BuildError(f"{rel}: more than one base-10 expression on a line")
            indent = line[: len(line) - len(line.lstrip())]
            variable = match.group(1)
            target = f"b2er_decimal_{variable}"
            out += [l + "\n" for l in posix_decimal_lines(indent, variable, target)]
            out.append(BASE10_EXPRESSION.sub(f"${target}", line))
            count += 1
        if count == 0:
            raise BuildError(f"{rel}: expected Bash base-10 arithmetic to patch, found none")
        new_text = "".join(out)
        path.write_text(new_text, encoding="utf-8")
        patched[rel] = {"sites": count, "sha256_after": sha256(new_text.encode("utf-8"))}
    remaining = []
    for path in sorted(root.rglob("meson.build")) + sorted(root.rglob("meson_options.txt")):
        if "10#" in path.read_text(encoding="utf-8", errors="replace"):
            remaining.append(str(path.relative_to(root)))
    if remaining:
        raise BuildError(f"Bash-only base-10 arithmetic remains in staged Meson files: {remaining}")
    return patched


ROMBUILDER_ANCHOR = 'ctrmap.cli.ROMBuilder --input "$2" --output "$3"; '
ROM_CHECK_SCRIPT = "tools/b2er_check_rombuilder_output.py"
ROM_CHECK_TEMPLATE = """#!/usr/bin/env python3
# Generated by b2er (never part of W2U): fail the ROM target unless CTRMap's ROMBuilder produced a valid ROM,
# so the ARM9 footer and finalization scripts never run on a missing, empty, or truncated output.
import sys
from pathlib import Path
sys.path.insert(0, {repo!r})
from b2er.nds.dsi import check_assembled_file
problems = check_assembled_file(Path(sys.argv[1]), Path({base_header!r}))
for problem in problems:
    print(f"b2er: ROMBuilder output rejected: {{problem}}", file=sys.stderr)
sys.exit(1 if problems else 0)
"""


def guard_rombuilder(root: Path, repo: Path, base_header: Path) -> dict:
    """Insert an output check between ROMBuilder and the footer/finalize steps of the staged ROM command.

    CTRMap's CLI can print an exception yet exit 0, so `sh -eu` alone does not stop the compound command.
    """
    meson = root / "meson.build"
    text = meson.read_text(encoding="utf-8")
    if text.count(ROMBUILDER_ANCHOR) != 1:
        raise BuildError("meson.build must contain the ROMBuilder invocation exactly once")
    check = f'python3 -I "$(dirname "$4")/{Path(ROM_CHECK_SCRIPT).name}" "$3"; '
    new_text = text.replace(ROMBUILDER_ANCHOR, ROMBUILDER_ANCHOR + check)
    meson.write_text(new_text, encoding="utf-8")
    script = root / ROM_CHECK_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(ROM_CHECK_TEMPLATE.format(repo=str(Path(repo).resolve()), base_header=str(Path(base_header).resolve())),
                      encoding="utf-8")
    return {"inserted_after": "ROMBuilder", "check_script": ROM_CHECK_SCRIPT,
            "meson_build_sha256_after": sha256(new_text.encode("utf-8"))}


NARC_PREMERGE_SCRIPT = "tools/b2er_premerge_narcs.py"
NARC_PREMERGE_WORK = "b2er-narc-merge"
NARC_PREMERGE_TEMPLATE = """#!/usr/bin/env python3
# Generated by b2er (never part of W2U): merge CTRMap .arc overlay directories into exact NARC files
# before ROMBuilder, which otherwise leaves the base archive's stale tail in shorter rebuilt NARCs.
import shutil
import sys
from pathlib import Path
sys.path.insert(0, {repo!r})
from b2er.assembly import narc_premerge
jar, cmproj, java = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
work = cmproj.resolve().parent / {work!r}
if work.is_dir() and not work.is_symlink():
    shutil.rmtree(work)
try:
    report = narc_premerge.premerge(cmproj, work, narc_premerge.java_runner(java, jar, work))
except narc_premerge.PremergeError as error:
    print(f"b2er: NARC pre-merge failed: {{error}}", file=sys.stderr)
    sys.exit(1)
print(f"b2er: pre-merged {{len(report['merged'])}} CTRMap .arc overlays into exact NARCs")
"""


def premerge_narcs(root: Path, repo: Path) -> dict:
    """Run the NARC pre-merge immediately before ROMBuilder in the staged ROM command."""
    meson = root / "meson.build"
    text = meson.read_text(encoding="utf-8")
    if text.count(ROMBUILDER_ANCHOR) != 1:
        raise BuildError("meson.build must contain the ROMBuilder invocation exactly once")
    anchor = text.index(ROMBUILDER_ANCHOR)
    java = '"${JAVA:-java}"'
    start = text.rfind(java, 0, anchor)
    if start < 0 or text.rfind("'", start, anchor) >= 0 or "\n" in text[start:anchor]:
        raise BuildError("ROMBuilder invocation does not start with the expected Java launcher")
    step = f'python3 -I "$(dirname "$4")/{Path(NARC_PREMERGE_SCRIPT).name}" "$1" "$2" {java}; '
    new_text = text[:start] + step + text[start:]
    meson.write_text(new_text, encoding="utf-8")
    script = root / NARC_PREMERGE_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(NARC_PREMERGE_TEMPLATE.format(repo=str(Path(repo).resolve()), work=NARC_PREMERGE_WORK), encoding="utf-8")
    return {"inserted_before": "ROMBuilder", "script": NARC_PREMERGE_SCRIPT, "work": NARC_PREMERGE_WORK,
            "meson_build_sha256_after": sha256(new_text.encode("utf-8"))}


# --- W2U wild-battle setup hook (player-name corruption) -------------------------------------------
# Proven at runtime (melonDS GDB, five wild encounters): before W2U's hook at BtlSetup_SetWildNormal+0x3A,
# setup+0x34 points to the player info (UTF-16 "A", 0xFFFF); after it, setup+0x34 equals setup+0x24 (the
# party). Battle messages that print the player's name (e.g. throwing a Poke Ball) then read party data.
# No W2U code reads setup+0x34; the hook's only required work is replaying the two instructions its
# branch-link displaced. The correction drops the two offending instructions in the staged copy only.
WILD_SETUP_HOOK = "src/battle_setup_patch.s"
WILD_SETUP_HOOK_SHA256 = "1d386e4d79ec8a930f374f3a6ed3679d1de593f3f35ecb5c4a720af28e3469ea"
WILD_SETUP_LABEL = "THUMB_BRANCH_LINK_BtlSetup_SetWildNormal_0x3A:\n"
WILD_SETUP_OFFENDING = "    ldr r1, [r4, #0x24]\n    str r1, [r4, #0x34]\n"
WILD_SETUP_REPLAY = "    ldr r0, [sp, #0x20]\n    movs r7, #0\n    bx lr\n"
WILD_SETUP_CORRECTION = ("    @ b2er: removed `ldr r1, [r4, #0x24]` / `str r1, [r4, #0x34]`, which replaced the player-info\n"
                         "    @ pointer at setup+0x34 with the party pointer (wild-battle player-name corruption).\n")
# Thumb encodings (checked with llvm-mc -triple=thumbv5te): ldr r1,[r4,#0x24]=616A str r1,[r4,#0x34]=6163
# ldr r0,[sp,#0x20]=0898 movs r7,#0=0027 bx lr=7047.
WILD_SETUP_PREIMAGE_CODE = bytes.fromhex("616a6163" "08980027" "7047")
WILD_SETUP_CORRECTED_CODE = bytes.fromhex("08980027" "7047")


def repair_wild_setup_hook(root: Path, pinned: str | None = WILD_SETUP_HOOK_SHA256) -> dict:
    path = root / WILD_SETUP_HOOK
    text = _expect(path, pinned).decode("utf-8")
    site = WILD_SETUP_LABEL + WILD_SETUP_OFFENDING
    body = text[text.find(site) + len(site):] if site in text else ""
    if text.count(WILD_SETUP_LABEL) != 1 or text.count(site) != 1 or text.count(WILD_SETUP_OFFENDING) != 1:
        raise BuildError(f"{WILD_SETUP_HOOK}: the wild-setup hook site is missing or changed; refusing to patch")
    replay_at = body.find(WILD_SETUP_REPLAY)
    if replay_at < 0 or any(line.strip() and not line.strip().startswith("@") for line in body[:replay_at].splitlines()):
        raise BuildError(f"{WILD_SETUP_HOOK}: the hook no longer replays exactly `ldr r0, [sp, #0x20]; movs r7, #0; bx lr`")
    new_text = text.replace(site, WILD_SETUP_LABEL + WILD_SETUP_CORRECTION)
    path.write_text(new_text, encoding="utf-8")
    return {"file": WILD_SETUP_HOOK, "sha256_before": sha256(text.encode("utf-8")), "sha256_after": sha256(new_text.encode("utf-8")),
            "removed": ["ldr r1, [r4, #0x24]", "str r1, [r4, #0x34]"], "kept": ["ldr r0, [sp, #0x20]", "movs r7, #0", "bx lr"],
            "code_before": WILD_SETUP_PREIMAGE_CODE.hex(), "code_after": WILD_SETUP_CORRECTED_CODE.hex()}


def wild_setup_code_problems(dll: bytes | None) -> list[str]:
    """Final-ROM check: the built W2U code must contain the corrected hook and never the offending one."""
    if dll is None:
        return ["patches/White2Upgrade.dll is missing from the built ROM"]
    problems = []
    if WILD_SETUP_PREIMAGE_CODE in dll:
        problems.append("built White2Upgrade.dll still contains `ldr r1,[r4,#0x24]; str r1,[r4,#0x34]` before the replayed code")
    if WILD_SETUP_CORRECTED_CODE not in dll:
        problems.append("built White2Upgrade.dll does not contain the corrected hook body (ldr r0,[sp,#0x20]; movs r7,#0; bx lr)")
    return problems
