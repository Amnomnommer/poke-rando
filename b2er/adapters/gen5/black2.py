"""Pokémon Black Version 2 (IREO): recognized and validated; randomized builds are not yet available."""

from __future__ import annotations

from ...capabilities import Capability, Level, Support, unsupported
from .common import Gen5Adapter


class Black2Adapter(Gen5Adapter):
    id = "gen5.black2"
    version = "0.1.0"
    display_name = "Pokémon Black Version 2 (USA/Europe, IREO)"
    fingerprints = ("gen5-black2",)

    def capabilities(self):
        reason = "the pinned W2U revision provides no Black 2 ROM build target (only a runtime/data package)"
        caps = {c: unsupported(reason) for c in Capability}
        caps[Capability.RECOGNITION] = Support(Level.SUPPORTED, "approved clean SHA-256 in pins/inputs.json and IREO header")
        return caps

    def _unavailable(self, *args, **kwargs):
        raise NotImplementedError("Black 2 builds are not supported in Alpha 0.1")

    prepare = read_model = apply = rebuild = postprocess = verify = _unavailable
