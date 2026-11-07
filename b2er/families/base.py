"""Recognition-only family adapter: one class, parameterized per game, until a family's build path exists."""

from __future__ import annotations

import importlib

from ..adapters.base import GameAdapter
from ..capabilities import Capability, Level, Support, unsupported

VALIDATORS = {"gb": "b2er.families.gb", "gba": "b2er.families.gba", "nds-gen4": "b2er.families.nds_gen4",
              "nds-gen5": "b2er.families.nds_gen4", "ctr-gen6": "b2er.families.ctr", "ctr-gen7": "b2er.families.ctr"}


class RecognizedFamilyAdapter(GameAdapter):
    version = "0.1.0"

    def __init__(self, fingerprint: dict, family_id: str, family: dict) -> None:
        self.fingerprint_entry = fingerprint
        self.family_id = family_id
        self.family = family
        self.id = f"{family_id}.{fingerprint['id']}"
        self.display_name = f"{fingerprint['title']} ({family['name']})"
        self.generation = fingerprint["generation"]
        self.fingerprints = (fingerprint["id"],)

    def capabilities(self):
        reason = f"{self.family['name']}: not yet implemented — next: {self.family['next']}"
        caps = {c: unsupported(reason) for c in Capability}
        caps[Capability.RECOGNITION] = Support(Level.SUPPORTED, f"header fingerprint; shared: {self.family['shared']}")
        return caps

    def validate(self, data: bytes) -> list[str]:
        module = VALIDATORS.get(self.family_id)
        return importlib.import_module(module).validate(data) if module else []

    def _unavailable(self, *args, **kwargs):
        raise NotImplementedError(f"{self.display_name} is recognized only")

    prepare = read_model = apply = rebuild = postprocess = verify = _unavailable
