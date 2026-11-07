"""Source-level generation: evolutions, wild encounters, starters, trainers, and major-battle Doubles.

Everything here operates on :class:`b2er.w2u_data.W2UData` (W2U's TOML sources in
a disposable workroot) and returns a JSON-serializable plan. Script-level
features (starter gift commands, gifts, statics, items) are planned against
the finished ROM in :mod:`b2er.randomizer.script_features`.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field

from ..gamedata import encounters as enc
from ..w2u_data import Evolution, W2UData
from .rng import Stream

VANILLA_STARTERS = ("SPECIES_SNIVY", "SPECIES_TEPIG", "SPECIES_OSHAWOTT")
UNDISCOVERED_EGG_GROUP = 15
# W2U's TM/HM compatibility layout differs from vanilla Generation V and its HM bit
# positions are not established by evidence, so the progression constraint is
# layout-independent: every machine-compatibility bit that vanilla wild species
# provide must still be provided by at least MIN_MACHINE_LEARNERS obtainable species.
# This preserves every field HM (Cut, Surf, Strength, Waterfall, Dive) whatever its index.
MACHINE_BITS = 128
MIN_MACHINE_LEARNERS = 2
MAX_ATTEMPTS = 8

LEVEL_METHODS = {
    "METHOD_LEVELUP", "METHOD_LEVELUP_ATK_OVER_DEF", "METHOD_LEVELUP_ATK_EQU_DEF", "METHOD_LEVELUP_DEF_OVER_ATK",
    "METHOD_LEVELUP_RANDOM_1", "METHOD_LEVELUP_RANDOM_2", "METHOD_LEVELUP_NINJASK", "METHOD_LEVELUP_SHEDINJA",
    "METHOD_LEVELUP_FEMALE", "METHOD_LEVELUP_DAY", "METHOD_LEVELUP_NIGHT", "METHOD_LEVELUP_PARTY_TYPE",
}
ALWAYS_FEASIBLE = LEVEL_METHODS | {
    "METHOD_FRIENDSHIP", "METHOD_FRIENDSHIP_DAY", "METHOD_FRIENDSHIP_NIGHT",
    # White 2 contains the magnetic (Chargestone Cave), moss (Pinwheel Forest), and ice (Twist Mountain) spots.
    "METHOD_LOCATION_ELEC", "METHOD_LOCATION_MOSS", "METHOD_LOCATION_ICE", "METHOD_LOCATION_ELEC2",
}
ITEM_METHODS = {"METHOD_ITEM_USE", "METHOD_ITEM_USE_MALE", "METHOD_ITEM_USE_FEMALE", "METHOD_ITEM_HELD_DAY", "METHOD_ITEM_HELD_NIGHT"}
# Evolution items obtainable in an unmodified White 2 playthrough (shops, field, gifts). Item shuffling
# preserves the item multiset and never moves key items, so availability is preserved.
VANILLA_EVOLUTION_ITEMS = {
    "ITEM_FIRE_STONE", "ITEM_WATER_STONE", "ITEM_THUNDERSTONE", "ITEM_LEAF_STONE", "ITEM_MOON_STONE",
    "ITEM_SUN_STONE", "ITEM_SHINY_STONE", "ITEM_DUSK_STONE", "ITEM_DAWN_STONE", "ITEM_OVAL_STONE",
    "ITEM_KINGS_ROCK", "ITEM_METAL_COAT", "ITEM_DRAGON_SCALE", "ITEM_UPGRADE", "ITEM_PROTECTOR",
    "ITEM_ELECTIRIZER", "ITEM_MAGMARIZER", "ITEM_DUBIOUS_DISC", "ITEM_REAPER_CLOTH", "ITEM_RAZOR_CLAW",
    "ITEM_RAZOR_FANG", "ITEM_PRISM_SCALE", "ITEM_DEEPSEATOOTH", "ITEM_DEEPSEASCALE",
}


class GenerationError(ValueError):
    """The requested plan cannot be generated safely (raised before any ROM is built)."""


@dataclass
class Plan:
    evolution_changes: list[dict] = field(default_factory=list)
    starters: dict = field(default_factory=dict)
    wild: list[dict] = field(default_factory=list)
    trainers: list[dict] = field(default_factory=list)
    doubles: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    obtainable: list[int] = field(default_factory=list)
    route19: dict = field(default_factory=dict)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

    def to_json(self) -> dict:
        return {k: copy.deepcopy(v) for k, v in self.__dict__.items()}


class Generator:
    def __init__(self, data: W2UData, settings: dict, key: bytes) -> None:
        self.data = data
        self.settings = settings
        self.key = key
        self.plan = Plan()
        self.model = data.model
        self.pre_evolution: dict[int, int] = {}
        for species, evos in data.evolutions.items():
            for evo in evos:
                target = data.enums["species"].get(evo.target)
                if target is not None and data.model.is_ordinary(target):
                    self.pre_evolution.setdefault(target, species)

    # --- species facts ------------------------------------------------------
    def evolution_targets(self, species: int) -> list[int]:
        out = []
        for evo in self.data.evolutions.get(species, []):
            target = self.data.enums["species"].get(evo.target)
            if target is not None and self.model.is_ordinary(target):
                out.append(target)
        return out

    def is_legendary(self, species: int) -> bool:
        p = self.data.personal[species]
        return UNDISCOVERED_EGG_GROUP in p.egg_groups and p.capture_rate <= 45 and not self.evolution_targets(species) \
            and species not in self.pre_evolution

    def usable(self, species: int, level: int) -> bool:
        return bool(self.data.damaging_moves_by_level(species, level))

    def base_pool(self) -> list[int]:
        pool = []
        for species, p in sorted(self.data.personal.items()):
            if not self.model.encounter_encodable(species) or p.bst <= 0:
                continue
            if not self.data.learnsets.get(species):
                continue
            if self.is_legendary(species) and not self.settings["species"]["include_legendaries"]:
                continue
            pool.append(species)
        if len(pool) < 50:
            raise GenerationError(f"only {len(pool)} species are eligible; refusing to randomize")
        return pool

    def pick_similar(self, stream: Stream, original: int, candidates: list[int], level: int, *,
                     similar: bool, exclude: set[int] = frozenset()) -> int:
        usable = [s for s in candidates if s not in exclude and self.usable(s, level)]
        if not usable:
            raise GenerationError(f"no usable replacement for species {original} at level {level}")
        if not similar:
            return stream.choice(usable)
        target = self.data.personal[original].bst
        for band in (0.10, 0.20, 0.35, 0.60, 10.0):
            near = [s for s in usable if abs(self.data.personal[s].bst - target) <= target * band]
            if near:
                return stream.choice(near)
        return stream.choice(usable)

    # --- evolutions -------------------------------------------------------------
    def normalize_evolutions(self) -> None:
        available_items = set(VANILLA_EVOLUTION_ITEMS)
        for species in sorted(self.data.evolutions):
            if not self.model.is_ordinary(species):
                continue
            original = self.data.evolutions[species]
            changed = []
            reasons = []
            for evo in original:
                new, reason = self._normalize_one(species, evo, available_items)
                changed.extend(new)
                if reason:
                    reasons.append(reason)
            if reasons:
                if len(changed) > 8:
                    raise GenerationError(f"{self.data.species_label(species)} needs more than 8 evolution entries")
                self.plan.evolution_changes.append({
                    "species": species, "label": self.data.species_label(species),
                    "before": [e.__dict__ for e in original], "after": [e.__dict__ for e in changed], "reasons": reasons,
                })
                self.data.write_evolutions(species, changed)

    def _fallback_level(self, species: int) -> int:
        return 40 if species in self.pre_evolution else 30

    def _normalize_one(self, species: int, evo: Evolution, items: set[str]) -> tuple[list[Evolution], str | None]:
        method = evo.method
        if method in ALWAYS_FEASIBLE:
            return [evo], None
        level = self._fallback_level(species)
        if method in ITEM_METHODS:
            if isinstance(evo.parameter, str) and evo.parameter in items:
                return [evo], None
            item = self.data.item_names.get(evo.parameter) if isinstance(evo.parameter, int) else evo.parameter
            if item in items:
                return [evo], None
            return [Evolution("METHOD_LEVELUP", level, evo.target)], f"{method} needs unobtainable {item}; now level {level}"
        if method == "METHOD_TRADE_ITEM":
            item = self.data.item_names.get(evo.parameter) if isinstance(evo.parameter, int) else evo.parameter
            if item in items:
                return [Evolution("METHOD_ITEM_HELD_DAY", evo.parameter, evo.target),
                        Evolution("METHOD_ITEM_HELD_NIGHT", evo.parameter, evo.target)], \
                    f"trade holding {item} -> level up holding {item} (day or night)"
            return [Evolution("METHOD_LEVELUP", level, evo.target)], f"trade holding unobtainable {item} -> level {level}"
        if method == "METHOD_LEVELUP_MOVE":
            move = evo.parameter if isinstance(evo.parameter, str) else self._move_label(evo.parameter)
            if move and any(m == move for _, m in self.data.learnsets.get(species, [])):
                return [evo], None
            return [Evolution("METHOD_LEVELUP", level, evo.target)], f"move {move} not learnable by level-up -> level {level}"
        return [Evolution("METHOD_LEVELUP", level, evo.target)], f"{method} is not feasible in this alpha -> level {level}"

    def _move_label(self, move_id: int) -> str | None:
        for label, value in self.data.enums["moves"].items():
            if value == move_id:
                return label
        return None

    # --- wild ---------------------------------------------------------------
    def randomize_wild(self, pool: list[int]) -> set[int]:
        stream = Stream(self.key, "wild")
        chosen: set[int] = set()
        mode = self.settings["wild"]["mode"]
        for index in sorted(self.data.encounters):
            document = copy.deepcopy(self.data.encounters[index])
            mapping: dict[int, int] = {}
            changes = []
            for container_key in sorted(document):
                container = document[container_key]
                for category, kind, _count in enc.TABLES:
                    table = container.get(category, {}).get(kind, {})
                    for slot_key in sorted(table, key=lambda k: int(k.split()[-1])):
                        slot = table[slot_key]
                        label = slot["Species"]
                        if label == "SPECIES_NONE":
                            continue
                        original = self.data.species_id(label)
                        if mode == "vanilla":
                            chosen.add(original)
                            continue
                        if original not in mapping:
                            mapping[original] = self.pick_similar(
                                stream, original, pool, int(slot["Minimum Level"]),
                                similar=self.settings["wild"]["similar_strength"], exclude=set(mapping.values()))
                        new = mapping[original]
                        if not self.usable(new, int(slot["Minimum Level"])):
                            new = self.pick_similar(stream, original, pool, int(slot["Minimum Level"]),
                                                    similar=self.settings["wild"]["similar_strength"])
                        slot["Species"] = self.data.species_label(new)
                        slot["Form"] = 0
                        chosen.add(new)
                        changes.append((container_key, f"{category}.{kind}", slot_key, label, slot["Species"]))
            if mode != "vanilla":
                self.data.encounters[index] = document
                self.dirty_encounters.add(index)
                self.plan.wild.append({"record": index, "mapping": {self.data.species_label(k): self.data.species_label(v)
                                                                    for k, v in sorted(mapping.items())},
                                       "slots_changed": len(changes)})
        self.plan.features["wild"] = "applied" if mode != "vanilla" else "vanilla"
        return chosen

    # --- starters -------------------------------------------------------------
    def choose_starters(self, pool: list[int]) -> list[int]:
        vanilla = [self.data.species_id(label) for label in VANILLA_STARTERS]
        if self.settings["starters"]["mode"] == "vanilla":
            self.plan.starters = {"vanilla": vanilla, "chosen": vanilla}
            self.plan.features["starters"] = "vanilla"
            return vanilla
        stream = Stream(self.key, "starters")
        candidates = [s for s in pool
                      if s not in self.pre_evolution and self.evolution_targets(s)
                      and self.data.personal[s].bst <= 360 and self.usable(s, 5) and not self.is_legendary(s)]
        if len(candidates) < 3:
            raise GenerationError("fewer than 3 species satisfy the starter constraints")
        chosen: list[int] = []
        types_used: set[str] = set()
        for _ in range(3):
            fresh = [s for s in candidates if s not in chosen and self.data.personal[s].types[0] not in types_used]
            pick = stream.choice(fresh or [s for s in candidates if s not in chosen])
            chosen.append(pick)
            types_used.add(self.data.personal[pick].types[0])
        self.plan.starters = {"vanilla": vanilla, "chosen": chosen,
                              "labels": [self.data.species_label(s) for s in chosen]}
        self.plan.features["starters"] = "planned (script sites applied after assembly)"
        return chosen

    def line_member_at(self, base: int, level: int) -> int:
        """The member of ``base``'s evolution line a trainer would plausibly use at ``level``."""
        current = base
        for _ in range(3):
            nxt = None
            for evo in self.data.evolutions.get(current, []):
                target = self.data.enums["species"].get(evo.target)
                if target is None or not self.model.is_ordinary(target):
                    continue
                threshold = evo.parameter if evo.method in LEVEL_METHODS and isinstance(evo.parameter, int) else 36
                if level >= threshold:
                    nxt = target
                    break
            if nxt is None:
                break
            current = nxt
        return current

    def starter_line_index(self, species: int, vanilla: list[int]) -> int | None:
        for _ in range(3):
            if species in vanilla:
                return vanilla.index(species)
            species = self.pre_evolution.get(species, -1)
            if species < 0:
                return None
        return None

    # --- trainers and doubles ----------------------------------------------------
    MAJOR_CLASS_NAMES = ("Leader", "Elite Four", "Champion")
    MAJOR_TRAINER_NAMES = ("Rival", "Ghetsis", "Colress", "Zinzolin", "N", "Shadow")

    def is_major(self, trainer_id: int) -> str | None:
        document = self.data.trainers[trainer_id]
        cls_name = self.data.trainer_classes.get(int(document["CLASS"]), "")
        name = self.data.trainer_names.get(trainer_id, "")
        if any(cls_name == c for c in self.MAJOR_CLASS_NAMES):
            return f"class {cls_name}"
        if name in self.MAJOR_TRAINER_NAMES:
            return f"story trainer {name}"
        return None

    def randomize_trainers(self, closure: list[int], starters: list[int]) -> None:
        stream = Stream(self.key, "trainers")
        vanilla_starters = [self.data.species_id(label) for label in VANILLA_STARTERS]
        mode = self.settings["trainers"]["mode"]
        doubles = self.settings["doubles"]
        for trainer_id in sorted(self.data.trainers):
            original = self.data.trainers[trainer_id]
            document = copy.deepcopy(original)
            party = document.get("PARTY", [])
            if not party:
                continue
            changed = False
            for member in party:
                label = member["SPECIES"]
                if label == "SPECIES_NONE":
                    continue
                species = self.data.species_id(label)
                level = int(member["LEVEL"])
                line = self.starter_line_index(species, vanilla_starters)
                if line is not None and self.settings["starters"]["mode"] == "random":
                    new = self.line_member_at(starters[line], level)
                elif mode == "random" and self.model.is_ordinary(species):
                    keep_legend = self.is_legendary(species)
                    pool = closure if not keep_legend else [s for s in self.data.personal if self.is_legendary(s) and self.model.encounter_encodable(s)] or closure
                    new = self.pick_similar(stream, species, pool, level, similar=self.settings["trainers"]["similar_strength"])
                else:
                    continue
                if new != species:
                    member["SPECIES"] = self.data.species_label(new)
                    member["FORM"] = 0
                    if "MOVES" in member:
                        member["MOVES"] = self.data.default_moves(new, level)
                    changed = True
            reason = self.is_major(trainer_id)
            convert = (doubles["major_battles"] and reason) or doubles["all_trainers"]
            if convert and int(document["BATTLE_TYPE"]) == 0:
                name = self.data.trainer_names.get(trainer_id, "")
                if name == "Rival" and len(original.get("PARTY", [])) == 1:
                    self.plan.doubles.append({"trainer": trainer_id, "name": name, "converted": False,
                                              "reason": "first rival battle: the player can only own one Pokémon"})
                else:
                    self._convert_to_doubles(stream, trainer_id, document, closure, reason or "all_trainers setting")
                    changed = True
            if changed:
                self.data.trainers[trainer_id] = document
                self.dirty_trainers.add(trainer_id)
                self.plan.trainers.append({
                    "trainer": trainer_id, "name": self.data.trainer_names.get(trainer_id, ""),
                    "class": self.data.trainer_classes.get(int(document["CLASS"]), ""),
                    "battle_type": int(document["BATTLE_TYPE"]),
                    "party": [{"species": m["SPECIES"], "level": int(m["LEVEL"])} for m in document["PARTY"]],
                })
        self.plan.features["trainers"] = "applied" if mode == "random" else "vanilla"
        self.plan.features["doubles"] = "applied" if doubles["major_battles"] else "off"

    def _convert_to_doubles(self, stream: Stream, trainer_id: int, document: dict, closure: list[int], reason: str) -> None:
        party = document["PARTY"]
        padded = False
        if len(party) < 2:
            first = party[0]
            level = int(first["LEVEL"])
            base = self.data.species_id(first["SPECIES"])
            new = self.pick_similar(stream, base, closure, level, similar=True, exclude={base})
            extra = copy.deepcopy(first)
            extra["SPECIES"] = self.data.species_label(new)
            extra["FORM"] = 0
            if "MOVES" in extra:
                extra["MOVES"] = self.data.default_moves(new, level)
            party.append(extra)
            padded = True
        document["BATTLE_TYPE"] = 1
        document["AI"] = int(document["AI"]) | 0x80
        self.plan.doubles.append({"trainer": trainer_id, "name": self.data.trainer_names.get(trainer_id, ""),
                                  "converted": True, "reason": reason, "padded_party": padded})

    # --- diagnostic --------------------------------------------------------------
    def apply_route19_fixture(self) -> int:
        index = resolve_route19(self.data)
        document = copy.deepcopy(self.data.encounters[index])
        labels = {enc.PATRAT: "SPECIES_PATRAT", enc.CARBINK: "SPECIES_CARBINK", enc.CHESPIN: "SPECIES_CHESPIN"}
        for label_id, label in labels.items():
            if self.data.enums["species"].get(label) != label_id:
                raise GenerationError(f"{label} is not species {label_id} in this checkout")
        for container_key in sorted(document):
            table = document[container_key]["GRASS"]["SINGLES"]
            keys = sorted(table, key=lambda k: int(k.split()[-1]))
            if len(keys) != 12:
                raise GenerationError("Route 19 grass table does not have 12 slots")
            for key, slot in zip(keys, enc.fixture_grass_slots()):
                table[key] = {"Species": labels[slot.species], "Form": 0,
                              "Minimum Level": slot.min_level, "Maximum Level": slot.max_level}
        self.data.write_encounter(index, document)
        self.plan.route19 = {"record": index, "evidence": route19_evidence(self.data, index),
                             "species": {"Patrat": 504, "Carbink": 703, "Chespin": 650},
                             "levels": {"Patrat": 5, "Carbink": 6, "Chespin": 7}, "weights": {"Patrat": 35, "Carbink": 35, "Chespin": 30}}
        self.plan.features["diagnostic_route19"] = "applied"
        return index

    # --- constraints ---------------------------------------------------------------
    def vanilla_wild_species(self) -> set[int]:
        species = set()
        for document in self.vanilla_encounters.values():
            for container in document.values():
                for category, kind, _ in enc.TABLES:
                    for slot in container.get(category, {}).get(kind, {}).values():
                        if slot["Species"] != "SPECIES_NONE":
                            species.add(self.data.species_id(slot["Species"]))
        return species

    def machine_coverage_failures(self, obtainable: set[int]) -> list[str]:
        vanilla = self.vanilla_wild_species()
        failures = []
        for bit in range(MACHINE_BITS):
            before = sum(1 for s in vanilla if self.data.personal[s].can_learn_machine(bit))
            if before == 0:
                continue
            need = min(before, MIN_MACHINE_LEARNERS)
            after = sum(1 for s in obtainable if self.data.personal[s].can_learn_machine(bit))
            if after < need:
                failures.append(f"machine bit {bit}: {after} obtainable learners < {need} (vanilla wild had {before})")
        return failures

    def check_constraints(self, wild: set[int], starters: list[int], closure: list[int]) -> None:
        closure_set = set(closure)
        obtainable = set(wild) | {s for base in starters for s in self.line(base)}
        coverage = self.machine_coverage_failures(obtainable)
        self.plan.check("machine_compatibility_coverage", not coverage,
                        "every TM/HM compatibility bit offered by vanilla wild species keeps at least "
                        f"{MIN_MACHINE_LEARNERS} obtainable learners" if not coverage else "; ".join(coverage[:5]))
        for s in starters:
            self.plan.check(f"starter_usable:{s}", self.usable(s, 5), f"{self.data.species_label(s)} has a damaging move at level 5")
            self.plan.check(f"starter_evolves:{s}", bool(self.evolution_targets(s)), f"{self.data.species_label(s)} has an evolution line")
        bad = []
        for entry in self.plan.trainers:
            for member in entry["party"]:
                species = self.data.species_id(member["species"])
                if not self.model.is_ordinary(species) or not 1 <= member["level"] <= 100:
                    bad.append((entry["trainer"], member))
                elif not self.usable(species, member["level"]):
                    bad.append((entry["trainer"], member))
        self.plan.check("trainer_parties_legal", not bad, f"{len(bad)} illegal or unusable trainer party members")
        for entry in self.plan.doubles:
            if entry["converted"]:
                party = self.data.trainers[entry["trainer"]]["PARTY"]
                self.plan.check(f"doubles_party:{entry['trainer']}", len(party) >= 2, f"{len(party)} party members")
        unobtainable = sorted(s for s in wild if s not in closure_set)
        self.plan.check("wild_species_obtainable", not unobtainable, f"{len(unobtainable)} wild species outside the obtainable closure")
        evo_targets_missing = sorted({t for s in closure_set for t in self.evolution_targets(s) if t not in closure_set
                                      and self.model.encounter_encodable(t)})
        self.plan.check("evolution_closure", not evo_targets_missing,
                        f"{len(evo_targets_missing)} evolution targets of obtainable species are outside the closure")
        failures = [c for c in self.plan.checks if c["status"] == "fail"]
        if failures:
            raise GenerationError("progression/legality constraints failed:\n  " + "\n  ".join(f"{c['name']}: {c['detail']}" for c in failures))

    def line(self, base: int) -> list[int]:
        out, frontier = [base], [base]
        while frontier:
            nxt = []
            for s in frontier:
                for t in self.evolution_targets(s):
                    if t not in out:
                        out.append(t)
                        nxt.append(t)
            frontier = nxt
        return out

    def closure(self, roots: set[int]) -> list[int]:
        seen: set[int] = set()
        for root in roots:
            seen.update(self.line(root))
        return sorted(s for s in seen if self.model.encounter_encodable(s))

    # --- driver -----------------------------------------------------------------------
    def run(self) -> Plan:
        self.vanilla_encounters = copy.deepcopy(self.data.encounters)
        if self.settings["mode"] == "diagnostic-route19":
            self.apply_route19_fixture()
            for feature in ("wild", "trainers", "starters", "items", "gifts", "statics"):
                self.plan.features[feature] = "vanilla"
            self.plan.features["doubles"] = "off"
            return self.plan
        self.normalize_evolutions()
        vanilla_trainers = copy.deepcopy(self.data.trainers)
        base_key = self.key
        last_error: GenerationError | None = None
        for attempt in range(MAX_ATTEMPTS):
            # Deterministic retries: attempt N always uses the same derived key.
            self.key = base_key if attempt == 0 else hashlib.sha256(base_key + b"|attempt|" + str(attempt).encode()).digest()
            evolution_changes = self.plan.evolution_changes
            self.plan = Plan(evolution_changes=evolution_changes)
            self.plan.check("evolutions_feasible", True, f"{len(evolution_changes)} species had impossible methods normalized")
            for index, document in self.vanilla_encounters.items():
                self.data.encounters[index] = copy.deepcopy(document)
            for index, document in vanilla_trainers.items():
                self.data.trainers[index] = copy.deepcopy(document)
            self.dirty_encounters, self.dirty_trainers = set(), set()
            try:
                pool = self.base_pool()
                starters = self.choose_starters(pool)
                wild = self.randomize_wild(pool)
                closure = self.closure(wild | set(starters))
                self.plan.obtainable = closure
                self.randomize_trainers(closure, starters)
                self.check_constraints(wild, starters, closure)
            except GenerationError as error:
                last_error = error
                continue
            self.plan.features["attempt"] = attempt
            # Only a plan that passed every constraint is written to the workroot sources.
            for index in sorted(self.dirty_encounters):
                self.data.write_encounter(index, self.data.encounters[index])
            for index in sorted(self.dirty_trainers):
                self.data.write_trainer(index, self.data.trainers[index])
            return self.plan
        raise GenerationError(f"no valid plan after {MAX_ATTEMPTS} deterministic attempts: {last_error}")


# --- Route 19 resolution --------------------------------------------------------------

ROUTE19_SIGNATURE = {"SPECIES_PATRAT", "SPECIES_PURRLOIN", "SPECIES_PIDOVE"}


def route19_evidence(data: W2UData, index: int) -> dict:
    document = data.encounters[index]
    grass = sorted({slot["Species"] for c in document.values() for slot in c["GRASS"]["SINGLES"].values()})
    levels = sorted({int(slot["Maximum Level"]) for c in document.values() for slot in c["GRASS"]["SINGLES"].values()})
    return {"seasons": len(document), "grass_species": grass, "grass_max_levels": levels,
            "rule": "unique four-season record whose ordinary grass contains Patrat, Purrloin, and Pidove at levels <= 5"}


def resolve_route19(data: W2UData) -> int:
    """Resolve Route 19 by source-data evidence; require exactly one match (never trust a fixed index)."""
    matches = []
    for index, document in data.encounters.items():
        if len(document) != enc.SEASONS:
            continue
        try:
            slots = [slot for c in document.values() for slot in c["GRASS"]["SINGLES"].values()]
        except (KeyError, AttributeError):
            continue
        species = {slot["Species"] for slot in slots}
        if ROUTE19_SIGNATURE <= species and all(0 < int(slot["Maximum Level"]) <= 5 for slot in slots):
            matches.append(index)
    if len(matches) != 1:
        raise GenerationError(f"Route 19 must match exactly one encounter record by evidence; matched {matches}")
    return matches[0]
