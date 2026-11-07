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

The allowlist is itself read from its stage-0 index blob, never from the
working tree, and fails closed: if it is missing, non-regular, conflicted,
malformed, contains duplicate paths, or names a path or content that another
rule prohibits, the check fails and nothing is authorized.

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

ALLOWLIST_PATH = "scripts/repository-safety-allowlist.json"
ALLOWLIST_SCHEMA_VERSION = 1
ALLOWLIST_MAX_SIZE = 1024 * 1024
ALLOWLIST_TOP_LEVEL_KEYS = frozenset({"schema_version", "description", "binaries"})
ALLOWLIST_ENTRY_KEYS = frozenset({"path", "sha256", "reason"})
REGULAR_MODES = frozenset({"100644", "100755"})


@dataclass(frozen=True)
class IndexEntry:
    mode: str
    object_id: str
    stage: str
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


def prohibited_content(head: bytes) -> str | None:
    """Magic and DS-header rules; never overridable by the allowlist."""
    magic = FORBIDDEN_MAGICS.get(head[:4])
    if magic is not None:
        return f"Nitro {magic} magic at offset 0"

    code = head[DS_GAME_CODE_OFFSET : DS_GAME_CODE_OFFSET + 4]
    if code in FORBIDDEN_GAME_CODES:
        return f"Nintendo DS header for {FORBIDDEN_GAME_CODES[code]} at offset 0x0C"
    return None


def content_violation(head: bytes, size: int, sha256: str, path: str, allowlist: dict[str, str]) -> str | None:
    prohibited = prohibited_content(head)
    if prohibited is not None:
        return prohibited

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
        mode, object_id, stage = meta.decode("ascii").split(" ")
        entries.append(IndexEntry(mode, object_id, stage, path.decode("utf-8", errors="surrogateescape")))
    return entries


class AllowlistError(ValueError):
    pass


def parse_allowlist(raw: bytes) -> dict[str, str]:
    """Validate allowlist JSON bytes and return {path: sha256}. Raises AllowlistError."""
    if len(raw) > ALLOWLIST_MAX_SIZE:
        raise AllowlistError(f"larger than {ALLOWLIST_MAX_SIZE} bytes")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AllowlistError(f"not valid UTF-8 JSON ({error})") from None
    if not isinstance(data, dict):
        raise AllowlistError("top level must be a JSON object")
    unknown = set(data) - ALLOWLIST_TOP_LEVEL_KEYS
    if unknown:
        raise AllowlistError(f"unknown top-level keys {sorted(unknown)}")
    if data.get("schema_version") != ALLOWLIST_SCHEMA_VERSION or isinstance(data.get("schema_version"), bool):
        raise AllowlistError(f"schema_version must be {ALLOWLIST_SCHEMA_VERSION}")
    binaries = data.get("binaries")
    if not isinstance(binaries, list):
        raise AllowlistError("'binaries' must be a list")

    allowed: dict[str, str] = {}
    for index, entry in enumerate(binaries):
        label = f"binaries[{index}]"
        if not isinstance(entry, dict) or set(entry) != ALLOWLIST_ENTRY_KEYS:
            raise AllowlistError(f"{label} must be an object with exactly {sorted(ALLOWLIST_ENTRY_KEYS)}")
        path, sha256, reason = entry["path"], entry["sha256"], entry["reason"]
        if not isinstance(path, str) or not path:
            raise AllowlistError(f"{label}.path must be a non-empty string")
        normalized = PurePosixPath(path)
        if (
            path != normalized.as_posix()
            or normalized.is_absolute()
            or ".." in normalized.parts
            or "\\" in path
        ):
            raise AllowlistError(f"{label}.path {path!r} must be a normalized repository-relative path")
        if not isinstance(sha256, str) or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise AllowlistError(f"{label}.sha256 must be 64 lowercase hex characters")
        if not isinstance(reason, str) or not reason.strip():
            raise AllowlistError(f"{label}.reason must be a non-empty string")
        if path in allowed:
            raise AllowlistError(f"duplicate path {path!r}")
        prohibited = path_violation(path)
        if prohibited:
            raise AllowlistError(f"{label}.path {path!r} is prohibited ({prohibited}) and cannot be allowlisted")
        allowed[path] = sha256
    return allowed


def _reject_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise AllowlistError(f"duplicate JSON keys {duplicates}")
    return dict(pairs)


def load_indexed_allowlist(repo: Path, entries: list[IndexEntry]) -> dict[str, str]:
    """Load the allowlist from its stage-0 index blob only. Raises AllowlistError."""
    matches = [entry for entry in entries if entry.path == ALLOWLIST_PATH]
    if not matches:
        raise AllowlistError("missing from the Git index")
    if len(matches) != 1 or matches[0].stage != "0":
        raise AllowlistError("conflicted in the Git index (non-stage-0 entries)")
    entry = matches[0]
    if entry.mode not in REGULAR_MODES:
        raise AllowlistError(f"index mode {entry.mode} is not a regular file")
    return parse_allowlist(git(repo, "cat-file", "blob", entry.object_id))


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
    violations: list[Violation] = []
    regular: list[IndexEntry] = []

    try:
        allowlist = load_indexed_allowlist(repo, entries)
    except AllowlistError as error:
        # Fail closed: report the defect and authorize nothing.
        violations.append(Violation(ALLOWLIST_PATH, f"allowlist invalid: {error}"))
        allowlist = {}

    for entry in entries:
        if entry.mode == "160000":
            violations.append(Violation(entry.path, "submodule (gitlink) entries are not allowed"))
            continue
        reason = path_violation(entry.path)
        if reason:
            violations.append(Violation(entry.path, reason))
            continue
        if entry.mode in REGULAR_MODES:
            regular.append(entry)

    for entry, head, size, sha256 in iter_blobs(repo, regular):
        reason = content_violation(head, size, sha256, entry.path, allowlist)
        if reason:
            violations.append(Violation(entry.path, reason))
        if entry.path in allowlist:
            prohibited = prohibited_content(head)
            if prohibited:
                violations.append(
                    Violation(
                        ALLOWLIST_PATH,
                        f"allowlist invalid: entry {entry.path!r} names content that is prohibited ({prohibited})",
                    )
                )
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
