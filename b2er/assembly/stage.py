"""Stage a verified W2U checkout and the clean ROM file system into a workroot (read-only on the sources)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

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
    offset = rom.field("banner_offset")
    if offset == 0 or offset + 2 > len(rom.data):
        return 0
    version = int.from_bytes(rom.data[offset : offset + 2], "little")
    return {1: 0x840, 2: 0x940, 3: 0xA40, 0x103: 0x23C0}.get(version, 0x840)


def extract_base(rom_bytes: bytes, destination: Path) -> dict:
    """Write the clean ROM's files in the base-VFS layout W2U expects (data/, overlay/, binaries)."""
    rom = Rom(rom_bytes)
    destination.mkdir(parents=True, exist_ok=False)
    for path, file_id in rom.paths().items():
        target = destination / "data" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(rom.file_bytes(file_id))
    overlay_dir = destination / "overlay"
    overlay_dir.mkdir()
    for overlay in rom.overlays():
        (overlay_dir / f"overlay_{overlay.overlay_id:04d}.bin").write_bytes(rom.file_bytes(overlay.file_id))
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
        (destination / name).write_bytes(bytes(d[offset : offset + size]))
    return {"files": len(rom.paths()), "overlays": len(rom.overlays())}
