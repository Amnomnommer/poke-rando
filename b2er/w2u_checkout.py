"""Read-only, offline verification of a user-supplied local W2U checkout.

Original project code, standard library only. The verifier establishes whether
a local checkout matches the approved pins in ``pins/upstream.json`` without
copying, modifying, building, or executing upstream content.

Read-only guarantees
--------------------
* Only read-only Git plumbing is invoked: ``rev-parse``, ``ls-tree``,
  ``ls-files``, and ``config --get``. No checkout, reset, clean, fetch,
  submodule update, add, or commit is ever run.
* Git runs with system and global configuration disabled, optional locks off
  (so the index is never refreshed or rewritten), the file-system monitor and
  hooks disabled, the untracked cache disabled, and every transport protocol
  forbidden. Repository discovery cannot climb above the given path.
* Worktree contents are compared with the index by computing Git blob IDs in
  Python. Git clean/smudge filters, textconv drivers, and other configured
  programs are therefore never invoked.
* File contents are read only to compute hashes. Reports contain paths,
  categories, and hashes, never file contents.

Exit codes: 0 verified, 1 verification failure, 2 operational error.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REPORT_SCHEMA_VERSION = 1
TOOL_NAME = "verify-w2u-checkout"

EXIT_VERIFIED = 0
EXIT_VERIFICATION_FAILED = 1
EXIT_OPERATIONAL_ERROR = 2

SWAN_ID = "swan"

# Defect categories, each reported distinctly.
WRONG_COMMIT = "wrong_commit"
TRACKED_MODIFICATION = "tracked_modification"
CONFLICTED_ENTRY = "conflicted_entry"
UNTRACKED_FILE = "untracked_file"
IGNORED_FILE = "ignored_file"
MISSING_DEPENDENCY = "missing_dependency"
WRONG_HASH = "wrong_hash"

# Warning categories.
UNRESOLVED_PIN = "unresolved_pin"
UNVERIFIED_DEPENDENCY = "unverified_dependency"
DEPENDENCY_NOT_INITIALIZED = "dependency_not_initialized"

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")

GITLINK_MODE = "160000"
SYMLINK_MODE = "120000"
REGULAR_MODES = frozenset({"100644", "100755"})

_SAFE_CONFIG = (
    "core.fsmonitor=false",
    f"core.hooksPath={os.devnull}",
    "core.untrackedCache=false",
    "protocol.allow=never",
    "submodule.recurse=false",
)


class OperationalError(Exception):
    """The verification could not be performed (exit code 2)."""


class PinError(OperationalError):
    """The pin file is missing or malformed."""


# --- Pins -------------------------------------------------------------------


@dataclass(frozen=True)
class BinaryPin:
    id: str
    path: str
    sha256: str | None  # None when unresolved


@dataclass(frozen=True)
class Pins:
    repository: str
    commit: str
    swan_path: str
    swan_commit: str
    binaries: tuple[BinaryPin, ...]
    warnings: tuple[dict, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PinError(message)


def _is_status_field(value) -> bool:
    return isinstance(value, dict) and "status" in value


def _check_status_field(value, where: str) -> None:
    _require(set(value) == {"status", "value", "reason"}, f"{where}: status field must have exactly status/value/reason")
    _require(value["status"] in ("resolved", "unresolved"), f"{where}: status must be 'resolved' or 'unresolved'")
    _require(isinstance(value["reason"], str) and value["reason"].strip() != "", f"{where}: reason must be non-empty")
    if value["status"] == "unresolved":
        _require(value["value"] is None, f"{where}: unresolved field must have a null value")
    else:
        _require(value["value"] is not None, f"{where}: resolved field must have a value")


def _check_relative_path(path, where: str) -> None:
    _require(isinstance(path, str) and path != "", f"{where}: path must be a non-empty string")
    pure = PurePosixPath(path)
    _require(
        pure.as_posix() == path and not pure.is_absolute() and ".." not in pure.parts and "\\" not in path,
        f"{where}: path {path!r} must be a normalized relative POSIX path",
    )


def _walk_status_fields(node, pointer: str):
    if isinstance(node, dict):
        if _is_status_field(node):
            yield pointer, node
            return
        for key in sorted(node):
            yield from _walk_status_fields(node[key], f"{pointer}/{_escape_pointer(key)}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk_status_fields(item, f"{pointer}/{index}")


def _escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def load_pins(path: Path) -> Pins:
    """Parse and validate pins/upstream.json. Raises PinError on any defect."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise PinError(f"cannot read pin file: {error.strerror or error}") from None
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PinError(f"pin file is not valid UTF-8 JSON: {error}") from None
    except PinError:
        raise

    _require(isinstance(data, dict), "pin file top level must be an object")
    _require(data.get("schema_version") == 1 and not isinstance(data.get("schema_version"), bool), "schema_version must be 1")

    upstream = data.get("upstream")
    _require(isinstance(upstream, dict), "'upstream' must be an object")
    _require(isinstance(upstream.get("repository"), str) and upstream["repository"], "upstream.repository must be a string")
    _require(isinstance(upstream.get("commit"), str) and HEX40.fullmatch(upstream["commit"]) is not None,
             "upstream.commit must be 40 lowercase hex characters")

    for pointer, value in _walk_status_fields(data, ""):
        _check_status_field(value, pointer or "/")

    dependencies = data.get("source_dependencies")
    _require(isinstance(dependencies, list), "'source_dependencies' must be a list")
    seen_ids: set[str] = set()
    swan = None
    warnings: list[dict] = []
    for index, dependency in enumerate(dependencies):
        where = f"source_dependencies[{index}]"
        _require(isinstance(dependency, dict), f"{where} must be an object")
        dep_id = dependency.get("id")
        _require(isinstance(dep_id, str) and dep_id, f"{where}.id must be a string")
        _require(dep_id not in seen_ids, f"duplicate source dependency id {dep_id!r}")
        seen_ids.add(dep_id)
        _check_relative_path(dependency.get("path"), f"{where}.path")
        commit = dependency.get("commit")
        _require(
            (isinstance(commit, str) and HEX40.fullmatch(commit) is not None) or _is_status_field(commit),
            f"{where}.commit must be 40 lowercase hex characters or an explicit status field",
        )
        if dep_id == SWAN_ID:
            _require(dependency.get("storage") == "submodule", f"{where}: swan must have storage 'submodule'")
            _require(isinstance(commit, str), f"{where}: swan commit must be resolved")
            swan = dependency
        else:
            warnings.append(
                {
                    "category": UNVERIFIED_DEPENDENCY,
                    "subject": dependency["path"],
                    "detail": f"{dep_id} ({dependency.get('storage', 'unknown storage')}) is not verified by this tool"
                    + (f"; evidence: {dependency['commit_evidence']}" if isinstance(dependency.get("commit_evidence"), str) else ""),
                }
            )
    _require(swan is not None, "source_dependencies must contain the 'swan' submodule pin")

    binaries_raw = data.get("external_binaries")
    _require(isinstance(binaries_raw, list), "'external_binaries' must be a list")
    binaries: list[BinaryPin] = []
    seen_ids = set()
    seen_paths: set[str] = set()
    for index, entry in enumerate(binaries_raw):
        where = f"external_binaries[{index}]"
        _require(isinstance(entry, dict), f"{where} must be an object")
        bin_id = entry.get("id")
        _require(isinstance(bin_id, str) and bin_id, f"{where}.id must be a string")
        _require(bin_id not in seen_ids, f"duplicate external binary id {bin_id!r}")
        seen_ids.add(bin_id)
        _check_relative_path(entry.get("path"), f"{where}.path")
        _require(entry["path"] not in seen_paths, f"duplicate external binary path {entry['path']!r}")
        seen_paths.add(entry["path"])
        digest = entry.get("sha256")
        if _is_status_field(digest):
            _require(digest["status"] == "unresolved", f"{where}.sha256: a resolved hash must be a plain string")
            binaries.append(BinaryPin(bin_id, entry["path"], None))
        else:
            _require(isinstance(digest, str) and HEX64.fullmatch(digest) is not None,
                     f"{where}.sha256 must be 64 lowercase hex characters or an unresolved field")
            binaries.append(BinaryPin(bin_id, entry["path"], digest))

    for pointer, value in _walk_status_fields(data, ""):
        if value["status"] == "unresolved":
            warnings.append({"category": UNRESOLVED_PIN, "subject": pointer, "detail": value["reason"]})

    return Pins(
        repository=upstream["repository"],
        commit=upstream["commit"],
        swan_path=swan["path"],
        swan_commit=swan["commit"],
        binaries=tuple(binaries),
        warnings=tuple(warnings),
    )


def _no_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise PinError(f"duplicate JSON keys {duplicates}")
    return dict(pairs)


# --- Read-only Git access ----------------------------------------------------


class ReadOnlyGit:
    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_CEILING_DIRECTORIES": str(worktree.parent),
                "GIT_PAGER": "cat",
                "LC_ALL": "C",
            }
        )
        self.env = env

    def run(self, *args: str, allow_failure: bool = False) -> bytes | None:
        command = ["git", "-C", str(self.worktree)]
        for setting in _SAFE_CONFIG:
            command += ["-c", setting]
        command += list(args)
        try:
            result = subprocess.run(command, env=self.env, stdin=subprocess.DEVNULL, capture_output=True, check=False)
        except OSError as error:
            raise OperationalError(f"cannot run git: {error}") from None
        if result.returncode != 0:
            if allow_failure:
                return None
            message = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise OperationalError(f"git {args[0]} failed: {message[-1] if message else 'unknown error'}")
        return result.stdout


def _split_z(data: bytes) -> list[str]:
    return [item.decode("utf-8", errors="surrogateescape") for item in data.split(b"\x00") if item]


def _open_worktree(path: Path) -> tuple[ReadOnlyGit, str]:
    if not path.exists():
        raise OperationalError(f"checkout path does not exist: {path}")
    if not path.is_dir():
        raise OperationalError(f"checkout path is not a directory: {path}")
    git = ReadOnlyGit(path)
    inside = git.run("rev-parse", "--is-inside-work-tree", allow_failure=True)
    if inside is None or inside.strip() != b"true":
        raise OperationalError(f"not a Git worktree: {path}")
    toplevel = Path(git.run("rev-parse", "--show-toplevel").decode("utf-8", errors="surrogateescape").strip())
    if toplevel.resolve() != path.resolve():
        raise OperationalError(f"path is not the top level of its Git worktree (top level: {toplevel})")
    object_format = git.run("rev-parse", "--show-object-format", allow_failure=True)
    fmt = object_format.decode().strip() if object_format else "sha1"
    if fmt not in ("sha1", "sha256"):
        raise OperationalError(f"unsupported Git object format {fmt!r}")
    return git, fmt


def _blob_id(fmt: str, payload_size: int, chunks) -> str:
    digest = hashlib.sha1() if fmt == "sha1" else hashlib.sha256()
    digest.update(b"blob %d\x00" % payload_size)
    for chunk in chunks:
        digest.update(chunk)
    return digest.hexdigest()


def _file_chunks(path: Path):
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            yield chunk


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    for chunk in _file_chunks(path):
        digest.update(chunk)
    return digest.hexdigest()


# --- Checkout-state inspection ----------------------------------------------


@dataclass
class Findings:
    defects: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    checks: dict[str, dict] = field(default_factory=dict)

    def defect(self, category: str, subject: str, detail: str, **extra) -> None:
        self.defects.append({"category": category, "subject": subject, "detail": detail, **extra})

    def warn(self, category: str, subject: str, detail: str) -> None:
        self.warnings.append({"category": category, "subject": subject, "detail": detail})


def _head_commit(git: ReadOnlyGit) -> str | None:
    out = git.run("rev-parse", "--verify", "--quiet", "HEAD^{commit}", allow_failure=True)
    return out.decode().strip() if out else None


def _parse_ls_tree(data: bytes) -> dict[str, tuple[str, str]]:
    entries = {}
    for record in _split_z(data):
        meta, path = record.split("\t", 1)
        mode, _kind, oid = meta.split(" ")
        entries[path] = (mode, oid)
    return entries


def _inspect_state(git: ReadOnlyGit, fmt: str, prefix: str, findings: Findings, skip: frozenset[str]) -> None:
    """Report staged/unstaged tracked changes, conflicts, untracked and ignored files."""
    root = git.worktree
    head = _parse_ls_tree(git.run("ls-tree", "-r", "-z", "--full-tree", "HEAD")) if _head_commit(git) else {}

    index: dict[str, tuple[str, str]] = {}
    conflicted: set[str] = set()
    for record in _split_z(git.run("ls-files", "-z", "--stage")):
        meta, path = record.split("\t", 1)
        mode, oid, stage = meta.split(" ")
        if stage != "0":
            conflicted.add(path)
        else:
            index[path] = (mode, oid)
    for path in sorted(conflicted):
        findings.defect(CONFLICTED_ENTRY, prefix + path, "unmerged index entry")

    for path in sorted(set(head) | set(index)):
        if path in conflicted:
            continue
        if path not in index:
            findings.defect(TRACKED_MODIFICATION, prefix + path, "deleted from the index", kind="staged")
        elif path not in head:
            findings.defect(TRACKED_MODIFICATION, prefix + path, "added to the index", kind="staged")
        elif head[path] != index[path]:
            findings.defect(TRACKED_MODIFICATION, prefix + path, "index differs from HEAD", kind="staged")

    filemode_raw = git.run("config", "--type=bool", "--get", "core.filemode", allow_failure=True)
    trust_filemode = filemode_raw is None or filemode_raw.strip() != b"false"

    for path in sorted(index):
        mode, oid = index[path]
        if mode == GITLINK_MODE or path in skip:
            continue
        reason = _worktree_difference(root / path, mode, oid, fmt, trust_filemode)
        if reason:
            findings.defect(TRACKED_MODIFICATION, prefix + path, reason, kind="worktree")

    for path in _split_z(git.run("ls-files", "-z", "--others", "--exclude-standard")):
        findings.defect(UNTRACKED_FILE, prefix + path, "untracked file")
    for path in _split_z(git.run("ls-files", "-z", "--others", "--ignored", "--exclude-standard")):
        findings.defect(IGNORED_FILE, prefix + path, "ignored file (disallowed in a verified checkout)")


def _worktree_difference(path: Path, mode: str, oid: str, fmt: str, trust_filemode: bool) -> str | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "deleted from the worktree"
    except OSError as error:
        return f"unreadable ({error.strerror})"
    if mode == SYMLINK_MODE:
        if not stat.S_ISLNK(info.st_mode):
            return "type changed (expected a symlink)"
        target = os.fsencode(os.readlink(path))
        return None if _blob_id(fmt, len(target), [target]) == oid else "symlink target modified"
    if mode in REGULAR_MODES:
        if not stat.S_ISREG(info.st_mode):
            return "type changed (expected a regular file)"
        if trust_filemode and bool(info.st_mode & stat.S_IXUSR) != (mode == "100755"):
            return "file mode changed"
        try:
            actual = _blob_id(fmt, info.st_size, _file_chunks(path))
        except OSError as error:
            return f"unreadable ({error.strerror})"
        return None if actual == oid else "content modified"
    return f"unsupported index mode {mode}"


def _inspect_swan(git: ReadOnlyGit, pins: Pins, findings: Findings) -> None:
    path = pins.swan_path
    entry = _parse_ls_tree(git.run("ls-tree", "-z", "--full-tree", "HEAD", "--", path)).get(path) if _head_commit(git) else None
    if entry is None:
        findings.defect(MISSING_DEPENDENCY, path, "swan gitlink is absent from HEAD")
        findings.checks["swan_gitlink"] = {"status": "fail", "expected": pins.swan_commit, "actual": None}
        return
    mode, oid = entry
    if mode != GITLINK_MODE:
        findings.defect(MISSING_DEPENDENCY, path, f"swan path is not a gitlink (mode {mode})")
        findings.checks["swan_gitlink"] = {"status": "fail", "expected": pins.swan_commit, "actual": None}
        return
    if oid != pins.swan_commit:
        findings.defect(WRONG_COMMIT, path, "swan gitlink commit does not match the pin", expected=pins.swan_commit, actual=oid)
        findings.checks["swan_gitlink"] = {"status": "fail", "expected": pins.swan_commit, "actual": oid}
    else:
        findings.checks["swan_gitlink"] = {"status": "pass", "expected": pins.swan_commit, "actual": oid}

    sub_root = git.worktree / path
    if not os.path.lexists(sub_root / ".git"):
        findings.warn(DEPENDENCY_NOT_INITIALIZED, path, "swan submodule is not initialized; checked-out commit not verified")
        findings.checks["swan_checkout"] = {"status": "warn", "expected": pins.swan_commit, "actual": None}
        return
    try:
        sub_git, sub_fmt = _open_worktree(sub_root)
    except OperationalError as error:
        findings.defect(MISSING_DEPENDENCY, path, f"swan checkout is unreadable: {error}")
        findings.checks["swan_checkout"] = {"status": "fail", "expected": pins.swan_commit, "actual": None}
        return
    actual = _head_commit(sub_git)
    if actual != pins.swan_commit:
        findings.defect(WRONG_COMMIT, path, "swan checked-out commit does not match the pin", expected=pins.swan_commit, actual=actual)
        findings.checks["swan_checkout"] = {"status": "fail", "expected": pins.swan_commit, "actual": actual}
    else:
        findings.checks["swan_checkout"] = {"status": "pass", "expected": pins.swan_commit, "actual": actual}
    _inspect_state(sub_git, sub_fmt, path + "/", findings, frozenset())


def _inspect_binaries(root: Path, pins: Pins, findings: Findings) -> None:
    resolved_root = root.resolve()
    for binary in sorted(pins.binaries, key=lambda item: item.id):
        check_id = f"binary:{binary.id}"
        if binary.sha256 is None:
            findings.checks[check_id] = {"status": "warn", "path": binary.path, "expected": None, "actual": None}
            continue
        target = root / binary.path
        try:
            info = target.lstat()
        except FileNotFoundError:
            findings.defect(MISSING_DEPENDENCY, binary.path, f"external binary {binary.id} is missing")
            findings.checks[check_id] = {"status": "fail", "path": binary.path, "expected": binary.sha256, "actual": None}
            continue
        if not stat.S_ISREG(info.st_mode) or resolved_root not in target.resolve().parents:
            findings.defect(MISSING_DEPENDENCY, binary.path, f"external binary {binary.id} is not a regular file inside the checkout")
            findings.checks[check_id] = {"status": "fail", "path": binary.path, "expected": binary.sha256, "actual": None}
            continue
        try:
            actual = _sha256_file(target)
        except OSError as error:
            raise OperationalError(f"cannot read external binary {binary.path}: {error.strerror}") from None
        if actual != binary.sha256:
            findings.defect(WRONG_HASH, binary.path, f"external binary {binary.id} SHA-256 does not match the pin",
                            expected=binary.sha256, actual=actual)
            findings.checks[check_id] = {"status": "fail", "path": binary.path, "expected": binary.sha256, "actual": actual}
        else:
            findings.checks[check_id] = {"status": "pass", "path": binary.path, "expected": binary.sha256, "actual": actual}


# --- Entry point ------------------------------------------------------------


def verify(checkout: Path, pins_path: Path) -> dict:
    """Verify a checkout. Returns a report; raises OperationalError for exit code 2."""
    pins = load_pins(pins_path)
    git, fmt = _open_worktree(checkout)
    findings = Findings()
    findings.warnings.extend(dict(item) for item in pins.warnings)

    head = _head_commit(git)
    if head != pins.commit:
        findings.defect(WRONG_COMMIT, "HEAD", "HEAD does not match the pinned upstream commit", expected=pins.commit, actual=head)
    findings.checks["head"] = {"status": "pass" if head == pins.commit else "fail", "expected": pins.commit, "actual": head}

    _inspect_swan(git, pins, findings)
    _inspect_state(git, fmt, "", findings, frozenset())
    _inspect_binaries(checkout, pins, findings)

    findings.defects.sort(key=lambda item: (item["category"], item["subject"], item["detail"]))
    findings.warnings.sort(key=lambda item: (item["category"], item["subject"], item["detail"]))
    counts: dict[str, int] = {}
    for item in findings.defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1

    verified = not findings.defects
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "result": "verified" if verified else "failed",
        "exit_code": EXIT_VERIFIED if verified else EXIT_VERIFICATION_FAILED,
        "checkout": str(checkout.resolve()),
        "pinned": {"repository": pins.repository, "commit": pins.commit},
        "checks": dict(sorted(findings.checks.items())),
        "defect_counts": dict(sorted(counts.items())),
        "defects": findings.defects,
        "warnings": findings.warnings,
    }


def error_report(message: str) -> dict:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "result": "error",
        "exit_code": EXIT_OPERATIONAL_ERROR,
        "error": message,
    }


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def render_text(report: dict, limit: int = 20) -> str:
    if report["result"] == "error":
        return f"W2U checkout verification could not run: {report['error']}\n"
    lines = [
        f"W2U checkout verification: {report['result'].upper()}",
        f"Checkout: {report['checkout']}",
        f"Pinned:   {report['pinned']['repository']} @ {report['pinned']['commit']}",
        "",
        "Checks:",
    ]
    for name, check in report["checks"].items():
        lines.append(f"  [{check['status'].upper():4}] {name}: expected {check.get('expected')}, actual {check.get('actual')}")
    lines.append("")
    by_category: dict[str, list[dict]] = {}
    for item in report["defects"]:
        by_category.setdefault(item["category"], []).append(item)
    lines.append(f"Defects: {len(report['defects'])}")
    for category, items in by_category.items():
        lines.append(f"  {category} ({len(items)}):")
        for item in items[:limit]:
            lines.append(f"    {item['subject']!r}: {item['detail']}")
        if len(items) > limit:
            lines.append(f"    ... and {len(items) - limit} more (use --json for the full list)")
    lines.append(f"Warnings: {len(report['warnings'])}")
    for item in report["warnings"]:
        lines.append(f"  {item['category']}: {item['subject']}: {item['detail']}")
    return "\n".join(lines) + "\n"
