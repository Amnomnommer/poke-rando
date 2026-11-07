#!/usr/bin/env python3
"""Reject prohibited game-derived material from the Git index.

Original project code, standard library only. Every check runs against the
blobs recorded in the index (what would be committed), not the working tree,
and filenames are read NUL-delimited so spaces, newlines, and non-ASCII names
are handled exactly.

Checks, in order, for every tracked entry:

1. Entry type: gitlinks (submodules) are rejected; symlinks are path-checked
   only (their blob is a link target string, never followed).
2. Path policy: forbidden directories anywhere in the path, forbidden file
   extensions, and forbidden exact basenames.
3. Content policy (regular files): NARC/Nitro container magics at offset 0,
   Nintendo DS ROM headers for the target game codes, and binary files larger
   than BINARY_SIZE_LIMIT unless allowlisted by exact path and SHA-256.

See docs/repository-safety.md for the rationale behind each rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# --- Path policy -----------------------------------------------------------

# Directory names rejected at any depth (case-insensitive, exact component).
FORBIDDEN_DIRECTORIES = frozenset(
    {
        # Upstream W2U trees identified in the R-001 audit.
        "dump",
        "vfs",
        "pmc",
        # Extracted file systems.
        "nitrofs",
        # Local inputs, caches, outputs, and evidence.
        "build",
        "output",
        "outputs",
        "cache",
        "temp",
        "tmp",
        "user-data",
    }
)

# Directory-name prefixes rejected at any depth (case-insensitive).
FORBIDDEN_DIRECTORY_PREFIXES = ("irdo_extracted", "ireo_extracted", "build-")

# Multi-component directory paths rejected at any depth (case-insensitive).
FORBIDDEN_DIRECTORY_SEQUENCES = (
    ("tester", "input"),
    ("tester", "cache"),
    ("tester", "output"),
    ("tester", "results"),
)

FORBIDDEN_EXTENSIONS = frozenset(
    {
        # ROMs, saves, emulator state, firmware, keys.
        "nds", "srl", "sav", "dsv", "duc", "mch",
        "ml1", "ml2", "ml3", "ml4", "ml5", "ml6", "ml7", "ml8",
        "state", "bios", "firmware", "nand", "key", "keys",
        # Nitro containers, graphics, fonts, and audio.
        "narc", "ncgr", "nclr", "nscr", "ncer", "nanr", "nmcr", "nmar",
        "nftr", "sdat", "sseq", "ssar", "sbnk", "swar", "strm",
        # Extracted or injected code.
        "overlay", "dll", "rpm",
        # Native dumps and compiled artifacts.
        "core",
    }
)

FORBIDDEN_BASENAMES = frozenset(
    {"header.bin", "arm7.bin", "arm9.bin", "y7.bin", "y9.bin", "banner.bin"}
)

# --- Content policy --------------------------------------------------------

# Magic values checked at offset 0. Nitro resources store their four-character
# identifier byte-reversed on disk (for example NCGR is written "RGCN"); both
# orientations are rejected so a reversed or normalized copy is also caught.
NITRO_FOURCCS = (
    "NARC",  # file archive
    "NCGR", "NCLR", "NSCR", "NCER", "NANR", "NMCR", "NMAR",  # 2D graphics
    "NFTR",  # font
    "SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "STRM",  # audio
)
FORBIDDEN_MAGICS: dict[bytes, str] = {}
for _fourcc in NITRO_FOURCCS:
    FORBIDDEN_MAGICS[_fourcc.encode("ascii")] = _fourcc
    FORBIDDEN_MAGICS[_fourcc[::-1].encode("ascii")] = _fourcc

# Nintendo DS header: 12-byte title, then the 4-byte game code at 0x0C.
DS_GAME_CODE_OFFSET = 0x0C
FORBIDDEN_GAME_CODES = {b"IRDO": "White 2 (IRDO)", b"IREO": "Black 2 (IREO)"}

# Conservative ceiling for tracked binary files. Project-original binary
# fixtures should be generated in tests instead of committed; a committed
# binary above this size must be allowlisted by exact path and SHA-256.
BINARY_SIZE_LIMIT = 64 * 1024

# Bytes inspected to classify text versus binary and to find magics.
SNIFF_SIZE = 8 * 1024

ALLOWLIST_RELATIVE_PATH = PurePosixPath("scripts/repository-safety-allowlist.json")


@dataclass(frozen=True)
class IndexEntry:
    mode: str
    object_id: str
    path: str


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str


def path_violation(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    lowered = [part.lower() for part in parts]
    directories = lowered[:-1]
    basename = lowered[-1] if lowered else ""

    for directory in directories:
        if directory in FORBIDDEN_DIRECTORIES:
            return f"forbidden directory '{directory}/'"
        for prefix in FORBIDDEN_DIRECTORY_PREFIXES:
            if directory.startswith(prefix):
                return f"forbidden directory '{directory}/'"
    for sequence in FORBIDDEN_DIRECTORY_SEQUENCES:
        width = len(sequence)
        for start in range(len(directories) - width + 1):
            if tuple(directories[start : start + width]) == sequence:
                return f"forbidden directory '{'/'.join(sequence)}/'"

    if basename in FORBIDDEN_BASENAMES:
        return f"forbidden file name '{basename}'"
    if "." in basename:
        extension = basename.rsplit(".", 1)[1]
        if extension in FORBIDDEN_EXTENSIONS:
            return f"forbidden extension '.{extension}'"
    return None


def is_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as error:
        # A multibyte character cut off by the sniff boundary is still text.
        return not (error.reason == "unexpected end of data" and error.start >= len(sample) - 3)
    return False


def content_violation(head: bytes, size: int, sha256: str, path: str, allowlist: dict[str, str]) -> str | None:
    magic = FORBIDDEN_MAGICS.get(head[:4])
    if magic is not None:
        return f"Nitro {magic} magic at offset 0"

    code = head[DS_GAME_CODE_OFFSET : DS_GAME_CODE_OFFSET + 4]
    if code in FORBIDDEN_GAME_CODES:
        return f"Nintendo DS header for {FORBIDDEN_GAME_CODES[code]} at offset 0x0C"

    if size > BINARY_SIZE_LIMIT and is_binary(head):
        if allowlist.get(path) == sha256:
            return None
        return (
            f"binary file of {size} bytes exceeds the {BINARY_SIZE_LIMIT}-byte limit "
            "and is not allowlisted by path and SHA-256"
        )
    return None


def git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def read_index(repo: Path) -> list[IndexEntry]:
    entries = []
    for record in git(repo, "ls-files", "-z", "--stage").split(b"\x00"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, object_id, _stage = meta.decode("ascii").split(" ")
        entries.append(IndexEntry(mode, object_id, path.decode("utf-8", errors="surrogateescape")))
    return entries


def load_allowlist(repo: Path) -> dict[str, str]:
    allowlist_path = repo / ALLOWLIST_RELATIVE_PATH
    if not allowlist_path.is_file():
        return {}
    data = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowed = {}
    for entry in data.get("binaries", []):
        path, sha256 = entry["path"], entry["sha256"].lower()
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError(f"allowlist entry for {path!r} has an invalid SHA-256")
        if not entry.get("reason"):
            raise ValueError(f"allowlist entry for {path!r} must state a reason")
        allowed[path] = sha256
    return allowed


def iter_blobs(repo: Path, entries: list[IndexEntry]):
    """Yield (entry, head, size, sha256) for each blob, streamed via cat-file."""
    if not entries:
        return
    request = b"".join(entry.object_id.encode("ascii") + b"\n" for entry in entries)
    process = subprocess.Popen(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None

    def feed() -> None:
        # Written from a separate thread: git emits output while still reading
        # requests, so a single-threaded write could fill both pipes and hang.
        try:
            process.stdin.write(request)
        finally:
            process.stdin.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    try:
        for entry in entries:
            header = process.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise RuntimeError(f"unexpected cat-file response for {entry.path!r}: {header!r}")
            size = int(header[2])
            digest = hashlib.sha256()
            head = b""
            remaining = size
            while remaining:
                chunk = process.stdout.read(min(remaining, 1 << 20))
                if not chunk:
                    raise RuntimeError(f"truncated blob for {entry.path!r}")
                if len(head) < SNIFF_SIZE:
                    head += chunk[: SNIFF_SIZE - len(head)]
                digest.update(chunk)
                remaining -= len(chunk)
            process.stdout.read(1)  # trailing LF
            yield entry, head, size, digest.hexdigest()
    finally:
        process.stdout.close()
        writer.join()
        if process.wait() != 0:
            raise RuntimeError("git cat-file --batch failed")


def scan(repo: Path) -> list[Violation]:
    entries = read_index(repo)
    allowlist = load_allowlist(repo)
    violations: list[Violation] = []
    regular: list[IndexEntry] = []

    for entry in entries:
        if entry.mode == "160000":
            violations.append(Violation(entry.path, "submodule (gitlink) entries are not allowed"))
            continue
        reason = path_violation(entry.path)
        if reason:
            violations.append(Violation(entry.path, reason))
            continue
        if entry.mode in ("100644", "100755"):
            regular.append(entry)

    for entry, head, size, sha256 in iter_blobs(repo, regular):
        reason = content_violation(head, size, sha256, entry.path, allowlist)
        if reason:
            violations.append(Violation(entry.path, reason))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root (default: cwd)")
    args = parser.parse_args(argv)

    try:
        violations = scan(args.repo)
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"Repository safety check could not run: {error}", file=sys.stderr)
        return 2

    if violations:
        print("Repository safety check failed. Prohibited tracked content:", file=sys.stderr)
        for violation in violations:
            # repr() keeps spaces, newlines, and control characters visible.
            print(f"  {violation.path!r}: {violation.reason}", file=sys.stderr)
        return 1
    print("Repository safety check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
