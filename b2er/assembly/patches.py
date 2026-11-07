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
