"""Synthetic tests for scripts/verify_w2u_checkout.py.

Every checkout is a throwaway Git repository built from synthetic bytes, with a
synthetic pin file pointing at its own commits. No upstream or proprietary
content is used.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "verify_w2u_checkout.py"
sys.path.insert(0, str(REPO_ROOT))

from b2er import w2u_checkout  # noqa: E402

SWAN_PATH = "include/swan"
FAKE_SWAN_COMMIT = "07187e940a097309500769c83aac313032f970ac"
BINARIES = {
    "tool-jar": ("tools/Tool/Tool.jar", b"synthetic jar bytes\x00\x01"),
    "core-bin": ("core/CORE.bin", bytes(range(64))),
}


def clean_env(index: int = 0) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    stamp = f"2026-01-01T00:00:{index:02d}+0000"
    env.update(
        {
            "GIT_AUTHOR_NAME": "Synthetic",
            "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic",
            "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    return env


def git(cwd: Path, *args: str, stdin: bytes | None = None, index: int = 0) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], input=stdin, capture_output=True, check=True, env=clean_env(index)
    )
    return result.stdout.decode().strip()


def write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_swan_repo(path: Path, commits: int = 1) -> list[str]:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    heads = []
    for number in range(commits):
        write(path, "swan.h", f"// synthetic header {number}\n".encode())
        git(path, "add", "swan.h")
        git(path, "commit", "-q", "-m", f"swan {number}", index=number)
        heads.append(git(path, "rev-parse", "HEAD"))
    return heads


def make_upstream(root: Path, *, swan_commit: str | None = FAKE_SWAN_COMMIT, initialized_swan: bool = False) -> dict:
    """Build a synthetic upstream checkout and return the facts a pin file needs."""
    root.mkdir(parents=True)
    git(root, "init", "-q")
    write(root, "README.md", b"synthetic upstream\n")
    write(root, ".gitignore", b"build/\n")
    write(root, "scripts/run.sh", b"#!/bin/sh\necho synthetic\n").chmod(0o755)
    for path, data in BINARIES.values():
        write(root, path, data)
    git(root, "add", "README.md", ".gitignore", "scripts/run.sh", *[path for path, _ in BINARIES.values()])
    swan_heads: list[str] = []
    if initialized_swan:
        swan_heads = make_swan_repo(root / SWAN_PATH, commits=2)
        git(root / SWAN_PATH, "checkout", "-q", swan_heads[0])
        swan_commit = swan_heads[0]
    if swan_commit is not None:
        (root / SWAN_PATH).mkdir(parents=True, exist_ok=True)  # as a real clone leaves it
        git(root, "update-index", "--add", "--cacheinfo", f"160000,{swan_commit},{SWAN_PATH}")
    git(root, "commit", "-q", "-m", "synthetic upstream")
    return {"head": git(root, "rev-parse", "HEAD"), "swan_commit": swan_commit or FAKE_SWAN_COMMIT, "swan_heads": swan_heads}


def make_pins(facts: dict) -> dict:
    return {
        "schema_version": 1,
        "upstream": {
            "repository": "https://example.invalid/synthetic-upstream",
            "commit": facts["head"],
            "license": {"status": "unresolved", "value": None, "reason": "synthetic unresolved license"},
        },
        "source_dependencies": [
            {"id": "swan", "path": SWAN_PATH, "storage": "submodule", "commit": facts["swan_commit"]},
            {
                "id": "vendored-lib",
                "path": "include/lib",
                "storage": "vendored",
                "commit": "4fcc85b78f4eafcf9c77347a51930359c3a6da39",
                "commit_evidence": "declared, not compared",
            },
        ],
        "python_requirements": {"status": "unresolved", "value": None, "reason": "synthetic unresolved requirements"},
        "external_binaries": [
            {"id": bin_id, "path": path, "sha256": hashlib.sha256(data).hexdigest()}
            for bin_id, (path, data) in sorted(BINARIES.items())
        ],
    }


def snapshot(root: Path) -> dict:
    """Every path under the checkout (including .git) with type, mode, size, mtime, and content hash."""
    state = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            info = path.lstat()
            entry = (info.st_mode, info.st_size, info.st_mtime_ns)
            if path.is_file() and not path.is_symlink():
                entry += (hashlib.sha256(path.read_bytes()).hexdigest(),)
            state[str(path.relative_to(root))] = entry
    return state


class CheckoutCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.checkout = self.tmp / "upstream"
        self.pins_path = self.tmp / "pins.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build(self, **kwargs) -> dict:
        self.facts = make_upstream(self.checkout, **kwargs)
        self.pins = make_pins(self.facts)
        self.save_pins()
        return self.facts

    def save_pins(self, raw: bytes | None = None) -> None:
        self.pins_path.write_bytes(raw if raw is not None else json.dumps(self.pins, indent=2).encode())

    def run_cli(self, *extra: str, checkout: Path | None = None, json_output: bool = True):
        args = [sys.executable, "-I", str(CLI), str(checkout or self.checkout), "--pins", str(self.pins_path), *extra]
        if json_output:
            args.append("--json")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        result = subprocess.run(args, capture_output=True, text=True, env=env, check=False)
        report = json.loads(result.stdout) if json_output and result.stdout else None
        return result, report

    def verify(self, expected_exit: int) -> dict:
        result, report = self.run_cli()
        self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
        self.assertEqual(report["exit_code"], expected_exit)
        return report

    def defects(self, report: dict, category: str) -> list[dict]:
        return [item for item in report["defects"] if item["category"] == category]

    def assertOnlyDefects(self, report: dict, *categories: str) -> None:
        self.assertEqual(sorted({item["category"] for item in report["defects"]}), sorted(set(categories)), report["defects"])


class VerificationTests(CheckoutCase):
    def test_exact_clean_checkout_verifies(self) -> None:
        self.build()
        report = self.verify(0)
        self.assertEqual(report["result"], "verified")
        self.assertEqual(report["defects"], [])
        self.assertEqual(report["checks"]["head"]["status"], "pass")
        self.assertEqual(report["checks"]["swan_gitlink"]["status"], "pass")
        for bin_id in BINARIES:
            self.assertEqual(report["checks"][f"binary:{bin_id}"]["status"], "pass")

    def test_wrong_head_fails(self) -> None:
        self.build()
        write(self.checkout, "README.md", b"changed upstream\n")
        git(self.checkout, "commit", "-q", "-am", "newer commit", index=5)
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.WRONG_COMMIT)
        [defect] = self.defects(report, w2u_checkout.WRONG_COMMIT)
        self.assertEqual(defect["subject"], "HEAD")
        self.assertEqual(defect["expected"], self.facts["head"])
        self.assertNotEqual(defect["actual"], self.facts["head"])

    def test_tracked_modifications_fail(self) -> None:
        cases = {
            "content": (lambda root: write(root, "README.md", b"edited\n"), "README.md", "content modified", "worktree"),
            "deleted": (lambda root: (root / "README.md").unlink(), "README.md", "deleted from the worktree", "worktree"),
            "mode": (lambda root: (root / "scripts/run.sh").chmod(0o644), "scripts/run.sh", "file mode changed", "worktree"),
            "staged": (
                lambda root: (write(root, "README.md", b"staged\n"), git(root, "add", "README.md")),
                "README.md", "index differs from HEAD", "staged",
            ),
        }
        for label, (mutate, subject, detail, kind) in cases.items():
            with self.subTest(case=label):
                self.tearDown(); self.setUp()
                self.build()
                mutate(self.checkout)
                report = self.verify(1)
                self.assertOnlyDefects(report, w2u_checkout.TRACKED_MODIFICATION)
                matching = [d for d in report["defects"] if d["subject"] == subject and d["detail"] == detail]
                self.assertTrue(matching, report["defects"])
                self.assertEqual(matching[0]["kind"], kind)

    def test_untracked_file_fails(self) -> None:
        self.build()
        write(self.checkout, "notes.txt", b"untracked\n")
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.UNTRACKED_FILE)
        self.assertEqual(self.defects(report, w2u_checkout.UNTRACKED_FILE)[0]["subject"], "notes.txt")

    def test_ignored_file_fails_distinctly(self) -> None:
        self.build()
        write(self.checkout, "build/White2Upgrade.out", b"stale build output\n")
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.IGNORED_FILE)
        self.assertEqual(self.defects(report, w2u_checkout.IGNORED_FILE)[0]["subject"], "build/White2Upgrade.out")

    def test_absent_swan_gitlink_fails(self) -> None:
        facts = self.build(swan_commit=None)
        self.assertEqual(facts["swan_commit"], FAKE_SWAN_COMMIT)
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.MISSING_DEPENDENCY)
        self.assertEqual(self.defects(report, w2u_checkout.MISSING_DEPENDENCY)[0]["subject"], SWAN_PATH)
        self.assertEqual(report["checks"]["swan_gitlink"]["status"], "fail")

    def test_incorrect_swan_gitlink_fails(self) -> None:
        self.build()
        self.pins["source_dependencies"][0]["commit"] = "1" * 40
        self.save_pins()
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.WRONG_COMMIT)
        [defect] = self.defects(report, w2u_checkout.WRONG_COMMIT)
        self.assertEqual((defect["subject"], defect["actual"]), (SWAN_PATH, FAKE_SWAN_COMMIT))

    def test_uninitialized_swan_is_a_warning(self) -> None:
        self.build()
        report = self.verify(0)
        self.assertEqual(report["checks"]["swan_checkout"]["status"], "warn")
        self.assertIn(w2u_checkout.DEPENDENCY_NOT_INITIALIZED, {w["category"] for w in report["warnings"]})

    def test_initialized_swan_checkout_correct(self) -> None:
        facts = self.build(initialized_swan=True)
        report = self.verify(0)
        self.assertEqual(report["checks"]["swan_checkout"], {"status": "pass", "expected": facts["swan_heads"][0], "actual": facts["swan_heads"][0]})

    def test_initialized_swan_checkout_incorrect(self) -> None:
        facts = self.build(initialized_swan=True)
        git(self.checkout / SWAN_PATH, "checkout", "-q", facts["swan_heads"][1])
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.WRONG_COMMIT)
        [defect] = self.defects(report, w2u_checkout.WRONG_COMMIT)
        self.assertEqual((defect["subject"], defect["actual"]), (SWAN_PATH, facts["swan_heads"][1]))
        self.assertEqual(report["checks"]["swan_checkout"]["status"], "fail")

    def test_initialized_swan_dirty_fails(self) -> None:
        self.build(initialized_swan=True)
        write(self.checkout / SWAN_PATH, "swan.h", b"// edited\n")
        write(self.checkout / SWAN_PATH, "extra.h", b"// untracked\n")
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.TRACKED_MODIFICATION, w2u_checkout.UNTRACKED_FILE)
        self.assertEqual({d["subject"] for d in report["defects"]}, {f"{SWAN_PATH}/swan.h", f"{SWAN_PATH}/extra.h"})

    def test_missing_external_binary_fails(self) -> None:
        self.build()
        path, _ = BINARIES["core-bin"]
        (self.checkout / path).unlink()
        report = self.verify(1)
        self.assertEqual(
            {(d["category"], d["subject"]) for d in report["defects"]},
            {(w2u_checkout.MISSING_DEPENDENCY, path), (w2u_checkout.TRACKED_MODIFICATION, path)},
        )
        self.assertEqual(report["checks"]["binary:core-bin"]["status"], "fail")

    def test_wrong_binary_hash_fails(self) -> None:
        self.build()
        path, data = BINARIES["tool-jar"]
        self.pins["external_binaries"][1]["sha256"] = hashlib.sha256(data + b"x").hexdigest()
        self.save_pins()
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.WRONG_HASH)
        [defect] = self.defects(report, w2u_checkout.WRONG_HASH)
        self.assertEqual((defect["subject"], defect["actual"]), (path, hashlib.sha256(data).hexdigest()))

    def test_symlinked_binary_is_not_accepted(self) -> None:
        self.build()
        path, data = BINARIES["core-bin"]
        outside = write(self.tmp, "outside.bin", data)
        (self.checkout / path).unlink()
        os.symlink(outside, self.checkout / path)
        report = self.verify(1)
        self.assertIn((w2u_checkout.MISSING_DEPENDENCY, path), {(d["category"], d["subject"]) for d in report["defects"]})

    def test_unresolved_pins_are_warnings_not_facts(self) -> None:
        self.build()
        self.pins["external_binaries"].append(
            {"id": "unknown-bin", "path": "tools/unknown.bin",
             "sha256": {"status": "unresolved", "value": None, "reason": "synthetic unaudited binary"}}
        )
        self.save_pins()
        report = self.verify(0)
        unresolved = {w["subject"]: w["detail"] for w in report["warnings"] if w["category"] == w2u_checkout.UNRESOLVED_PIN}
        self.assertEqual(
            unresolved,
            {
                "/upstream/license": "synthetic unresolved license",
                "/python_requirements": "synthetic unresolved requirements",
                "/external_binaries/2/sha256": "synthetic unaudited binary",
            },
        )
        self.assertEqual(report["checks"]["binary:unknown-bin"]["status"], "warn")
        self.assertIn(w2u_checkout.UNVERIFIED_DEPENDENCY, {w["category"] for w in report["warnings"]})
        self.assertEqual(report["defects"], [])

    def test_paths_containing_spaces(self) -> None:
        self.checkout = self.tmp / "W2U checkout copy"
        self.build()
        write(self.checkout, "new file.txt", b"untracked\n")
        write(self.checkout, "tools/Tool/Tool.jar", b"different bytes")
        report = self.verify(1)
        self.assertEqual(report["checkout"], str(self.checkout.resolve()))
        subjects = {(d["category"], d["subject"]) for d in report["defects"]}
        self.assertIn((w2u_checkout.UNTRACKED_FILE, "new file.txt"), subjects)
        self.assertIn((w2u_checkout.WRONG_HASH, "tools/Tool/Tool.jar"), subjects)

    def test_json_output_is_stable_and_sorted(self) -> None:
        self.build()
        write(self.checkout, "b.txt", b"untracked\n")
        write(self.checkout, "a.txt", b"untracked\n")
        write(self.checkout, "README.md", b"edited\n")
        first, report = self.run_cli()
        second, _ = self.run_cli()
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stdout, json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
        self.assertEqual([d["subject"] for d in self.defects(report, w2u_checkout.UNTRACKED_FILE)], ["a.txt", "b.txt"])
        self.assertEqual(set(report), {"schema_version", "tool", "result", "exit_code", "checkout", "pinned",
                                       "checks", "defect_counts", "defects", "warnings"})
        self.assertEqual(report["defect_counts"], {"tracked_modification": 1, "untracked_file": 2})

    def test_text_output_is_deterministic(self) -> None:
        self.build()
        write(self.checkout, "notes.txt", b"untracked\n")
        first, _ = self.run_cli(json_output=False)
        second, _ = self.run_cli(json_output=False)
        self.assertEqual(first.returncode, 1)
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn("W2U checkout verification: FAILED", first.stdout)
        self.assertIn("'notes.txt': untracked file", first.stdout)

    def test_report_never_contains_file_contents(self) -> None:
        self.build()
        secret = b"SYNTHETIC-PROPRIETARY-MARKER"
        write(self.checkout, "README.md", secret)
        write(self.checkout, "notes.txt", secret)
        for json_output in (True, False):
            result, _ = self.run_cli(json_output=json_output)
            self.assertNotIn(secret.decode(), result.stdout + result.stderr)

    def test_checkout_configured_programs_are_never_executed(self) -> None:
        self.build()
        marker = self.tmp / "executed-marker"
        program = write(self.tmp, "hostile.sh", f"#!/bin/sh\ntouch '{marker}'\ncat\n".encode())
        program.chmod(0o755)
        for key, value in (
            ("core.fsmonitor", str(program)),
            ("filter.hostile.clean", str(program)),
            ("filter.hostile.smudge", str(program)),
            ("filter.hostile.process", str(program)),
            ("diff.external", str(program)),
            ("core.pager", str(program)),
        ):
            git(self.checkout, "config", key, value)
        write(self.checkout, ".git/info/attributes", b"* filter=hostile diff=hostile\n")
        for hook in ("post-checkout", "post-index-change", "reference-transaction"):
            write(self.checkout, f".git/hooks/{hook}", program.read_bytes()).chmod(0o755)
        write(self.checkout, "README.md", b"edited so content must be compared\n")
        report = self.verify(1)
        self.assertOnlyDefects(report, w2u_checkout.TRACKED_MODIFICATION)
        self.run_cli(json_output=False)
        self.assertFalse(marker.exists(), "a program configured by the checkout was executed")

    def test_verification_leaves_checkout_unchanged(self) -> None:
        states = {
            "clean": lambda root: None,
            "dirty": lambda root: (write(root, "README.md", b"edited\n"), write(root, "notes.txt", b"x\n"),
                                   write(root, "build/out", b"x\n")),
            "staged": lambda root: (write(root, "README.md", b"staged\n"), git(root, "add", "README.md")),
        }
        for label, mutate in states.items():
            with self.subTest(state=label):
                self.tearDown(); self.setUp()
                self.build(initialized_swan=True)
                mutate(self.checkout)
                before = snapshot(self.checkout)
                head_before = git(self.checkout, "rev-parse", "HEAD")
                self.run_cli()
                self.run_cli(json_output=False)
                self.assertEqual(snapshot(self.checkout), before)
                self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), head_before)


class OperationalErrorTests(CheckoutCase):
    def assertOperationalError(self, fragment: str, checkout: Path | None = None) -> dict:
        result, report = self.run_cli(checkout=checkout)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(report["result"], "error")
        self.assertIn(fragment, report["error"])
        return report

    def test_malformed_pin_files(self) -> None:
        self.build()
        good = copy.deepcopy(self.pins)

        def variant(mutator):
            pins = copy.deepcopy(good)
            mutator(pins)
            return json.dumps(pins).encode()

        cases = {
            "not json": (b"{ nope", "not valid UTF-8 JSON"),
            "top-level list": (b"[]", "top level must be an object"),
            "duplicate key": (b'{"schema_version": 1, "schema_version": 1}', "duplicate JSON keys"),
            "schema": (variant(lambda p: p.update(schema_version=2)), "schema_version must be 1"),
            "short commit": (variant(lambda p: p["upstream"].update(commit="abc")), "upstream.commit"),
            "no swan": (variant(lambda p: p["source_dependencies"].pop(0)), "must contain the 'swan'"),
            "bad hash": (variant(lambda p: p["external_binaries"][0].update(sha256="ABC")), "sha256 must be"),
            "escaping path": (variant(lambda p: p["external_binaries"][0].update(path="../x.bin")), "normalized relative"),
            "duplicate binary": (
                variant(lambda p: p["external_binaries"].append(dict(p["external_binaries"][0]))), "duplicate external binary"
            ),
            "bad status": (
                variant(lambda p: p["upstream"].update(license={"status": "unresolved", "value": "x", "reason": "r"})),
                "null value",
            ),
        }
        for label, (raw, fragment) in cases.items():
            with self.subTest(case=label):
                self.save_pins(raw)
                self.assertOperationalError(fragment)

    def test_missing_pin_file(self) -> None:
        self.build()
        self.pins_path.unlink()
        self.assertOperationalError("cannot read pin file")

    def test_unreadable_checkout_paths(self) -> None:
        self.build()
        plain = self.tmp / "plain dir"
        plain.mkdir()
        self.assertOperationalError("not a Git worktree", checkout=plain)
        self.assertOperationalError("does not exist", checkout=self.tmp / "missing")
        # Discovery never climbs above the given path, so a subdirectory is not a worktree.
        self.assertOperationalError("not a Git worktree", checkout=self.checkout / "scripts")

    def test_invalid_arguments(self) -> None:
        result = subprocess.run([sys.executable, "-I", str(CLI)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)

    def test_repository_pins_parse(self) -> None:
        pins = w2u_checkout.load_pins(REPO_ROOT / "pins" / "upstream.json")
        self.assertEqual(pins.commit, "9489aebf42a870fa6a60816c273b0aa22fe5c1fe")
        self.assertEqual((pins.swan_path, pins.swan_commit), (SWAN_PATH, FAKE_SWAN_COMMIT))
        self.assertEqual({b.id for b in pins.binaries}, {"ctrmap-jar", "pmc-arm9", "pmc-overlay", "pmc-symbols"})
        unresolved = {w["subject"] for w in pins.warnings if w["category"] == w2u_checkout.UNRESOLVED_PIN}
        self.assertIn("/python_requirements", unresolved)
        self.assertNotIn("/source_dependencies/3/commit", unresolved)  # Meson commit resolved in R-003


if __name__ == "__main__":
    unittest.main()
