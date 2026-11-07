"""Synthetic tests for settings, RNG, TOML writing, the species model, the generation engine, and script features."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_w2u as sw  # noqa: E402
from b2er import tomlw  # noqa: E402
from b2er.adapters.gen5 import sites  # noqa: E402
from b2er.adapters.gen5.w2u_model import to_model, write_model  # noqa: E402
from b2er.capabilities import Capability, Level, Support, check, unsupported  # noqa: E402
from b2er.gamedata import scripts  # noqa: E402
from b2er.model import BattleFormat, EvolutionMethod, ItemLocation, PokemonSite, Reserved, SpeciesCodec, SpeciesCodecError, SpeciesKey  # noqa: E402
from b2er.nds.narc import Narc  # noqa: E402
from b2er.nds.rom import Rom  # noqa: E402
from b2er.randomizer import policy, rng, settings as st  # noqa: E402
from b2er.species import SpeciesModel, SpeciesModelError  # noqa: E402
from b2er.w2u_data import W2UData  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_defaults_and_canonical_form(self) -> None:
        a = st.normalize({})
        b = st.normalize(json.loads(json.dumps(a)))
        self.assertEqual(st.canonical_json(a), st.canonical_json(b))
        self.assertEqual(a["doubles"]["major_battles"], True)

    def test_rejections(self) -> None:
        cases = [
            ({"trainers": {"keep_type_themes": True}}, "deferred stretch goal"),
            ({"challenge_mode": True}, "deferred stretch goal"),
            ({"wild": {"mode": "chaos"}}, "wild.mode"),
            ({"bogus": 1}, "unknown option"),
            ({"species": {"include_legendaries": 1}}, "include_legendaries"),
            ({"mode": "diagnostic-route19"}, "requires wild.mode"),
            ({"unsupported_feature_policy": "skip"}, "always refused"),
            ({"species": {"national_max": 1100}}, "national_max"),
            ({"doubles": {"all_trainers": True, "major_battles": False}}, "implies"),
        ]
        for raw, fragment in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(st.SettingsError) as ctx:
                    st.normalize(raw)
                self.assertIn(fragment, str(ctx.exception))

    def test_diagnostic_settings_valid(self) -> None:
        self.assertEqual(st.diagnostic_settings()["mode"], "diagnostic-route19")

    def test_requested_capabilities(self) -> None:
        req = st.requested_capabilities(st.normalize({}))
        self.assertEqual(req[Capability.SPECIES_EXPANSION], {"national_dex_max": 1025})
        self.assertIn(Capability.MAJOR_BATTLE_DOUBLES, req)
        self.assertNotIn(Capability.HIDDEN_ITEMS, req)
        self.assertIn(Capability.HIDDEN_ITEMS, st.requested_capabilities(st.normalize({"items": {"hidden": "shuffle"}})))
        self.assertIn(Capability.DIAGNOSTIC_FIXTURE, st.requested_capabilities(st.diagnostic_settings()))

    def test_build_key(self) -> None:
        s = st.normalize({})
        k1 = st.build_key(s, "seed-a", {"x": 1})
        self.assertEqual(k1, st.build_key(copy.deepcopy(s), "seed-a", {"x": 1}))
        self.assertNotEqual(k1, st.build_key(s, "seed-b", {"x": 1}))
        self.assertNotEqual(k1, st.build_key(s, "seed-a", {"x": 2}))
        with self.assertRaises(st.SettingsError):
            st.build_key(s, "", {})


class RngTests(unittest.TestCase):
    def test_deterministic_and_independent(self) -> None:
        a, b = rng.Stream(b"k", "wild"), rng.Stream(b"k", "wild")
        self.assertEqual([a.below(1000) for _ in range(50)], [b.below(1000) for _ in range(50)])
        c = rng.Stream(b"k", "trainers")
        self.assertNotEqual([rng.Stream(b"k", "wild").below(1000) for _ in range(5)], [c.below(1000) for _ in range(5)])

    def test_uniformity_and_shuffle(self) -> None:
        s = rng.Stream(b"seed", "test")
        counts = [0] * 6
        for _ in range(6000):
            counts[s.below(6)] += 1
        self.assertTrue(all(800 < c < 1200 for c in counts), counts)
        items = list(range(20))
        self.assertEqual(sorted(s.shuffle(items)), items)
        with self.assertRaises(ValueError):
            s.below(0)


class TomlWriterTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        doc = {"A": 1, "B": "x \"q\" \\", "C": [1, -2], "D": ["ITEM_POKé_BALL"],
               "T": {"Slot 0": {"Minimum Level": 2, "Species": "SPECIES_PATRAT"}, "inner": {"deep": True}},
               "PARTY": [{"SPECIES": "S1", "MOVES": ["M1", "M2"]}, {"SPECIES": "S2"}]}
        self.assertEqual(tomllib.loads(tomlw.dumps(doc)), doc)


class SpeciesModelTests(unittest.TestCase):
    def defines(self, max_ordinary=1023, egg=1024):
        d = {"SPECIES_NONE": 0, "SPECIES_EGG": egg, "SPECIES_BAD_EGG": egg + 1}
        d.update({f"SPECIES_{i}": i for i in range(1, max_ordinary + 1)})
        d.update({f"SPECIES_{i}": i for i in range(egg + 2, egg + 6)})
        return d

    def test_current_layout(self) -> None:
        model = SpeciesModel.from_enum(self.defines())
        self.assertTrue(model.is_ordinary(1023))
        self.assertFalse(model.is_ordinary(1024))
        self.assertTrue(model.is_egg_sentinel(1024) and model.is_egg_sentinel(1025))
        self.assertTrue(model.is_form_record(1026))
        self.assertEqual(model.missing_required, [1024, 1025])
        self.assertFalse(model.describe()["requirement_met"])

    def test_relocated_layout_meets_requirement(self) -> None:
        model = SpeciesModel.from_enum(self.defines(max_ordinary=1025, egg=1026))
        self.assertEqual(model.missing_required, [])
        self.assertTrue(model.is_ordinary(1024) and model.is_ordinary(1025))
        self.assertTrue(model.encounter_encodable(1025))

    def test_invalid_layouts(self) -> None:
        bad_gap = self.defines()
        del bad_gap["SPECIES_500"]
        broken = dict(self.defines())
        broken["SPECIES_BAD_EGG"] = 1030
        for d in (bad_gap, broken, {"SPECIES_NONE": 0, "SPECIES_1": 1}):
            with self.assertRaises(SpeciesModelError):
                SpeciesModel.from_enum(d)


class CodecAndCapabilityTests(unittest.TestCase):
    def codec(self, national_max=1023, egg=1024):
        return SpeciesCodec("test", 1, national_max, {Reserved.NONE: 0, Reserved.EGG: egg, Reserved.BAD_EGG: egg + 1},
                            {egg + 2: SpeciesKey(25, 1)})

    def test_codec_round_trip_and_bounds(self) -> None:
        c = self.codec()
        self.assertEqual(c.encode(SpeciesKey(1023)), 1023)
        self.assertEqual(c.decode(1024), Reserved.EGG)
        self.assertEqual(c.encode(Reserved.BAD_EGG), 1025)
        self.assertEqual(c.decode(1026), SpeciesKey(25, 1))
        self.assertEqual(c.encode(SpeciesKey(25, 1)), 1026)
        for bad in (SpeciesKey(1024), SpeciesKey(1025), SpeciesKey(25, 2)):
            with self.subTest(key=bad), self.assertRaises(SpeciesCodecError):
                c.encode(bad)
        for internal in (5000, 1 << 16):
            with self.assertRaises(SpeciesCodecError):
                c.decode(internal)

    def test_codec_rejects_collisions(self) -> None:
        with self.assertRaises(SpeciesCodecError):
            self.codec(national_max=1025, egg=1024)  # Egg would collide with National Dex #1024
        relocated = self.codec(national_max=1025, egg=1026)
        self.assertEqual(relocated.encode(SpeciesKey(1025)), 1025)
        self.assertEqual(relocated.decode(1026), Reserved.EGG)

    def test_species_keys(self) -> None:
        with self.assertRaises(ValueError):
            SpeciesKey(0)
        self.assertEqual(str(SpeciesKey(650, 1)), "#0650-f1")

    def test_capability_check(self) -> None:
        declared = {Capability.WILD: Support(Level.SUPPORTED, "e"),
                    Capability.SPECIES_EXPANSION: Support(Level.EXPERIMENTAL, "e", {"national_dex_max": 1023}),
                    Capability.SHOP_ITEMS: unsupported("not decoded")}
        self.assertEqual(check(declared, {Capability.WILD: None, Capability.SPECIES_EXPANSION: {"national_dex_max": 1023}}), [])
        problems = check(declared, {Capability.SPECIES_EXPANSION: {"national_dex_max": 1025}, Capability.SHOP_ITEMS: None,
                                    Capability.TRAINERS: None})
        self.assertEqual(len(problems), 3)
        self.assertTrue(any("1025" in p for p in problems))

    def test_assign_by_rates(self) -> None:
        rates = (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1)
        out = policy.assign_by_rates(rates, (("P", 35), ("C", 35), ("H", 30)))
        totals = {}
        for target, rate in zip(out, rates):
            totals[target] = totals.get(target, 0) + rate
        self.assertEqual(totals, {"P": 35, "C": 35, "H": 30})
        with self.assertRaises(policy.PlanError):
            policy.assign_by_rates(rates, (("P", 3), ("C", 97)))  # 3 needs three 1% slots; only two exist
        with self.assertRaises(policy.PlanError):
            policy.assign_by_rates((60, 30, 5, 4, 1), (("a", 50), ("b", 50)))


class PolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name) / "w2u"
        sw.make_checkout(cls.base)
        cls.model = to_model(W2UData.load(cls.base), "test")
        cls.model.starters = [PokemonSite(f"s{n}", "starter", SpeciesKey(n), 5) for n in (495, 498, 501)]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def plan(self, seed="seed", raw=None, national_max=1023):
        raw = dict(raw or {})
        raw.setdefault("species", {})["national_max"] = national_max
        s = st.normalize(raw)
        return policy.Planner(self.model, s, bytes.fromhex(st.seed_key(seed, {}))).run()

    def test_policy_does_not_mutate_input_model(self) -> None:
        before = copy.deepcopy(self.model)
        self.plan()
        self.assertEqual(self.model, before)

    def test_deterministic_and_seed_sensitive(self) -> None:
        m1, p1 = self.plan("a")
        m2, p2 = self.plan("a")
        _m3, p3 = self.plan("b")
        self.assertEqual(p1.to_json(), p2.to_json())
        self.assertEqual(m1, m2)
        self.assertNotEqual(p1.wild, p3.wild)

    def test_independent_streams(self) -> None:
        _a, base = self.plan("a")
        _b, no_items = self.plan("a", {"items": {"field": "vanilla"}})
        self.assertEqual(base.wild, no_items.wild)  # disabling items does not reshuffle wild encounters
        self.assertEqual(base.trainers, no_items.trainers)

    def test_constraints_and_selection_rules(self) -> None:
        m, plan = self.plan()
        self.assertTrue(all(c["status"] == "pass" for c in plan.checks))
        names = {c["name"] for c in plan.checks}
        for required in ("machine_compatibility_coverage", "trainer_parties_legal", "trainer_species_obtainable",
                         "evolution_closure", "evolutions_achievable"):
            self.assertIn(required, names)
        planner = policy.Planner(m, st.normalize({}), b"k")
        for area in m.encounters:
            for slot in area.all_slots():
                if slot.species:
                    self.assertLessEqual(slot.species.national, 1023)
                    self.assertTrue(planner.usable(m, slot.species, slot.min_level))
                    self.assertFalse(m.species[slot.species].legendary)

    def test_starters_and_rival_continuity(self) -> None:
        m, plan = self.plan()
        chosen = [s.species for s in m.starters]
        self.assertEqual(len(set(chosen)), 3)
        planner = policy.Planner(m, st.normalize({}), b"k")
        for k in chosen:
            self.assertTrue(planner.usable(m, k, 5) and m.species[k].evolutions)
        rival = next(t for t in m.trainers if t.id == 2)
        lines = [planner.line(m, k) for k in chosen]
        self.assertTrue(any(rival.party[0].species in line for line in lines))

    def test_doubles(self) -> None:
        m, plan = self.plan()
        converted = {d["trainer"]: d for d in plan.doubles if d["converted"]}
        self.assertEqual(set(converted), {2, 3, 4, 5, 7})
        self.assertFalse(next(d for d in plan.doubles if d["trainer"] == 1)["converted"])
        for t in m.trainers:
            if t.id in converted:
                self.assertIs(t.battle_format, BattleFormat.DOUBLE)
                self.assertGreaterEqual(len(t.party), 2)
        self.assertTrue(converted[4]["padded_party"])

    def test_evolutions_normalized(self) -> None:
        m, plan = self.plan()
        reasons = " ".join(r for c in plan.evolution_changes for r in c["reasons"])
        self.assertIn("ITEM_LINKING_CORD", reasons)
        self.assertIn("trade is not feasible", reasons)
        self.assertIn("level up holding ITEM_METAL_COAT", reasons)
        for s in m.species.values():
            for e in s.evolutions:
                self.assertIn(e.method, m.feasible_evolution_methods)

    def test_items_shuffle_within_class(self) -> None:
        self.model.item_locations = [ItemLocation(f"i{n}", "scripted", item, cls) for n, (item, cls) in
                                     enumerate([("ITEM_POTION", "ordinary"), ("ITEM_RARE_CANDY", "ordinary"), ("ITEM_TM01", "tm"),
                                                ("ITEM_TM02", "tm"), ("ITEM_HM01", "hm"), ("ITEM_BICYCLE", "key")] * 3)]
        try:
            m, plan = self.plan()
        finally:
            self.model.item_locations = []
        self.assertEqual(sorted(l.item for l in m.item_locations if l.item_class == "tm"), sorted(["ITEM_TM01", "ITEM_TM02"] * 3))
        for loc in m.item_locations:
            if loc.item_class in ("hm", "key"):
                self.assertEqual(loc.item, {"hm": "ITEM_HM01", "key": "ITEM_BICYCLE"}[loc.item_class])

    def test_diagnostic(self) -> None:
        s = st.diagnostic_settings(1023)
        m, plan = policy.Planner(self.model, s, b"k", (2, {"rule": "test"})).run()
        area = next(a for a in m.encounters if a.id == 2)
        for season in area.seasons:
            grass = next(t for t in season if t.kind == "grass")
            totals = {}
            for slot in grass.slots:
                totals[slot.species.national] = totals.get(slot.species.national, 0) + slot.rate
                self.assertEqual(slot.min_level, {504: 5, 703: 6, 650: 7}[slot.species.national])
            self.assertEqual(totals, {504: 35, 703: 35, 650: 30})
        others = [a for a in m.encounters if a.id != 2]
        self.assertEqual(others, [a for a in self.model.encounters if a.id != 2])

    def test_write_back_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "copy"
            shutil.copytree(self.base, root, ignore=shutil.ignore_patterns(".git"))
            data = W2UData.load(root)
            original = to_model(data, "test")
            m, plan = self.plan()
            m.starters = original.starters = []
            summary = write_model(data, original, m)
            self.assertGreater(summary["encounters"], 0)
            reread = to_model(W2UData.load(root), "test")
            self.assertEqual([a.seasons for a in reread.encounters], [a.seasons for a in m.encounters])
            self.assertEqual([(t.battle_format, [(p.species, p.level) for p in t.party]) for t in reread.trainers],
                             [(t.battle_format, [(p.species, p.level) for p in t.party]) for t in m.trainers])
            converted = W2UData.load(root).trainers[3]
            self.assertTrue(converted["AI"] & 0x80)
            for key, sp in reread.species.items():
                self.assertEqual([(e.method, e.target, e.level, e.item) for e in sp.evolutions],
                                 [(e.method, e.target, e.level, e.item) for e in m.species[key].evolutions])

    def test_alternate_form_slots_use_base_species_data(self) -> None:
        # Regression: real White 2 data has form-1 encounter/trainer slots (e.g. Basculin) without own records.
        forms = copy.deepcopy(self.model)
        forms.encounters[0].seasons[0][0].slots[0].species = SpeciesKey(40, 1)
        forms.trainers[0].party[0].species = SpeciesKey(40, 1)
        self.assertIs(forms.sp(SpeciesKey(40, 1)), forms.species[SpeciesKey(40)])
        s = st.normalize({"species": {"national_max": 1023}})
        planned, plan = policy.Planner(forms, s, b"k").run()
        self.assertTrue(all(c["status"] == "pass" for c in plan.checks))
        with self.assertRaises(KeyError):
            forms.sp(SpeciesKey(1500))

    def test_impossible_pool_refused(self) -> None:
        broken = copy.deepcopy(self.model)
        for sp in broken.species.values():
            sp.learnset = []
        with self.assertRaises(policy.PlanError):
            policy.Planner(broken, st.normalize({"species": {"national_max": 1023}}), b"k").run()


class SiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        white, _black = sw.make_roms(Path(self._tmp.name))
        self.rom = Rom(white.read_bytes())
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(sw.synthetic_jar())) as outer:
            inner = zipfile.ZipFile(io.BytesIO(outer.read("ctrmap/resources/scripting/cm_ide/sdk/EV_GEN_V/SDK5-B2W2-Generated.lib")))
            self.table = scripts.load_opcode_table(inner.read("yml/Base.yml").decode())
        self.codec = SpeciesCodec("t", 1, 1023, {Reserved.NONE: 0, Reserved.EGG: 1024, Reserved.BAD_EGG: 1025})
        self.items = {v: k for k, v in sw.ITEMS.items()}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_discovery(self) -> None:
        found = sites.discover(self.rom, self.table, self.codec, self.items)
        self.assertEqual(found["archive"], "a/0/5/6")
        self.assertEqual(found["starter_species_found"], [495, 498, 501])
        self.assertEqual([g.species.national for g in found["gifts"]], [40])
        self.assertEqual([s.species.national for s in found["statics"]], [380])
        classes = sorted({i.item_class for i in found["items"]})
        self.assertEqual(classes, ["hm", "key", "ordinary", "tm"])

    def test_guarded_patching(self) -> None:
        found = sites.discover(self.rom, self.table, self.codec, self.items)
        site = found["starters"][0]
        patch = {**{k: site.ext[k] for k in ("archive", "member", "command_offset", "value_offset", "command")},
                 "before": 495, "after": 10}
        size = len(self.rom.read_path("a/0/5/6"))
        sites.apply_patches(self.rom, self.table, [patch])
        self.assertEqual(len(self.rom.read_path("a/0/5/6")), size)
        self.assertEqual(sites.verify_patches(self.rom, [patch]), [])
        with self.assertRaises(Exception):
            sites.apply_patches(self.rom, self.table, [patch])  # original value no longer present

    def test_item_classes(self) -> None:
        self.assertEqual([sites.item_class(i) for i in (17, 328, 420, 450, 900)], ["ordinary", "tm", "hm", "key", "key"])


if __name__ == "__main__":
    unittest.main()
