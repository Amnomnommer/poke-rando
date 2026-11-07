"""DSi component preparation for CTRMap, using the user's pinned local CTRMap jar for the one keyed step.

CTRMap's builder expects ``arm9i.bin`` with modcrypt area 1 stored decrypted
(it re-encrypts during assembly). This project holds no DSi keys, so that
single transform is delegated to the pinned, SHA-256-verified CTRMap jar on the
user's machine via its public ``NDSROM.extractROM``. Every component CTRMap
writes is cross-checked against this project's own bounds-checked raw
extraction: all bytes must be identical, except the area-1 bytes of arm9i.bin.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

from ..nds import dsi
from . import BuildError

JAVA_SOURCE = """import ctrmap.formats.ntr.rom.srl.NDSROM;
import xstandard.fs.accessors.DiskFile;

/** b2er: run the pinned CTRMap extractor on a private disposable ROM copy into a scratch directory. */
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


DISPOSABLE_NAME = "ctrmap-extract-input.nds"


def fingerprint(path: Path) -> dict:
    """Identity and content of a file without following a final symlink. No bytes are returned."""
    info = os.lstat(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "size": info.st_size, "mode": stat.S_IMODE(info.st_mode), "inode": info.st_ino,
            "device": info.st_dev, "links": info.st_nlink, "uid": info.st_uid, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "regular": stat.S_ISREG(info.st_mode)}


def verify_disposable(path: Path, protected: list[dict], expected: dict) -> list[str]:
    """The disposable copy must be a private regular file, not linked to any protected input, with exact content."""
    problems = []
    try:
        info = os.lstat(path)
    except OSError as error:
        return [f"disposable copy unavailable: {error.strerror}"]
    if stat.S_ISLNK(info.st_mode):
        return ["disposable copy is a symlink"]
    if not stat.S_ISREG(info.st_mode):
        return ["disposable copy is not a regular file"]
    if info.st_nlink != 1:
        problems.append(f"disposable copy has {info.st_nlink} hard links")
    if any((info.st_dev, info.st_ino) == (p["device"], p["inode"]) for p in protected):
        problems.append("disposable copy shares an inode with a protected input")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        problems.append("disposable copy is not owned by the running user")
    if stat.S_IMODE(info.st_mode) != 0o600:
        problems.append(f"disposable copy has mode {oct(stat.S_IMODE(info.st_mode))}, expected 0o600")
    current = fingerprint(path)
    if (current["size"], current["sha256"]) != (expected["size"], expected["sha256"]):
        problems.append("disposable copy does not match the clean ROM's size and SHA-256")
    return problems


def make_disposable_copy(source: Path, scratch: Path, expected: dict) -> Path:
    """Copy into a new private file (O_EXCL|O_NOFOLLOW, mode 0600); remove it on any failure."""
    target = scratch / DISPOSABLE_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(target, flags, 0o600)
    except OSError as error:
        raise BuildError(f"cannot create the disposable CTRMap input ({error.strerror}); refusing a pre-existing path") from None
    try:
        with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
            shutil.copyfileobj(src, out, 1 << 20)
            out.flush()
            os.fchmod(out.fileno(), 0o600)  # umask-independent
            os.fsync(out.fileno())
    except BaseException:
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
        raise
    return target


def java_runner(java: str, jar: Path, source_file: Path, rom: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run([java, "-cp", str(jar), str(source_file), str(rom), str(out)], capture_output=True, text=True)


def ctrmap_arm9i(rom_path: Path, jar: Path, scratch: Path, ours: dict[str, bytes], java: str | None = None, *,
                 original: Path | None = None, runner=None, report: dict | None = None) -> bytes:
    """Run CTRMap's extractor on a private writable copy (CTRMap opens its input with RandomAccessFile "rw")."""
    rom_path = Path(rom_path)
    protected_paths = [rom_path] + ([Path(original)] if original else [])
    before = {name: fingerprint(p) for name, p in zip(("canonical", "original"), protected_paths)}
    expected = before["canonical"]
    layout = dsi.parse(rom_path.read_bytes())
    if layout is None:
        raise BuildError("ctrmap_arm9i called for an NTR-only ROM")
    scratch.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        disposable = make_disposable_copy(rom_path, scratch, expected)
    except OSError as error:
        raise BuildError(f"could not create the disposable CTRMap input: {error}") from None
    problems = verify_disposable(disposable, list(before.values()), expected)
    if problems:
        os.unlink(disposable)  # unlinks the name only; never touches what a link might point to
        raise BuildError("disposable CTRMap input rejected: " + "; ".join(problems))
    out = scratch / "extracted"
    try:
        return _run_and_verify(rom_path, jar, scratch, ours, java, runner, report, disposable, out, layout,
                               protected_paths, before, expected)
    finally:
        # Neither a partial extraction nor the disposable ROM copy outlives this call, on success or failure.
        shutil.rmtree(out, ignore_errors=True)
        try:
            os.unlink(disposable)
        except FileNotFoundError:
            pass


def _run_and_verify(rom_path, jar, scratch, ours, java, runner, report, disposable, out, layout, protected_paths, before,
                    expected) -> bytes:
    source = scratch / "B2erCtrmapExtract.java"
    source.write_text(JAVA_SOURCE, encoding="utf-8")
    out.mkdir()
    runner = runner or (lambda r, o: java_runner(java or os.environ.get("JAVA") or "java", Path(jar), source, r, o))
    result = runner(disposable, out)
    (scratch / "ctrmap-extract.log").write_text((result.stdout or "") + (result.stderr or ""), encoding="utf-8")
    after_disposable = fingerprint(disposable)
    changed = (after_disposable["size"], after_disposable["sha256"]) != (expected["size"], expected["sha256"])
    after = {key: fingerprint(path) for key, path in zip(before, protected_paths)}
    keys = ("sha256", "size", "mode", "inode", "device", "links", "uid", "mtime_ns", "ctime_ns")
    protected_changed = [k for k in before if {f: before[k][f] for f in keys} != {f: after[k][f] for f in keys}]
    if report is not None:
        report.update({"disposable_input": DISPOSABLE_NAME, "disposable_mode": "0o600", "disposable_changed_by_ctrmap": changed,
                       "protected_inputs_unchanged": not protected_changed, "ctrmap_exit": result.returncode})
    if protected_changed:
        raise BuildError("a protected ROM input changed during CTRMap extraction")
    if result.returncode != 0:
        raise BuildError(f"CTRMap DSi extraction failed (exit {result.returncode}); see ctrmap-dsi/ctrmap-extract.log")
    if changed:
        raise BuildError("CTRMap unexpectedly modified its disposable input copy; extraction is not trusted")
    arm9i, problems = compare(ours, out, ours["arm9i.bin"], layout.modcrypt1[1])
    if problems:
        raise BuildError("CTRMap DSi extraction disagrees with the raw extraction: " + "; ".join(problems))
    return arm9i
