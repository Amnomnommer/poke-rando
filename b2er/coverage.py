"""Coverage registry: engine families, required titles, and per-release support status (b2er/coverage.json)."""

from __future__ import annotations

import json
from pathlib import Path

from .capabilities import SupportStatus

COVERAGE = Path(__file__).resolve().parent / "coverage.json"


def load(path: Path = COVERAGE) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for release in data["pinned_releases"]:
        SupportStatus(release["status"])  # validates the vocabulary
    return data


def pinned_release(fingerprint_id: str, game_code: str | None, approved_id: str | None, coverage: dict | None = None) -> dict | None:
    coverage = coverage or load()
    for release in coverage["pinned_releases"]:
        if release["fingerprint"] == fingerprint_id and release["game_code"] == game_code and release["approved_input"] == approved_id:
            return release
    return None


def matrix(fingerprints: dict, registry, coverage: dict | None = None) -> dict:
    """Per family -> per title -> per release status, plus each adapter's capability levels."""
    coverage = coverage or load()
    out = {}
    for family_id, family in coverage["families"].items():
        titles = []
        for t in fingerprints["titles"]:
            if t.get("family") != family_id:
                continue
            releases = [{"release": r["id"], "game_code": r["game_code"], "status": r["status"], "evidence": r["evidence"],
                         "missing_for_next_status": r.get("missing_for_next_status")}
                        for r in coverage["pinned_releases"] if r["fingerprint"] == t["id"]]
            adapter = registry.for_fingerprint(t["id"])
            best = max((SupportStatus(r["status"]) for r in releases), key=lambda s: s.rank, default=SupportStatus.RECOGNIZED)
            titles.append({
                "fingerprint": t["id"], "title": t["title"], "generation": t["generation"], "required": t.get("required", False),
                "title_status": best.value,
                "other_releases": "recognized (any other region, language, or revision: header recognized; no approved clean hash)",
                "pinned_releases": releases,
                "adapter": adapter.id if adapter else None,
                "capabilities": {c.value: s.to_json() for c, s in adapter.capabilities().items()} if adapter else {},
            })
        out[family_id] = {"name": family["name"], "shared": family["shared"], "next": family["next"], "titles": titles}
    return out


def audit(registry, coverage: dict | None = None) -> list[str]:
    """Reject any status claim the adapter's declared capabilities do not back."""
    from .capabilities import FEATURE_COMPLETE_REQUIRES, Capability, Level
    coverage = coverage or load()
    problems = []
    for release in coverage["pinned_releases"]:
        status = SupportStatus(release["status"])
        try:
            adapter = registry.get(release["adapter"])
        except KeyError:
            problems.append(f"{release['id']}: adapter {release['adapter']} is not registered")
            continue
        levels = {c.value: s.level for c, s in adapter.capabilities().items()}
        if release["fingerprint"] not in adapter.fingerprints:
            problems.append(f"{release['id']}: adapter does not claim fingerprint {release['fingerprint']}")
        if status.at_least(SupportStatus.BUILDABLE):
            for needed in (Capability.REPACK.value, Capability.DETERMINISTIC_VERIFICATION.value):
                if levels.get(needed) != Level.SUPPORTED:
                    problems.append(f"{release['id']}: '{status.value}' requires {needed} implemented")
        if status.at_least(SupportStatus.FEATURE_COMPLETE):
            missing = [c for c in FEATURE_COMPLETE_REQUIRES if levels.get(c) != Level.SUPPORTED]
            if missing:
                problems.append(f"{release['id']}: 'feature-complete' requires {missing} implemented")
    return problems
