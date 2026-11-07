"""Generation-neutral randomization policy over the normalized model.

Nothing here knows about TOML, NARCs, scripts, or any engine. The planner
copies the model, changes the copy, checks obtainability/evolution/progression
constraints, and returns (planned model, plan). Each feature draws from its
own named RNG stream so features never perturb each other.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field

from ..model import (BattleFormat, Evolution, EvolutionMethod, GameModel, PartyMember, PokemonSite, SpeciesKey,
                     TrainerRole)
from .rng import Stream

MACHINE_BITS = 128
MIN_MACHINE_LEARNERS = 2
MAX_ATTEMPTS = 8
DIAGNOSTIC_FIXTURE = ((SpeciesKey(504), 5, 35), (SpeciesKey(703), 6, 35), (SpeciesKey(650), 7, 30))


class PlanError(ValueError):
    """No safe plan exists; raised before any engine data is written."""


def assign_by_rates(rates: tuple[int, ...], targets: tuple[tuple[object, int], ...]) -> list[object]:
    """Assign slots to targets so each target's rate sum is exact (backtracking subset assignment)."""
    order = sorted(range(len(rates)), key=lambda i: -rates[i])
    remaining = {i: w for i, (_, w) in enumerate(targets)}
    result: list[int | None] = [None] * len(rates)

    def solve(pos: int) -> bool:
        if pos == len(order):
            return all(v == 0 for v in remaining.values())
        slot = order[pos]
        for t in remaining:
            if remaining[t] >= rates[slot]:
                remaining[t] -= rates[slot]
                result[slot] = t
                if solve(pos + 1):
                    return True
                remaining[t] += rates[slot]
        return False

    if sum(w for _, w in targets) != sum(rates) or not solve(0):
        raise PlanError(f"weights {[w for _, w in targets]} cannot be realized exactly over slot rates {list(rates)}")
    return [targets[t][0] for t in result]


@dataclass
class Plan:
    features: dict = field(default_factory=dict)
    evolution_changes: list = field(default_factory=list)
    starters: dict = field(default_factory=dict)
    wild: list = field(default_factory=list)
    trainers: list = field(default_factory=list)
    doubles: list = field(default_factory=list)
    sites: list = field(default_factory=list)
    items: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    obtainable: list = field(default_factory=list)
    diagnostic: dict = field(default_factory=dict)

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

    def to_json(self) -> dict:
        return copy.deepcopy(self.__dict__)


class Planner:
    def __init__(self, model: GameModel, settings: dict, key: bytes, diagnostic_area: tuple[int, dict] | None = None):
        self.base = model
        self.settings = settings
        self.root_key = key
        self.diagnostic_area = diagnostic_area

    # --- facts ------------------------------------------------------------
    def usable(self, m: GameModel, key: SpeciesKey, level: int) -> bool:
        return bool(m.damaging_moves_by(key, level))

    def pre_evolutions(self, m: GameModel) -> dict[SpeciesKey, SpeciesKey]:
        pre = {}
        for key, s in m.species.items():
            for e in s.evolutions:
                pre.setdefault(e.target, key)
        return pre

    def line(self, m: GameModel, base: SpeciesKey) -> list[SpeciesKey]:
        out, frontier = [base], [base]
        while frontier:
            nxt = []
            for s in frontier:
                for e in m.sp(s).evolutions:
                    if e.target in m.species and e.target not in out:
                        out.append(e.target)
                        nxt.append(e.target)
            frontier = nxt
        return out

    def pool(self, m: GameModel) -> list[SpeciesKey]:
        limit = self.settings["species"]["national_max"]
        keys = [k for k, s in sorted(m.species.items())
                if k.form == 0 and k.national <= limit and s.bst > 0 and s.learnset
                and (self.settings["species"]["include_legendaries"] or not s.legendary)]
        if len(keys) < 50:
            raise PlanError(f"only {len(keys)} species are eligible")
        return keys

    def similar(self, m: GameModel, stream: Stream, original: SpeciesKey, candidates, level: int, *, similar: bool,
                exclude=frozenset()) -> SpeciesKey:
        usable = [k for k in candidates if k not in exclude and self.usable(m, k, level)]
        if not usable:
            raise PlanError(f"no usable replacement for {original} at level {level}")
        if not similar:
            return stream.choice(usable)
        target = m.sp(original).bst
        for band in (0.10, 0.20, 0.35, 0.60, 10.0):
            near = [k for k in usable if abs(m.sp(k).bst - target) <= target * band]
            if near:
                return stream.choice(near)
        return stream.choice(usable)

    # --- evolutions ---------------------------------------------------------
    def normalize_evolutions(self, m: GameModel, plan: Plan) -> None:
        pre = self.pre_evolutions(m)
        for key, s in sorted(m.species.items()):
            new, reasons = [], []
            for e in s.evolutions:
                result, reason = self._normalize(m, key, e, pre)
                new.extend(result)
                if reason:
                    reasons.append(reason)
            if reasons:
                plan.evolution_changes.append({"species": str(key), "name": s.name, "reasons": reasons})
                s.evolutions = new
        plan.check("evolutions_feasible", True, f"{len(plan.evolution_changes)} species had infeasible methods normalized")

    def _normalize(self, m: GameModel, key: SpeciesKey, e: Evolution, pre) -> tuple[list[Evolution], str | None]:
        level = 40 if key in pre else 30
        fallback = Evolution(EvolutionMethod.LEVEL, e.target, level=level, ext={"changed": True})
        if e.method in (EvolutionMethod.ITEM_USE, EvolutionMethod.ITEM_HOLD):
            if e.item in m.obtainable_evolution_items and e.method in m.feasible_evolution_methods:
                return [e], None
            return [fallback], f"{e.method.value} needs unobtainable {e.item}; now level {level}"
        if e.method is EvolutionMethod.TRADE_ITEM:
            if e.item in m.obtainable_evolution_items and EvolutionMethod.ITEM_HOLD in m.feasible_evolution_methods:
                return [Evolution(EvolutionMethod.ITEM_HOLD, e.target, item=e.item, ext={"changed": True, "time": t})
                        for t in ("day", "night")], f"trade holding {e.item} -> level up holding {e.item} (day or night)"
            return [fallback], f"trade holding unobtainable {e.item} -> level {level}"
        if e.method is EvolutionMethod.MOVE_KNOWN:
            if e.move and any(mv == e.move for _, mv in m.sp(key).learnset):
                return [e], None
            return [fallback], f"move {e.move} not learnable by level-up -> level {level}"
        if e.method in m.feasible_evolution_methods:
            return [e], None
        return [fallback], f"{e.method.value} is not feasible in this game -> level {level}"

    # --- features ---------------------------------------------------------------
    # Each stream depends only on the seed/input key and the settings sections that shape that
    # feature, so toggling an unrelated feature never reshuffles it.
    STREAM_SECTIONS = {"wild": ("species", "wild", "evolutions"), "starters": ("species", "starters", "evolutions"),
                       "trainers": ("species", "trainers", "doubles", "starters", "wild", "evolutions"),
                       "gifts": ("species", "gifts"), "statics": ("species", "statics"), "items": ("items",)}

    def stream(self, name: str) -> Stream:
        from .settings import canonical_json
        scope = {section: self.settings[section] for section in self.STREAM_SECTIONS[name]}
        return Stream(self.key, name + "|" + canonical_json(scope))

    def wild(self, m: GameModel, plan: Plan, pool) -> set[SpeciesKey]:
        chosen: set[SpeciesKey] = set()
        mode = self.settings["wild"]["mode"]
        stream = self.stream("wild")
        for area in m.encounters:
            mapping: dict[SpeciesKey, SpeciesKey] = {}
            for slot in area.all_slots():
                if slot.species is None:
                    continue
                if mode == "vanilla":
                    chosen.add(slot.species)
                    continue
                if slot.species not in mapping:
                    mapping[slot.species] = self.similar(m, stream, slot.species, pool, slot.min_level,
                                                         similar=self.settings["wild"]["similar_strength"],
                                                         exclude=set(mapping.values()))
                new = mapping[slot.species]
                if not self.usable(m, new, slot.min_level):
                    new = self.similar(m, stream, slot.species, pool, slot.min_level, similar=True)
                slot.species = new
                chosen.add(new)
            if mode != "vanilla":
                plan.wild.append({"area": area.id, "mapping": {str(k): str(v) for k, v in sorted(mapping.items())}})
        plan.features["wild"] = mode
        return chosen

    def starters(self, m: GameModel, plan: Plan, pool) -> dict[SpeciesKey, SpeciesKey]:
        vanilla = sorted({s.species for s in m.starters})
        if self.settings["starters"]["mode"] == "vanilla" or not vanilla:
            plan.features["starters"] = "vanilla"
            return {v: v for v in vanilla}
        pre = self.pre_evolutions(m)
        candidates = [k for k in pool if k not in pre and m.sp(k).evolutions and m.sp(k).bst <= 360
                      and self.usable(m, k, 5) and not m.sp(k).legendary]
        if len(candidates) < len(vanilla):
            raise PlanError("not enough species satisfy the starter constraints")
        stream = self.stream("starters")
        mapping, used_types = {}, set()
        for original in vanilla:
            fresh = [k for k in candidates if k not in mapping.values() and m.sp(k).types[0] not in used_types]
            pick = stream.choice(fresh or [k for k in candidates if k not in mapping.values()])
            mapping[original] = pick
            used_types.add(m.sp(pick).types[0])
        for site in m.starters:
            plan.sites.append({"site": site.id, "kind": "starter", "before": str(site.species), "after": str(mapping[site.species])})
            site.species = mapping[site.species]
        plan.starters = {"vanilla": [str(v) for v in vanilla], "chosen": [str(mapping[v]) for v in vanilla],
                         "names": [m.sp(mapping[v]).name for v in vanilla]}
        plan.features["starters"] = "random"
        return mapping

    def stage_at(self, m: GameModel, base: SpeciesKey, level: int) -> SpeciesKey:
        current = base
        for _ in range(3):
            nxt = None
            for e in m.sp(current).evolutions:
                threshold = e.level if e.method in (EvolutionMethod.LEVEL, EvolutionMethod.LEVEL_CONDITIONAL) and e.level else 36
                if level >= threshold:
                    nxt = e.target
                    break
            if nxt is None:
                break
            current = nxt
        return current

    def starter_line(self, m: GameModel, species: SpeciesKey, vanilla, pre) -> SpeciesKey | None:
        for _ in range(3):
            if species in vanilla:
                return species
            species = pre.get(species)
            if species is None:
                return None
        return None

    def trainers(self, m: GameModel, plan: Plan, closure, starter_map) -> None:
        stream = self.stream("trainers")
        mode = self.settings["trainers"]["mode"]
        doubles = self.settings["doubles"]
        pre = self.pre_evolutions(m)
        legends = [k for k in closure if m.sp(k).legendary] or closure
        for trainer in m.trainers:
            original_size = len(trainer.party)
            for member in trainer.party:
                base = self.starter_line(m, member.species, starter_map, pre)
                if base is not None and starter_map.get(base, base) != base:
                    new = self.stage_at(m, starter_map[base], member.level)
                elif mode == "random":
                    pool = legends if m.sp(member.species).legendary else closure
                    new = self.similar(m, stream, member.species, pool, member.level,
                                       similar=self.settings["trainers"]["similar_strength"])
                else:
                    continue
                if new != member.species:
                    member.species = new
                    if member.moves is not None:
                        member.moves = m.default_moves(new, member.level)
            convert = (doubles["major_battles"] and trainer.role.major) or doubles["all_trainers"]
            if convert and trainer.battle_format is BattleFormat.SINGLE and trainer.party:
                if trainer.role is TrainerRole.RIVAL and original_size == 1:
                    plan.doubles.append({"trainer": trainer.id, "name": trainer.name, "converted": False,
                                         "reason": "first rival battle: the player can only own one Pokémon"})
                else:
                    padded = False
                    if len(trainer.party) < 2:
                        first = trainer.party[0]
                        extra = self.similar(m, stream, first.species, closure, first.level, similar=True, exclude={first.species})
                        trainer.party.append(PartyMember(extra, first.level,
                                                         m.default_moves(extra, first.level) if first.moves is not None else None,
                                                         dict(first.ext)))
                        padded = True
                    trainer.battle_format = BattleFormat.DOUBLE
                    plan.doubles.append({"trainer": trainer.id, "name": trainer.name, "role": trainer.role.value,
                                         "converted": True, "padded_party": padded})
            plan.trainers.append({"trainer": trainer.id, "name": trainer.name, "class": trainer.trainer_class,
                                  "format": trainer.battle_format.value,
                                  "party": [{"species": str(p.species), "name": m.sp(p.species).name, "level": p.level}
                                            for p in trainer.party]})
        plan.features["trainers"] = mode
        plan.features["major_battle_doubles"] = "on" if doubles["major_battles"] else "off"

    def pokemon_sites(self, m: GameModel, plan: Plan, sites: list[PokemonSite], kind: str, closure) -> None:
        if self.settings[kind]["mode"] != "random":
            plan.features[kind] = "vanilla"
            return
        stream = self.stream(kind)
        for site in sites:
            new = self.similar(m, stream, site.species, closure, site.level, similar=True)
            plan.sites.append({"site": site.id, "kind": kind, "before": str(site.species), "after": str(new)})
            site.species = new
        plan.features[kind] = "random"

    def items(self, m: GameModel, plan: Plan) -> None:
        mode = self.settings["items"]["field"]
        if mode != "shuffle":
            plan.features["field_items"] = "vanilla"
            return
        stream = self.stream("items")
        for cls in ("ordinary", "tm"):
            group = [loc for loc in m.item_locations if loc.item_class == cls]
            shuffled = stream.shuffle([loc.item for loc in group])
            for loc, item in zip(group, shuffled):
                if item != loc.item:
                    plan.items.append({"site": loc.id, "before": loc.item, "after": item})
                    loc.item = item
        plan.features["field_items"] = "shuffle (key items and HMs fixed)"

    # --- constraints -------------------------------------------------------------
    def check_constraints(self, m: GameModel, plan: Plan, wild, starter_map, closure) -> None:
        closure_set = set(closure)
        vanilla_wild = {s.species for a in self.base.encounters for s in a.all_slots() if s.species}
        obtainable = set(wild) | {k for base in starter_map.values() for k in self.line(m, base)}
        failures = []
        for bit in range(MACHINE_BITS):
            before = sum(1 for k in vanilla_wild if bit in m.sp(k).machines)
            if before:
                after = sum(1 for k in obtainable if bit in m.sp(k).machines)
                if after < min(before, MIN_MACHINE_LEARNERS):
                    failures.append(f"machine bit {bit}: {after} learners")
        plan.check("machine_compatibility_coverage", not failures,
                   "; ".join(failures[:5]) or f"every TM/HM bit offered by vanilla wild species keeps >= {MIN_MACHINE_LEARNERS} learners")
        for base in starter_map.values():
            plan.check(f"starter_usable:{base}", self.usable(m, base, 5) and bool(m.sp(base).evolutions),
                       f"{m.sp(base).name}: damaging move at level 5 and an evolution line")
        bad = [(t.id, str(p.species)) for t in m.trainers for p in t.party
               if not 1 <= p.level <= 100 or not self.usable(m, p.species, p.level)]
        plan.check("trainer_parties_legal", not bad, f"{len(bad)} illegal or unusable members {bad[:3]}")
        for d in plan.doubles:
            if d["converted"]:
                size = len(next(t for t in m.trainers if t.id == d["trainer"]).party)
                plan.check(f"doubles_party:{d['trainer']}", size >= 2, f"{size} party members")
        trainer_species = {p.species for t in m.trainers for p in t.party}
        if self.settings["trainers"]["mode"] == "random":
            outside = sorted(str(k) for k in trainer_species if k not in closure_set)
            plan.check("trainer_species_obtainable", not outside, f"{len(outside)} trainer species outside the closure")
        missing = sorted({str(e.target) for k in closure_set for e in m.sp(k).evolutions if e.target not in closure_set})
        plan.check("evolution_closure", not missing, f"{len(missing)} evolution targets outside the closure")
        infeasible = [(str(k), e.method.value) for k in closure_set for e in m.sp(k).evolutions
                      if e.method not in m.feasible_evolution_methods]
        plan.check("evolutions_achievable", not infeasible, f"{len(infeasible)} infeasible evolution methods {infeasible[:3]}")
        failed = [c for c in plan.checks if c["status"] == "fail"]
        if failed:
            raise PlanError("; ".join(f"{c['name']}: {c['detail']}" for c in failed))

    def closure(self, m: GameModel, roots) -> list[SpeciesKey]:
        seen = set()
        for r in roots:
            seen.update(self.line(m, r))
        limit = self.settings["species"]["national_max"]
        return sorted(k for k in seen if k.national <= limit)

    # --- driver --------------------------------------------------------------------
    def run(self) -> tuple[GameModel, Plan]:
        if self.settings["mode"] == "diagnostic-route19":
            return self.diagnostic()
        last = None
        for attempt in range(MAX_ATTEMPTS):
            self.key = self.root_key if attempt == 0 else hashlib.sha256(self.root_key + b"|attempt|%d" % attempt).digest()
            m = copy.deepcopy(self.base)
            plan = Plan()
            try:
                if self.settings["evolutions"]["normalize_impossible"]:
                    self.normalize_evolutions(m, plan)
                pool = self.pool(m)
                starter_map = self.starters(m, plan, pool)
                wild = self.wild(m, plan, pool)
                closure = self.closure(m, wild | set(starter_map.values()))
                plan.obtainable = [str(k) for k in closure]
                self.trainers(m, plan, closure, starter_map)
                self.pokemon_sites(m, plan, m.gifts, "gifts", closure)
                self.pokemon_sites(m, plan, m.statics, "statics", closure)
                self.items(m, plan)
                self.check_constraints(m, plan, wild, starter_map, closure)
            except PlanError as error:
                last = error
                continue
            plan.features["attempt"] = attempt
            return m, plan
        raise PlanError(f"no valid plan after {MAX_ATTEMPTS} deterministic attempts: {last}")

    def diagnostic(self) -> tuple[GameModel, Plan]:
        if self.diagnostic_area is None:
            raise PlanError("the adapter did not resolve a diagnostic area")
        m = copy.deepcopy(self.base)
        plan = Plan()
        area_id, evidence = self.diagnostic_area
        area = next(a for a in m.encounters if a.id == area_id)
        for key, _level, _w in DIAGNOSTIC_FIXTURE:
            if key not in m.species:
                raise PlanError(f"diagnostic species {key} is not in this game")
        for season in area.seasons:
            table = next(t for t in season if t.kind == "grass")
            rates = tuple(s.rate for s in table.slots)
            assignment = assign_by_rates(rates, tuple(((k, lv), w) for k, lv, w in DIAGNOSTIC_FIXTURE))
            for slot, (key, level) in zip(table.slots, assignment):
                slot.species, slot.min_level, slot.max_level = key, level, level
        plan.diagnostic = {"area": area_id, "evidence": evidence, "seasons": len(area.seasons),
                           "fixture": [{"species": str(k), "name": m.sp(k).name, "level": lv, "weight": w}
                                       for k, lv, w in DIAGNOSTIC_FIXTURE]}
        for feature in ("wild", "trainers", "starters", "gifts", "statics", "field_items"):
            plan.features[feature] = "vanilla"
        plan.features["diagnostic_route19"] = "applied"
        return m, plan
