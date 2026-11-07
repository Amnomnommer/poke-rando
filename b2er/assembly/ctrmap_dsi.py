"""DSi component preparation for CTRMap, using the user's pinned local CTRMap jar for the one keyed step.

CTRMap's builder expects ``arm9i.bin`` with modcrypt area 1 stored decrypted
(it re-encrypts during assembly). This project holds no DSi keys, so that
single transform is delegated to the pinned, SHA-256-verified CTRMap jar on the
user's machine via its public ``NDSROM.extractROM``. Every component CTRMap
writes is cross-checked against this project's own bounds-checked raw
extraction: all bytes must be identical, except the area-1 bytes of arm9i.bin.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..nds import dsi
from . import BuildError

JAVA_SOURCE = """import ctrmap.formats.ntr.rom.srl.NDSROM;
import xstandard.fs.accessors.DiskFile;

/** b2er: run the pinned CTRMap extractor on a clean ROM (read-only input) into a scratch directory. */
public class B2erCtrmapExtract {
    public static void main(String[] args) throws Exception {
        NDSROM.extractROM(new DiskFile(args[0]), new DiskFile(args[1]));
    }
}
"""
CROSS_CHECKED = ("header.bin", "arm9.bin", "arm7.bin", "y9.bin", "y7.bin", "banner.bin", "arm7i.bin", "twlblowfishtable.bin")


def compare(ours: dict[str, bytes], theirs_dir: Path, raw_arm9i: bytes, area1_size: int) -> tuple[bytes, list[str]]:
    problems = []
    for name in sorted(n for n in ours if n.startswith("overlay/")):
        path = theirs_dir / name
        if not path.is_file() or path.read_bytes() != ours[name]:
            problems.append(f"{name} differs between CTRMap's extraction and the bounds-checked raw extraction")
    for name in CROSS_CHECKED:
        if name not in ours:
            continue
        path = theirs_dir / name
        if not path.is_file():
            problems.append(f"CTRMap extraction lacks {name}")
        elif path.read_bytes() != ours[name]:
            problems.append(f"{name} differs between CTRMap's extraction and the bounds-checked raw extraction")
    path = theirs_dir / "arm9i.bin"
    if not path.is_file():
        return b"", problems + ["CTRMap extraction lacks arm9i.bin"]
    arm9i = path.read_bytes()
    if len(arm9i) != len(raw_arm9i):
        problems.append("arm9i.bin length differs from the header-declared ARM9i size")
    elif arm9i[area1_size:] != raw_arm9i[area1_size:]:
        problems.append("arm9i.bin differs outside modcrypt area 1")
    return arm9i, problems


def ctrmap_arm9i(rom_path: Path, jar: Path, scratch: Path, ours: dict[str, bytes], java: str | None = None) -> bytes:
    data = Path(rom_path).read_bytes()
    layout = dsi.parse(data)
    if layout is None:
        raise BuildError("ctrmap_arm9i called for an NTR-only ROM")
    scratch.mkdir(parents=True, exist_ok=False)
    source = scratch / "B2erCtrmapExtract.java"
    source.write_text(JAVA_SOURCE, encoding="utf-8")
    out = scratch / "extracted"
    out.mkdir()
    java = java or os.environ.get("JAVA") or "java"
    result = subprocess.run([java, "-cp", str(jar), str(source), str(rom_path), str(out)], capture_output=True, text=True)
    (scratch / "ctrmap-extract.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise BuildError(f"CTRMap DSi extraction failed (exit {result.returncode}); see ctrmap-dsi/ctrmap-extract.log")
    arm9i, problems = compare(ours, out, ours["arm9i.bin"], layout.modcrypt1[1])
    if problems:
        raise BuildError("CTRMap DSi extraction disagrees with the raw extraction: " + "; ".join(problems))
    shutil.rmtree(out)
    return arm9i
