"""The project's internal species-ID model.

The model is derived from the W2U species enum of the staged checkout (or a
synthetic one in tests) and checked against the documented allocation in
docs/species-id-model.md. Every consumer (randomizer pools, verifiers, plans)
asks this model whether an ID is an ordinary species, an egg sentinel, a form
record, or invalid. Nothing else hard-codes 1024/1025.
"""

from __future__ import annotations

from dataclasses import dataclass

NATIONAL_DEX_REQUIRED = range(1, 1026)  # #001-#1025, the core requirement
ENCOUNTER_SPECIES_BITS = 11
PKM_SPECIES_BITS = 16


class SpeciesModelError(ValueError):
    pass


@dataclass(frozen=True)
class SpeciesModel:
    max_ordinary: int
    egg: int
    bad_egg: int
    form_first: int | None
    form_last: int | None
    names: dict[int, str]

    @classmethod
    def from_enum(cls, defines: dict[str, int]) -> "SpeciesModel":
        if defines.get("SPECIES_NONE", 0) != 0:
            raise SpeciesModelError("SPECIES_NONE must be 0")
        egg = defines.get("SPECIES_EGG")
        bad = defines.get("SPECIES_BAD_EGG")
        if egg is None or bad is None:
            raise SpeciesModelError("species enum must define SPECIES_EGG and SPECIES_BAD_EGG")
        if bad != egg + 1:
            raise SpeciesModelError("SPECIES_BAD_EGG must directly follow SPECIES_EGG")
        ids = sorted(set(defines.values()))
        ordinary = [i for i in ids if 0 < i < egg]
        if ordinary != list(range(1, egg)):
            raise SpeciesModelError("ordinary species IDs below SPECIES_EGG must be contiguous from 1")
        forms = [i for i in ids if i > bad]
        if forms and forms != list(range(forms[0], forms[-1] + 1)):
            raise SpeciesModelError("form record IDs after the egg block must be contiguous")
        if any(i >= 1 << PKM_SPECIES_BITS for i in ids):
            raise SpeciesModelError("species IDs must fit the 16-bit Pokémon data field")
        names = {}
        for name, value in defines.items():
            names.setdefault(value, name)
        return cls(egg - 1, egg, bad, forms[0] if forms else None, forms[-1] if forms else None, names)

    # --- classification ------------------------------------------------------
    def is_ordinary(self, species: int) -> bool:
        return 1 <= species <= self.max_ordinary

    def is_egg_sentinel(self, species: int) -> bool:
        return species in (self.egg, self.bad_egg)

    def is_form_record(self, species: int) -> bool:
        return self.form_first is not None and self.form_first <= species <= self.form_last

    def encounter_encodable(self, species: int) -> bool:
        return self.is_ordinary(species) and species < 1 << ENCOUNTER_SPECIES_BITS

    @property
    def missing_required(self) -> list[int]:
        """National Dex numbers required by the core requirement that this layout cannot represent."""
        return [n for n in NATIONAL_DEX_REQUIRED if not self.is_ordinary(n)]

    def describe(self) -> dict:
        return {
            "ordinary_species": f"1..{self.max_ordinary}",
            "egg": self.egg,
            "bad_egg": self.bad_egg,
            "form_records": None if self.form_first is None else f"{self.form_first}..{self.form_last}",
            "missing_required_national_dex": self.missing_required,
            "requirement_met": not self.missing_required,
        }
