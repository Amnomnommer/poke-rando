"""Adapter registry built from the declarative fingerprints and coverage registry.

Adding a game: a fingerprint in b2er/fingerprints.json (with its family). It is
recognized immediately through its family's recognition-only adapter; a full
adapter replaces that entry here once its build path exists.
"""

from __future__ import annotations

from .. import coverage, identify
from ..families.base import RecognizedFamilyAdapter
from .base import AdapterRegistry, BuildContext, GameAdapter
from .gen5.black2 import Black2Adapter
from .gen5.white2 import White2Adapter

FULL_ADAPTERS = (White2Adapter, Black2Adapter)


def default_registry() -> AdapterRegistry:
    adapters: list[GameAdapter] = [cls() for cls in FULL_ADAPTERS]
    claimed = {fp for a in adapters for fp in a.fingerprints}
    families = coverage.load()["families"]
    for title in identify.load_fingerprints()["titles"]:
        if title["id"] in claimed or title.get("family") not in families:
            continue
        adapters.append(RecognizedFamilyAdapter(title, title["family"], families[title["family"]]))
    return AdapterRegistry(adapters)


__all__ = ["AdapterRegistry", "BuildContext", "GameAdapter", "default_registry"]
