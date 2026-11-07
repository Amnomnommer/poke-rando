"""Adapter registry. Adding a game: add a fingerprint to b2er/fingerprints.json and an adapter here."""

from __future__ import annotations

from .base import AdapterRegistry, BuildContext, GameAdapter
from .gen5.black2 import Black2Adapter
from .gen5.white2 import White2Adapter


def default_registry() -> AdapterRegistry:
    return AdapterRegistry([White2Adapter(), Black2Adapter()])


__all__ = ["AdapterRegistry", "BuildContext", "GameAdapter", "default_registry"]
