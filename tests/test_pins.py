"""Structural tests for the machine-readable pin records in pins/."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

PINS = Path(__file__).resolve().parent.parent / "pins"

WHITE2_SHA256 = "3e50aec3db401332175a5d2b5fe2a68ac1a05ec63995dba9d1506b1b51837446"
BLACK2_SHA256 = "2e6b2415354aa41471bc7617068dce059a59931bf5c4348a264f8043f297683a"
W2U_REPOSITORY = "https://github.com/hzla/W2U-Project"
W2U_COMMIT = "9489aebf42a870fa6a60816c273b0aa22fe5c1fe"

SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")


def load(name: str) -> dict:
    with (PINS / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def status_fields(node):
    """Yield every {status, value, reason} object anywhere in a pin document."""
    if isinstance(node, dict):
        if "status" in node:
            yield node
        for value in node.values():
            yield from status_fields(value)
    elif isinstance(node, list):
        for value in node:
            yield from status_fields(value)


def keyed_values(node, key):
    if isinstance(node, dict):
        for name, value in node.items():
            if name == key:
                yield value
            yield from keyed_values(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from keyed_values(value, key)


class PinTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs = load("inputs.json")
        self.upstream = load("upstream.json")

    def test_schema_versions(self) -> None:
        self.assertEqual(self.inputs["schema_version"], 1)
        self.assertEqual(self.upstream["schema_version"], 1)

    def test_exactly_the_two_approved_input_hashes(self) -> None:
        by_code = {entry["game_code"]: entry for entry in self.inputs["inputs"]}
        self.assertEqual(set(by_code), {"IRDO", "IREO"})
        self.assertEqual(by_code["IRDO"]["sha256"], WHITE2_SHA256)
        self.assertEqual(by_code["IREO"]["sha256"], BLACK2_SHA256)
        self.assertEqual(by_code["IRDO"]["role"], "target")

    def test_exact_upstream_commit(self) -> None:
        self.assertEqual(self.upstream["upstream"]["repository"], W2U_REPOSITORY)
        self.assertEqual(self.upstream["upstream"]["commit"], W2U_COMMIT)

    def test_hash_and_commit_formats(self) -> None:
        for document in (self.inputs, self.upstream):
            for value in keyed_values(document, "sha256"):
                self.assertRegex(value, SHA256)
            for value in keyed_values(document, "commit"):
                if isinstance(value, str):
                    self.assertRegex(value, COMMIT)

    def test_unresolved_fields_are_explicit(self) -> None:
        fields = list(status_fields(self.inputs)) + list(status_fields(self.upstream))
        self.assertTrue(fields, "expected at least one explicit status field")
        for field in fields:
            self.assertEqual(set(field), {"status", "value", "reason"}, field)
            self.assertIn(field["status"], {"resolved", "unresolved"})
            self.assertTrue(field["reason"])
            if field["status"] == "unresolved":
                self.assertIsNone(field["value"], field)
            else:
                self.assertIsNotNone(field["value"], field)

    def test_external_binaries_and_dependencies_are_identified(self) -> None:
        binaries = {entry["id"]: entry for entry in self.upstream["external_binaries"]}
        self.assertTrue({"ctrmap-jar", "pmc-arm9", "pmc-overlay", "pmc-symbols"} <= set(binaries))
        dependencies = {entry["id"] for entry in self.upstream["source_dependencies"]}
        self.assertTrue({"swan", "extlib", "nitrokernel", "meson"} <= dependencies)

    def test_pins_contain_no_embedded_bytes(self) -> None:
        for name in ("inputs.json", "upstream.json"):
            self.assertLess((PINS / name).stat().st_size, 16 * 1024, name)
            text = (PINS / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"[A-Za-z0-9+/]{120,}={0,2}", f"{name} looks like it embeds encoded data")


if __name__ == "__main__":
    unittest.main()
