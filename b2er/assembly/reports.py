"""Manifest, plan, settings, spoiler, and verification outputs (no ROM bytes, no absolute paths)."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST_SCHEMA_VERSION = 1


def dump(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def assert_sanitized(value, forbidden: list[str]) -> None:
    """Fail if any string contains a forbidden local path or looks like an absolute POSIX/Windows path."""
    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(k, where)
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")
        elif isinstance(node, str):
            for item in forbidden:
                if item and item in node:
                    raise ValueError(f"{where} contains a local path")
            if node.startswith("/") or (len(node) > 2 and node[1] == ":" and node[2] in "\\/"):
                raise ValueError(f"{where} looks like an absolute path: {node!r}")
    walk(value, "$")


def spoiler(plan: dict, settings: dict, seed: str, build_key: str, script_report: dict) -> str:
    lines = ["Pokémon White 2 Expanded Randomizer — Alpha 0.1 spoiler log", "",
             f"Seed: {seed}", f"Build key: {build_key}", f"Mode: {settings['mode']}", ""]
    lines.append("Features:")
    for name, status in sorted({**plan.get("features", {}), **script_report.get("features", {})}.items()):
        lines.append(f"  {name}: {status}")
    starters = plan.get("starters", {})
    if starters.get("labels"):
        lines += ["", "Starters: " + ", ".join(starters["labels"])]
    if plan.get("route19"):
        r = plan["route19"]
        lines += ["", f"Route 19 diagnostic: encounter record {r['record']} (evidence: {r['evidence']['rule']})"]
    if plan.get("evolution_changes"):
        lines += ["", "Evolution changes:"]
        for change in plan["evolution_changes"]:
            lines.append(f"  {change['label']}: " + "; ".join(change["reasons"]))
    if plan.get("wild"):
        lines += ["", "Wild encounters (per encounter record):"]
        for record in plan["wild"]:
            pairs = ", ".join(f"{a.removeprefix('SPECIES_')}->{b.removeprefix('SPECIES_')}" for a, b in record["mapping"].items())
            lines.append(f"  record {record['record']}: {pairs}")
    if plan.get("doubles"):
        lines += ["", "Major battles:"]
        for entry in plan["doubles"]:
            state = "Doubles" if entry["converted"] else "kept Singles"
            lines.append(f"  trainer {entry['trainer']} {entry['name']}: {state} ({entry['reason']})")
    if plan.get("trainers"):
        lines += ["", "Trainer parties:"]
        for t in plan["trainers"]:
            party = ", ".join(f"{m['species'].removeprefix('SPECIES_')} L{m['level']}" for m in t["party"])
            lines.append(f"  {t['trainer']} {t['class']} {t['name']}: {party}")
    if script_report.get("patches"):
        lines += ["", "Script patches:"]
        for p in script_report["patches"]:
            lines.append(f"  {p['feature']}: member {p['member']} {p['command']} {p['before']} -> {p['after']}")
    lines += ["", "Checks:"]
    for check in plan.get("checks", []):
        lines.append(f"  [{check['status']}] {check['name']}: {check['detail']}")
    return "\n".join(lines) + "\n"


def write_outputs(directory: Path, name: str, rom: bytes, documents: dict[str, object]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    written = {}
    rom_path = directory / f"{name}.nds"
    for path in [rom_path] + [directory / f"{name}.{suffix}" for suffix in documents]:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing output {path.name}")
    rom_path.write_bytes(rom)
    written["rom"] = rom_path.name
    for suffix, content in documents.items():
        path = directory / f"{name}.{suffix}"
        path.write_text(content if isinstance(content, str) else dump(content), encoding="utf-8")
        written[suffix] = path.name
    return written
