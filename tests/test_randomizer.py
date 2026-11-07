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
from b2er.gamedata import scripts  # noqa: E402
from b2er.nds.narc import Narc  # noqa: E402
from b2er.nds.rom import Rom  # noqa: E402
from b2er.randomizer import engine, rng, script_features, settings as st  # noqa: E402
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
            ({"doubles": {"all_trainers": True, "major_battles": False}}, "implies"),
        ]
        for raw, fragment in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(st.SettingsError) as ctx:
                    st.normalize(raw)
                self.assertIn(fragment, str(ctx.exception))

    def test_diagnostic_settings_valid(self) -> None:
        self.assertEqual(st.diagnostic_settings()["mode"], "diagnostic-route19")

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


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name) / "w2u"
        cls.facts = sw.make_checkout(cls.base)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def fresh(self) -> W2UData:
        target = Path(tempfile.mkdtemp(dir=self._tmp.name))
        root = target / "copy"
        shutil.copytree(self.base, root, ignore=shutil.ignore_patterns(".git"))
        return W2UData.load(root)

    def generate(self, seed: str = "seed", raw: dict | None = None):
        data = self.fresh()
        s = st.normalize(raw or {})
        plan = engine.Generator(data, s, bytes.fromhex(st.build_key(s, seed, {}))).run().to_json()
        return data, plan

    def tree_digest(self, data: W2UData) -> str:
        h = hashlib.sha256()
        for sub in ("data/encounters", "data/trainers", "data/pml"):
            for path in sorted((data.root / sub).rglob("*.toml")):
                h.update(path.relative_to(data.root).as_posix().encode() + path.read_bytes())
        return h.hexdigest()

    def test_deterministic_and_seed_sensitive(self) -> None:
        d1, p1 = self.generate("alpha")
        d2, p2 = self.generate("alpha")
        d3, p3 = self.generate("beta")
        self.assertEqual(p1, p2)
        self.assertEqual(self.tree_digest(d1), self.tree_digest(d2))
        self.assertNotEqual(p1["wild"], p3["wild"])
        self.assertNotEqual(self.tree_digest(d1), self.tree_digest(d3))

    def test_constraints_recorded_and_passing(self) -> None:
        _data, plan = self.generate()
        names = {c["name"] for c in plan["checks"]}
        for required in ("machine_compatibility_coverage", "trainer_parties_legal", "wild_species_obtainable",
                         "evolution_closure", "evolutions_feasible"):
            self.assertIn(required, names)
        self.assertTrue(all(c["status"] == "pass" for c in plan["checks"]))

    def test_wild_species_valid_usable_and_written(self) -> None:
        data, plan = self.generate()
        reloaded = W2UData.load(data.root)
        gen = engine.Generator(reloaded, st.normalize({}), b"k")
        for document in reloaded.encounters.values():
            for container in document.values():
                for slot in container["GRASS"]["SINGLES"].values():
                    species = reloaded.species_id(slot["Species"])
                    self.assertTrue(reloaded.model.encounter_encodable(species))
                    self.assertFalse(reloaded.model.is_egg_sentinel(species))
                    self.assertTrue(gen.usable(species, int(slot["Minimum Level"])))
                    self.assertEqual(slot["Form"], 0)
        self.assertEqual(len(plan["wild"]), len(reloaded.encounters))

    def test_starters(self) -> None:
        data, plan = self.generate()
        chosen = plan["starters"]["chosen"]
        self.assertEqual(len(set(chosen)), 3)
        gen = engine.Generator(data, st.normalize({}), b"k")
        for s in chosen:
            self.assertTrue(gen.usable(s, 5))
            self.assertTrue(gen.evolution_targets(s))
            self.assertNotIn(s, gen.pre_evolution)

    def test_rival_continuity(self) -> None:
        data, plan = self.generate()
        rival = data.trainers[2]["PARTY"][0]
        starter_line = engine.Generator(data, st.normalize({}), b"k").line(plan["starters"]["chosen"][0])
        self.assertIn(data.species_id(rival["SPECIES"]), starter_line)

    def test_major_battles_doubles(self) -> None:
        data, plan = self.generate()
        converted = {d["trainer"]: d for d in plan["doubles"] if d["converted"]}
        self.assertEqual(set(converted), {2, 3, 4, 5, 7})
        self.assertFalse(next(d for d in plan["doubles"] if d["trainer"] == 1)["converted"])  # first rival
        for tid in converted:
            self.assertEqual(data.trainers[tid]["BATTLE_TYPE"], 1)
            self.assertGreaterEqual(len(data.trainers[tid]["PARTY"]), 2)
            self.assertTrue(data.trainers[tid]["AI"] & 0x80)
        self.assertTrue(converted[4]["padded_party"])
        self.assertEqual(data.trainers[6]["BATTLE_TYPE"], 0)  # ordinary trainer untouched

    def test_trainer_moves_recomputed(self) -> None:
        data, _plan = self.generate()
        for member in data.trainers[6]["PARTY"]:
            species = data.species_id(member["SPECIES"])
            self.assertEqual(member["MOVES"], data.default_moves(species, member["LEVEL"]))

    def test_evolution_normalization(self) -> None:
        data, plan = self.generate()
        reasons = " ".join(r for c in plan["evolution_changes"] for r in c["reasons"])
        self.assertIn("ITEM_LINKING_CORD", reasons)
        self.assertIn("METHOD_TRADE is not feasible", reasons)
        self.assertIn("level up holding ITEM_METAL_COAT", reasons)
        reloaded = W2UData.load(data.root)
        for evos in reloaded.evolutions.values():
            for e in evos:
                self.assertNotIn(e.method, ("METHOD_TRADE", "METHOD_TRADE_ITEM", "METHOD_TRADE_MONS_SPECIFIC"))
                if e.method == "METHOD_ITEM_USE":
                    self.assertNotEqual(e.parameter, sw.ITEMS["ITEM_LINKING_CORD"])

    def test_vanilla_modes_leave_sources_unchanged(self) -> None:
        data = self.fresh()
        before = self.tree_digest(data)
        raw = {"wild": {"mode": "vanilla"}, "trainers": {"mode": "vanilla"}, "starters": {"mode": "vanilla"},
               "doubles": {"major_battles": False}}
        s = st.normalize(raw)
        engine.Generator(data, s, b"k").run()
        reloaded = W2UData.load(data.root)
        self.assertEqual(sorted(reloaded.trainers), sorted(data.trainers))
        # Only evolution normalization may change sources in vanilla modes.
        changed = self.tree_digest(reloaded)
        self.assertNotEqual(before, changed)  # evolutions normalized
        for sub in ("data/encounters", "data/trainers"):
            for path in sorted((data.root / sub).glob("*.toml")):
                original = (self.base / sub / path.name).read_bytes()
                self.assertEqual(path.read_bytes(), original, path.name)

    def test_route19_resolution(self) -> None:
        data = self.fresh()
        self.assertEqual(engine.resolve_route19(data), 2)
        duplicate = copy.deepcopy(data.encounters[2])
        data.encounters[9] = duplicate
        with self.assertRaises(engine.GenerationError):
            engine.resolve_route19(data)
        del data.encounters[9]
        del data.encounters[2]
        with self.assertRaises(engine.GenerationError):
            engine.resolve_route19(data)

    def test_diagnostic_fixture_applied_only(self) -> None:
        data = self.fresh()
        s = st.diagnostic_settings()
        plan = engine.Generator(data, s, b"k").run().to_json()
        self.assertEqual(plan["route19"]["record"], 2)
        for container in data.encounters[2].values():
            labels = [slot["Species"] for slot in container["GRASS"]["SINGLES"].values()]
            self.assertEqual(sorted(set(labels)), ["SPECIES_CARBINK", "SPECIES_CHESPIN", "SPECIES_PATRAT"])
        self.assertEqual(plan["evolution_changes"], [])
        for index in (0, 1, 3, 4):
            self.assertEqual((data.root / f"data/encounters/{index}.toml").read_bytes(),
                             (self.base / f"data/encounters/{index}.toml").read_bytes())

    def test_impossible_pool_fails_before_rom(self) -> None:
        data = self.fresh()
        for p in data.personal.values():
            p.stats = (0, 0, 0, 0, 0, 0)
        with self.assertRaises(engine.GenerationError):
            engine.Generator(data, st.normalize({}), b"k").run()


class ScriptFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        white, _black = sw.make_roms(Path(self._tmp.name))
        self.rom = Rom(white.read_bytes())
        import zipfile, io
        with zipfile.ZipFile(io.BytesIO(sw.synthetic_jar())) as outer:
            inner = zipfile.ZipFile(io.BytesIO(outer.read("ctrmap/resources/scripting/cm_ide/sdk/EV_GEN_V/SDK5-B2W2-Generated.lib")))
            self.table = scripts.load_opcode_table(inner.read("yml/Base.yml").decode())
        self.context = {"vanilla_starters": [495, 498, 501], "starters": [10, 20, 30], "obtainable": list(range(10, 200)),
                        "pick_similar": lambda stream, original, pool, level: stream.choice(pool), "is_ordinary": lambda s: 0 < s < 1024}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def items_of(self) -> list[int]:
        narc = Narc.parse(self.rom.read_path("a/0/5/6"))
        out = []
        for i, m in enumerate(narc.members):
            out += [s.value for s in scripts.constant_sites(i, m, self.table, {"ItemAdd": "itemId"})]
        return out

    def test_starters_items_and_preservation(self) -> None:
        before_items = self.items_of()
        size = len(self.rom.read_path("a/0/5/6"))
        result = script_features.apply(self.rom, self.table, self.context, st.normalize({}), b"key")
        self.assertTrue(result.features["starter_scripts"].startswith("applied (3"))
        narc = Narc.parse(self.rom.read_path("a/0/5/6"))
        starters = [s.value for s in scripts.constant_sites(10, narc.members[10], self.table, {"PokePartyAdd": "species"})]
        self.assertEqual(starters, [10, 20, 30])
        after_items = self.items_of()
        self.assertEqual(sorted(before_items), sorted(after_items))  # multiset preserved
        self.assertEqual(after_items.count(420), 1)
        self.assertEqual(after_items.count(450), 1)
        member11 = [s.value for s in scripts.constant_sites(11, narc.members[11], self.table, {"ItemAdd": "itemId"})]
        self.assertEqual(member11[5:], [420, 450])  # HM and key item stay in place
        self.assertEqual(len(self.rom.read_path("a/0/5/6")), size)
        self.assertEqual(script_features.verify(self.rom, result.archive, result.patches), [])

    def test_gifts_and_statics_when_enabled(self) -> None:
        s = st.normalize({"gifts": {"mode": "random"}, "statics": {"mode": "random"}})
        result = script_features.apply(self.rom, self.table, self.context, s, b"key")
        features = {p["feature"] for p in result.patches}
        self.assertTrue({"gifts", "statics"} <= features)

    def test_vanilla_and_missing_sites(self) -> None:
        s = st.normalize({"starters": {"mode": "vanilla"}, "items": {"mode": "vanilla"}})
        before = bytes(self.rom.data)
        result = script_features.apply(self.rom, self.table, self.context, s, b"key")
        self.assertEqual(result.patches, [])
        self.assertEqual(bytes(self.rom.data), before)
        empty_table = {k: v for k, v in self.table.items() if v.name != "PokePartyAdd"}
        result = script_features.apply(self.rom, empty_table, self.context, st.normalize({}), b"key")
        self.assertTrue(result.features["starter_scripts"].startswith("skipped"))

    def test_item_classes(self) -> None:
        self.assertEqual(script_features.item_class(17), "ordinary")
        self.assertEqual(script_features.item_class(328), "tm")
        for fixed in (0, 420, 425, 450, 900):
            self.assertIsNone(script_features.item_class(fixed))


if __name__ == "__main__":
    unittest.main()
