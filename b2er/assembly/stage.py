"""Stage a verified W2U checkout and the clean ROM file system into a workroot (read-only on the sources)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from ..nds import dsi
from ..nds.rom import Rom
from ..w2u_checkout import ReadOnlyGit
from . import BuildError


def _write_tree(git: ReadOnlyGit, destination: Path) -> int:
    """Write HEAD's tree via ls-tree/cat-file: no filters, attributes, hooks, or checkout."""
    listing = git.run("ls-tree", "-r", "-z", "--full-tree", "HEAD")
    entries = []
    for record in listing.split(b"\x00"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, oid = meta.decode().split(" ")
        entries.append((mode, kind, oid, path.decode("utf-8", errors="surrogateescape")))
    blobs = [e for e in entries if e[1] == "blob"]
    process = subprocess.Popen(["git", "-C", str(git.worktree), "cat-file", "--batch"], env=git.env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert process.stdin and process.stdout
    import threading

    def feed() -> None:
        try:
            process.stdin.write(b"".join(e[2].encode() + b"\n" for e in blobs))
        finally:
            process.stdin.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    count = 0
    try:
        for mode, _kind, _oid, rel in blobs:
            header = process.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise BuildError(f"unexpected cat-file output while staging {rel!r}")
            size = int(header[2])
            data = process.stdout.read(size)
            process.stdout.read(1)
            target = destination / rel
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                raise BuildError(f"refusing to stage unsafe path {rel!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "120000":
                os.symlink(os.fsdecode(data), target)
            else:
                target.write_bytes(data)
                if mode == "100755":
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            count += 1
    finally:
        process.stdout.close()
        writer.join()
        process.wait()
    for mode, kind, _oid, rel in entries:
        if kind == "commit":
            (destination / rel).mkdir(parents=True, exist_ok=True)
    return count


def stage_w2u(checkout: Path, destination: Path, swan_path: str) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    files = _write_tree(ReadOnlyGit(checkout), destination)
    swan = checkout / swan_path
    swan_files = _write_tree(ReadOnlyGit(swan), destination / swan_path)
    return {"files": files, "swan_files": swan_files}


def banner_size(rom: Rom) -> int:
    return dsi.banner_size(bytes(rom.data[:0x1000]))


def extract_base(rom_bytes: bytes, destination: Path, arm9i_provider=None) -> dict:
    """Write the clean ROM's files in CTRMap's base layout (data/, overlay/, NTR binaries, and DSi binaries).

    For a TWL-extended ROM, ``arm9i_provider(raw_components) -> bytes`` must return CTRMap's arm9i.bin
    (modcrypt area 1 decrypted by the pinned CTRMap); arm7i.bin and twlblowfishtable.bin are exact bytes.
    """
    rom = Rom(rom_bytes)
    dsi_raw = dsi.extract_raw(rom_bytes)  # validates every DSi region before anything is written
    if dsi_raw and arm9i_provider is None:
        raise BuildError("DSi-enhanced ROM: an arm9i provider (pinned CTRMap) is required")
    destination.mkdir(parents=True, exist_ok=False)
    for path, file_id in rom.paths().items():
        target = destination / "data" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(rom.file_bytes(file_id))
    overlay_dir = destination / "overlay"
    overlay_dir.mkdir()
    for overlay in rom.overlays():
        # CTRMap names overlay files by table index; W2U references them by overlay ID. Require both to agree.
        if overlay.overlay_id != overlay.index:
            raise BuildError(f"overlay table entry {overlay.index} has ID {overlay.overlay_id}; CTRMap's index naming "
                             "and W2U's ID references would disagree")
        (overlay_dir / f"overlay_{overlay.index:04d}.bin").write_bytes(rom.file_bytes(overlay.file_id))
    d = rom.data
    regions = {
        "arm9.bin": (rom.field("arm9_offset"), rom.field("arm9_size")),
        "arm7.bin": (rom.field("arm7_offset"), rom.field("arm7_size")),
        "y9.bin": (rom.field("y9_offset"), rom.field("y9_size")),
        "y7.bin": (rom.field("y7_offset"), rom.field("y7_size")),
        "header.bin": (0, min(len(d), 0x1000)),
        "banner.bin": (rom.field("banner_offset"), banner_size(rom)),
    }
    for name, (offset, size) in regions.items():
        if offset < 0 or size < 0 or offset + size > len(d):
            raise BuildError(f"{name} region 0x{offset:x}+0x{size:x} is outside the ROM")
        (destination / name).write_bytes(bytes(d[offset : offset + size]))
    report = {"files": len(rom.paths()), "overlays": len(rom.overlays()), "dsi_enhanced": bool(dsi_raw)}
    if dsi_raw:
        ours = {name: (destination / name).read_bytes() for name in regions} | dsi_raw
        ours |= {f"overlay/{p.name}": p.read_bytes() for p in sorted(overlay_dir.iterdir())}
        arm9i = arm9i_provider(ours)
        if len(arm9i) != len(dsi_raw["arm9i.bin"]):
            raise BuildError("arm9i provider changed the ARM9i length")
        (destination / "arm9i.bin").write_bytes(arm9i)
        (destination / "arm7i.bin").write_bytes(dsi_raw["arm7i.bin"])
        (destination / "twlblowfishtable.bin").write_bytes(dsi_raw["twlblowfishtable.bin"])
        report["dsi_components"] = {name: len(data) for name, data in sorted(dsi_raw.items())}
    return report
