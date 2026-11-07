"""Pre-merge W2U's CTRMap ``.arc`` overlay directories into exact NARC files before ROMBuilder runs.

Root cause (pinned CTRMap, confirmed in bytecode and by running the jar on generated data): when
ROMBuilder applies a ``.arc`` overlay directory, ``DirectNARC.setData`` builds the new archive in a temp
file and, because its target is a virtual-filesystem file rather than a ``DiskFile``, copies it back
with ``FSUtil.copy``. That copy writes through ``RandomAccessFile "rw"`` and never truncates, so when the
new archive is shorter than the base archive, the base archive's stale tail stays in the file and
in the ROM's FAT allocation (e.g. NARC declares 40804 bytes, FAT entry is 41500). Those bytes are not
padding: CTRMap's own padding is 0xFF to 4-byte alignment inside the declared extent.

For a ``DiskFile`` target the same ``setData`` finishes with ``Files.move(REPLACE_EXISTING)``, which
yields exactly the declared archive. So each overlay directory is merged here with CTRMap's own
``setData`` (same entries, same ``.arc`` compression directives, same base archive) into a private
copy of the base NARC and strictly validated. CTRMap's overlay filesystem cannot take a plain file at
an archive path, so the exact merge replaces the workroot's own base extraction (``IRDO_Extracted``,
derived data; the clean ROM is never touched) and the staged overlay directory is removed. ROMBuilder
then stores that file unchanged, its FAT extent equals the NARC size, and its DSi digest tables are
computed over the final bytes (nothing is patched after ROMBuilder).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

from ..nds import FormatError

JAVA_SOURCE = """import ctrmap.formats.ntr.narc.DirectNARC;
import xstandard.fs.accessors.DiskFile;
import xstandard.fs.accessors.arc.ArcInput;
import java.io.File;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** b2er: merge CTRMap .arc overlay directories into private DiskFile copies of their base NARCs. */
public class B2erNarcMerge {
    public static void main(String[] args) throws Exception {
        for (int i = 0; i + 1 < args.length; i += 2) {
            File[] children = new File(args[i + 1]).listFiles();
            Arrays.sort(children);
            List<ArcInput> inputs = new ArrayList<>();
            for (File child : children) {
                inputs.add(new ArcInput(child.getName(), new DiskFile(child)));
            }
            DirectNARC.setData(new DiskFile(new File(args[i])), inputs);
        }
    }
}
"""
DOT_ARC = ".arc"
REPORT = "report.json"


class PremergeError(Exception):
    pass


def cmproj_roots(cmproj: Path) -> tuple[Path, Path]:
    base = overlay = None
    for line in Path(cmproj).read_text(encoding="utf-8").splitlines():
        if line.startswith("VFSBase:"):
            base = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("VFSOverlay:"):
            overlay = line.split(":", 1)[1].strip().strip('"')
    if not base or not overlay:
        raise PremergeError("cmproj does not name VFSBase and VFSOverlay")
    root = Path(cmproj).resolve().parent
    return (root / base).resolve(), (root / overlay).resolve()


def narc_extent(data: bytes, where: str) -> int:
    """Strict structural check of a whole NARC file: declared size == stored size, sections in bounds."""
    if len(data) < 16 or data[:4] != b"NARC":
        raise PremergeError(f"{where}: not a NARC")
    declared = struct.unpack_from("<I", data, 8)[0]
    if declared != len(data):
        raise PremergeError(f"{where}: NARC declares {declared} bytes but is {len(data)}")
    from ..nds.narc import Narc
    try:
        Narc.parse(data)
    except FormatError as error:
        raise PremergeError(f"{where}: {error}") from None
    return declared


def arc_directories(overlay: Path) -> list[Path]:
    found = []
    for dirpath, dirnames, filenames in os.walk(overlay):
        dirnames.sort()
        if DOT_ARC in filenames:
            found.append(Path(dirpath))
    return found


def premerge(cmproj: Path, work: Path, runner) -> dict:
    """Fold every staged `.arc` overlay directory that has a base NARC into the base, using CTRMap's exact merge.

    `runner(pairs)` runs CTRMap's DirectNARC.setData over [(target_copy, overlay_dir), ...]."""
    base_root, overlay_root = cmproj_roots(cmproj)
    work.mkdir(parents=True, exist_ok=False)
    jobs = []
    for directory in arc_directories(overlay_root):
        rel = directory.relative_to(overlay_root)
        base = base_root / rel
        if directory.is_symlink() or base.is_symlink():
            raise PremergeError(f"{rel.as_posix()}: symlinked overlay or base archive refused")
        if not base.is_file():
            continue  # a brand-new archive has no base tail to leave behind
        base_bytes = base.read_bytes()
        narc_extent(base_bytes, f"base {rel.as_posix()}")
        target = work / f"{len(jobs):04d}.narc"
        target.write_bytes(base_bytes)
        jobs.append((rel, directory, target, len(base_bytes)))
    if jobs:
        runner([(target, directory) for _, directory, target, _ in jobs])
    entries = []
    for rel, directory, target, base_size in jobs:
        merged = target.read_bytes()
        size = narc_extent(merged, f"merged {rel.as_posix()}")
    for rel, directory, target, base_size in jobs:
        # CTRMap's overlay FS cannot take a plain file at an archive path, so the exact merge replaces the
        # workroot's own base extraction (derived data, never the clean ROM) and the overlay is removed.
        base = base_root / rel
        merged = target.read_bytes()
        size = len(merged)
        staging = base.with_name(base.name + ".b2er-merged")
        staging.write_bytes(merged)
        os.replace(staging, base)
        shutil.rmtree(directory)
        entries.append({"path": rel.as_posix(), "base_size": base_size, "merged_size": size,
                        "stale_tail_avoided": max(0, base_size - size), "sha256": hashlib.sha256(merged).hexdigest()})
    report = {"merged": entries}
    (work / REPORT).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def java_runner(java: str, jar: Path, work: Path):
    def run(pairs):
        source = work / "B2erNarcMerge.java"
        source.write_text(JAVA_SOURCE, encoding="utf-8")
        args = [str(x) for pair in pairs for x in pair]
        result = subprocess.run([java, "-cp", str(jar), str(source), *args], capture_output=True, text=True)
        (work / "narc-merge.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode != 0 or "Exception" in result.stderr:
            raise PremergeError(f"CTRMap NARC merge failed (exit {result.returncode}); see narc-merge.log")
    return run
