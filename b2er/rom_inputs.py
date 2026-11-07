"""Read-only verification of the two user-supplied clean ROM inputs.

Original project code, standard library only. Each explicitly supplied file is
checked against ``pins/inputs.json`` without being modified, copied, uploaded,
or exposed: reports contain hashes, sizes, identities, and paths, never ROM
bytes.

Safe reading
------------
* Each path is ``lstat``-ed first; a symlink or any non-regular file is rejected
  before it is opened.
* The file is opened with ``O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY |
  O_CLOEXEC`` (and ``O_NOATIME`` when the kernel permits it). ``O_NOFOLLOW``
  refuses a symlink swapped in after the ``lstat``; ``O_NONBLOCK`` keeps a FIFO
  from blocking the open. Where a flag is unavailable it is omitted and the
  fail-closed checks below still apply.
* ``fstat`` on the open descriptor must report a regular file whose device and
  inode equal the earlier ``lstat``. Otherwise the input is rejected.
* SHA-256 is streamed from the descriptor. The game code is taken from bytes
  ``0x0C..0x0F`` of the stream; no other byte is retained.
* After reading, ``fstat`` and ``lstat`` are repeated. Any change in device,
  inode, size, mtime, or ctime, a path now naming a different file, or a byte
  count that differs from the size, is reported as ``changed_during_read``.
* Nothing is ever written to an input or its containing directory. Reading may
  update the access time on file systems that track it when ``O_NOATIME`` is
  not permitted.

Exit codes: 0 both inputs verified, 1 verification failure, 2 operational error.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

REPORT_SCHEMA_VERSION = 1
TOOL_NAME = "verify-rom-inputs"

EXIT_VERIFIED = 0
EXIT_VERIFICATION_FAILED = 1
EXIT_OPERATIONAL_ERROR = 2

GAME_CODE_OFFSET = 0x0C
GAME_CODE_LENGTH = 4
HEADER_MINIMUM = GAME_CODE_OFFSET + GAME_CODE_LENGTH
CHUNK_SIZE = 1 << 20

# Roles are fixed by contract: the CLI's --white2/--black2 map to these pin ids.
ROLES = (("white2", "IRDO"), ("black2", "IREO"))

# Defect categories.
WRONG_HASH = "wrong_hash"
WRONG_GAME_CODE = "wrong_game_code"
TRUNCATED_HEADER = "truncated_header"
WRONG_SIZE = "wrong_size"
NOT_REGULAR_FILE = "not_regular_file"
DUPLICATE_IDENTITY = "duplicate_identity"
CHANGED_DURING_READ = "changed_during_read"

UNRESOLVED_PIN = "unresolved_pin"

HEX64 = re.compile(r"[0-9a-f]{64}")
GAME_CODE = re.compile(r"[A-Z0-9]{4}")
INPUT_KEYS = frozenset({"id", "title", "region", "game_code", "role", "sha256", "size_bytes", "evidence"})
TOP_LEVEL_KEYS = frozenset({"schema_version", "description", "inputs"})


class OperationalError(Exception):
    """Verification could not be performed (exit code 2)."""


class PinError(OperationalError):
    """The pin file is missing or malformed."""


# --- Pins -------------------------------------------------------------------


@dataclass(frozen=True)
class InputPin:
    id: str
    game_code: str
    sha256: str
    size_bytes: int | None  # None when unresolved
    size_reason: str


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PinError(message)


def _no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise PinError(f"duplicate JSON keys {duplicates}")
    return dict(pairs)


def load_input_pins(path: Path) -> dict[str, InputPin]:
    """Strictly parse pins/inputs.json. Returns {role id: pin}. Raises PinError."""
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise PinError(f"cannot read pin file: {error.strerror or error}") from None
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PinError(f"pin file is not valid UTF-8 JSON: {error}") from None

    _require(isinstance(data, dict), "pin file top level must be an object")
    unknown = set(data) - TOP_LEVEL_KEYS
    _require(not unknown, f"unknown top-level keys {sorted(unknown)}")
    _require(data.get("schema_version") == 1 and not isinstance(data.get("schema_version"), bool), "schema_version must be 1")
    entries = data.get("inputs")
    _require(isinstance(entries, list), "'inputs' must be a list")

    pins: dict[str, InputPin] = {}
    seen_codes: set[str] = set()
    seen_hashes: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"inputs[{index}]"
        _require(isinstance(entry, dict), f"{where} must be an object")
        unknown = set(entry) - INPUT_KEYS
        _require(not unknown, f"{where}: unknown keys {sorted(unknown)}")
        missing = {"id", "game_code", "role", "sha256", "size_bytes"} - set(entry)
        _require(not missing, f"{where}: missing keys {sorted(missing)}")
        pin_id, code, digest, role = entry["id"], entry["game_code"], entry["sha256"], entry["role"]
        _require(isinstance(pin_id, str) and pin_id != "", f"{where}.id must be a non-empty string")
        _require(isinstance(role, str) and role != "", f"{where}.role must be a non-empty string")
        _require(isinstance(code, str) and GAME_CODE.fullmatch(code) is not None, f"{where}.game_code must be 4 uppercase letters or digits")
        _require(isinstance(digest, str) and HEX64.fullmatch(digest) is not None, f"{where}.sha256 must be 64 lowercase hex characters")
        _require(pin_id not in pins, f"duplicate input id {pin_id!r}")
        _require(code not in seen_codes, f"duplicate game code {code!r}")
        _require(digest not in seen_hashes, f"duplicate sha256 for {pin_id!r}")
        seen_codes.add(code)
        seen_hashes.add(digest)

        size = entry["size_bytes"]
        _require(isinstance(size, dict) and set(size) == {"status", "value", "reason"},
                 f"{where}.size_bytes must be a status field with exactly status/value/reason")
        _require(isinstance(size["reason"], str) and size["reason"].strip() != "", f"{where}.size_bytes.reason must be non-empty")
        if size["status"] == "unresolved":
            _require(size["value"] is None, f"{where}.size_bytes: unresolved field must have a null value")
            size_value = None
        else:
            _require(size["status"] == "resolved", f"{where}.size_bytes.status must be 'resolved' or 'unresolved'")
            _require(isinstance(size["value"], int) and not isinstance(size["value"], bool) and size["value"] > 0,
                     f"{where}.size_bytes.value must be a positive integer")
            size_value = size["value"]
        pins[pin_id] = InputPin(pin_id, code, digest, size_value, size["reason"])

    expected = dict(ROLES)
    _require(set(pins) == set(expected), f"inputs must be exactly {sorted(expected)}")
    for pin_id, code in expected.items():
        _require(pins[pin_id].game_code == code, f"input {pin_id!r} must have game code {code!r}")
    return pins


# --- Reading ----------------------------------------------------------------


@dataclass(frozen=True)
class InputObservation:
    """Everything observed about one input. Contains no file bytes."""

    role: str
    path: str
    expected_game_code: str
    expected_sha256: str
    file_type: str
    sha256: str | None
    size: int | None
    device: int | None
    inode: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    observed_game_code: str | None
    defects: tuple[dict, ...]

    def fingerprint(self) -> dict:
        """Stable identity used to compare a pre-build and post-build observation."""
        return {
            "role": self.role,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "device": self.device,
            "inode": self.inode,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
        }


def _open_flags() -> int:
    flags = os.O_RDONLY
    for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_NOCTTY", "O_CLOEXEC", "O_BINARY"):
        flags |= getattr(os, name, 0)
    return flags


def _describe_mode(mode: int) -> str:
    for test, name in (
        (stat.S_ISREG, "regular file"),
        (stat.S_ISDIR, "directory"),
        (stat.S_ISLNK, "symlink"),
        (stat.S_ISFIFO, "FIFO"),
        (stat.S_ISSOCK, "socket"),
        (stat.S_ISCHR, "character device"),
        (stat.S_ISBLK, "block device"),
    ):
        if test(mode):
            return name
    return "unknown file type"


def _metadata(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_IFMT(info.st_mode))


def _printable_code(raw: bytes) -> str | None:
    """A game code is reported only when it is a well-formed identifier, never as raw bytes."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return text if GAME_CODE.fullmatch(text) else None


def _observe(role: str, path: Path, pin: InputPin, *, chunk_size: int, after_chunk) -> InputObservation:
    display = os.path.abspath(os.fspath(path))
    if "\x00" in display:
        raise OperationalError(f"{role}: path contains a NUL character")
    defects: list[dict] = []

    def observation(file_type: str, **fields) -> InputObservation:
        base = dict(sha256=None, size=None, device=None, inode=None, mtime_ns=None, ctime_ns=None, observed_game_code=None)
        base.update(fields)
        return InputObservation(role, display, pin.game_code, pin.sha256, file_type, defects=tuple(defects), **base)

    try:
        before_path = os.lstat(display)
    except FileNotFoundError:
        raise OperationalError(f"{role}: input does not exist: {display}") from None
    except OSError as error:
        raise OperationalError(f"{role}: cannot inspect input {display}: {error.strerror}") from None

    if not stat.S_ISREG(before_path.st_mode):
        kind = _describe_mode(before_path.st_mode)
        defects.append({"category": NOT_REGULAR_FILE, "detail": f"input is a {kind}, not a regular file"})
        return observation(kind)

    flags = _open_flags()
    try:
        try:
            fd = os.open(display, flags | getattr(os, "O_NOATIME", 0))
        except PermissionError:
            fd = os.open(display, flags)  # O_NOATIME needs ownership; retry without it
    except OSError as error:
        if error.errno == errno.ELOOP:
            defects.append({"category": NOT_REGULAR_FILE, "detail": "input became a symlink before it was opened"})
            return observation("symlink")
        raise OperationalError(f"{role}: cannot open input {display}: {error.strerror}") from None

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before_path.st_dev, before_path.st_ino):
            defects.append({"category": NOT_REGULAR_FILE, "detail": "opened file is not the regular file that was inspected"})
            return observation(_describe_mode(opened.st_mode))

        digest = hashlib.sha256()
        header = b""
        total = 0
        index = 0
        while True:
            try:
                chunk = os.read(fd, chunk_size)
            except OSError as error:
                raise OperationalError(f"{role}: cannot read input {display}: {error.strerror}") from None
            if not chunk:
                break
            if len(header) < HEADER_MINIMUM:
                header += chunk[: HEADER_MINIMUM - len(header)]
            digest.update(chunk)
            total += len(chunk)
            if after_chunk is not None:
                after_chunk(role, index)
            index += 1
        after = os.fstat(fd)
    finally:
        os.close(fd)

    try:
        after_path = os.lstat(display)
        path_same = (after_path.st_dev, after_path.st_ino) == (opened.st_dev, opened.st_ino)
    except OSError:
        path_same = False

    changes = []
    if _metadata(after) != _metadata(opened):
        changes.append("file metadata changed while reading")
    if total != opened.st_size:
        changes.append(f"read {total} bytes but the file reported {opened.st_size}")
    if not path_same:
        changes.append("path no longer names the file that was read")
    if changes:
        defects.append({"category": CHANGED_DURING_READ, "detail": "; ".join(changes)})

    sha256 = digest.hexdigest()
    observed_code = None
    if len(header) < HEADER_MINIMUM:
        defects.append({"category": TRUNCATED_HEADER, "detail": f"file has {total} bytes; at least {HEADER_MINIMUM} are needed for the game code"})
    else:
        raw_code = header[GAME_CODE_OFFSET:HEADER_MINIMUM]
        observed_code = _printable_code(raw_code)
        if raw_code != pin.game_code.encode("ascii"):
            defects.append({"category": WRONG_GAME_CODE, "detail": f"game code at 0x0C is not {pin.game_code}"})
    if sha256 != pin.sha256:
        defects.append({"category": WRONG_HASH, "detail": f"SHA-256 does not match the approved {role} hash"})
    if pin.size_bytes is not None and total != pin.size_bytes:
        defects.append({"category": WRONG_SIZE, "detail": f"size {total} does not match the approved {pin.size_bytes}"})

    return observation(
        "regular file",
        sha256=sha256,
        size=total,
        device=opened.st_dev,
        inode=opened.st_ino,
        mtime_ns=opened.st_mtime_ns,
        ctime_ns=opened.st_ctime_ns,
        observed_game_code=observed_code,
    )


# --- Library API --------------------------------------------------------------


@dataclass(frozen=True)
class InputVerification:
    pins_path: str
    inputs: tuple[InputObservation, ...]
    defects: tuple[dict, ...]
    warnings: tuple[dict, ...]

    @property
    def verified(self) -> bool:
        return not self.defects

    @property
    def exit_code(self) -> int:
        return EXIT_VERIFIED if self.verified else EXIT_VERIFICATION_FAILED

    def fingerprint(self) -> dict:
        """Stable comparison key. Equal before/after fingerprints mean the inputs are unchanged."""
        return {"verified": self.verified, "inputs": [item.fingerprint() for item in self.inputs]}

    def report(self) -> dict:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "result": "verified" if self.verified else "failed",
            "exit_code": self.exit_code,
            "local_only": "Contains absolute local paths and file identities; never commit or publish this report.",
            "pins": self.pins_path,
            "inputs": [
                {
                    "role": item.role,
                    "path": item.path,
                    "expected": {"game_code": item.expected_game_code, "sha256": item.expected_sha256},
                    "observed": {
                        "file_type": item.file_type,
                        "game_code": item.observed_game_code,
                        "sha256": item.sha256,
                        "size_bytes": item.size,
                        "device": item.device,
                        "inode": item.inode,
                        "mtime_ns": item.mtime_ns,
                        "ctime_ns": item.ctime_ns,
                    },
                    "status": "fail" if item.defects else "pass",
                    "defects": list(item.defects),
                }
                for item in self.inputs
            ],
            "defects": list(self.defects),
            "warnings": list(self.warnings),
        }


def verify_inputs(
    white2: os.PathLike | str,
    black2: os.PathLike | str,
    pins_path: os.PathLike | str,
    *,
    chunk_size: int = CHUNK_SIZE,
    after_chunk=None,
) -> InputVerification:
    """Verify both inputs. Raises OperationalError for exit-code-2 conditions.

    ``after_chunk(role, index)`` is a test seam called after each chunk is
    hashed; production callers leave it unset.
    """
    if chunk_size <= 0:
        raise OperationalError("chunk_size must be positive")
    pins = load_input_pins(Path(pins_path))
    observations = [
        _observe(role, Path(path), pins[role], chunk_size=chunk_size, after_chunk=after_chunk)
        for role, path in (("white2", white2), ("black2", black2))
    ]

    defects = [
        {"role": item.role, **defect} for item in observations for defect in item.defects
    ]
    white, black = observations
    same_path = white.path == black.path
    same_file = white.inode is not None and (white.device, white.inode) == (black.device, black.inode)
    if same_path or same_file:
        defects.append({"role": "both", "category": DUPLICATE_IDENTITY,
                        "detail": "the same file was supplied for both roles" if same_path else "both paths name the same file (hard link)"})
    for item, other in ((white, black), (black, white)):
        if item.sha256 is not None and item.sha256 == pins[other.role].sha256 and item.sha256 != pins[item.role].sha256:
            defects.append({"role": item.role, "category": WRONG_HASH,
                            "detail": f"input matches the approved {other.role} hash; the paths appear swapped"})

    warnings = [
        {"category": UNRESOLVED_PIN, "subject": f"/inputs/{role}/size_bytes",
         "detail": f"{pins[role].size_reason} Observed size is reported as evidence only."}
        for role, _ in ROLES
        if pins[role].size_bytes is None
    ]
    defects.sort(key=lambda item: (item["role"], item["category"], item["detail"]))
    return InputVerification(os.path.abspath(os.fspath(pins_path)), tuple(observations), tuple(defects), tuple(warnings))


def compare_fingerprints(before: InputVerification, after: InputVerification) -> list[str]:
    """List differences between two verifications; empty means the inputs are unchanged."""
    differences = []
    if before.verified != after.verified:
        differences.append(f"verified changed from {before.verified} to {after.verified}")
    for old, new in zip(before.fingerprint()["inputs"], after.fingerprint()["inputs"]):
        for key in old:
            if old[key] != new[key]:
                differences.append(f"{old['role']}.{key} changed")
    return differences


def error_report(message: str) -> dict:
    return {"schema_version": REPORT_SCHEMA_VERSION, "tool": TOOL_NAME, "result": "error",
            "exit_code": EXIT_OPERATIONAL_ERROR, "error": message}


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def render_text(report: dict) -> str:
    if report["result"] == "error":
        return f"ROM input verification could not run: {report['error']}\n"
    lines = [f"ROM input verification: {report['result'].upper()}", f"Pins: {report['pins']}", ""]
    for item in report["inputs"]:
        observed = item["observed"]
        lines += [
            f"[{item['status'].upper()}] {item['role']}: {item['path']}",
            f"  type:      {observed['file_type']}",
            f"  game code: expected {item['expected']['game_code']}, observed {observed['game_code']}",
            f"  sha256:    expected {item['expected']['sha256']}",
            f"             observed {observed['sha256']}",
            f"  size:      {observed['size_bytes']} bytes (evidence)",
        ]
    lines.append("")
    lines.append(f"Defects: {len(report['defects'])}")
    for defect in report["defects"]:
        lines.append(f"  {defect['role']}: {defect['category']}: {defect['detail']}")
    lines.append(f"Warnings: {len(report['warnings'])}")
    for warning in report["warnings"]:
        lines.append(f"  {warning['category']}: {warning['subject']}: {warning['detail']}")
    lines.append("")
    lines.append("This report contains local paths; do not commit it.")
    return "\n".join(lines) + "\n"
