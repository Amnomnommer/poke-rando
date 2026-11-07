"""Generated-data tests for the shared W2U trainer-record codec and semantic trainer verification.

Real failure: `int(m["ABILITY"])` raised on W2U's `ABILITY = "BATTLE_ABILITY_PRIMARY"` during verification.
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import synthetic_w2u as sw  # noqa: E402
from b2er import pipeline  # noqa: E402
from b2er.assembly import BuildError, builders  # noqa: E402
from b2er.gamedata import trainer_codec as tc, trainers as trn  # noqa: E402
from b2er.w2u_data import DataError, W2UData  # noqa: E402

ENUMS = {"items": {"ITEM_NONE": 0, "ITEM_POTION": 17}, "moves": {"MOVE_NONE": 0, "MOVE_TACKLE": 33},
         "species": {"SPECIES_NONE": 0, "SPECIES_SNIVY": 495}}
LABELS = tc.Labels(ENUMS)


def member(**over) -> dict:
    m = {"SPECIES": "SPECIES_SNIVY", "LEVEL": 5, "FORM": 0, "GENDER": "GENDER_RANDOM", "ABILITY": "BATTLE_ABILITY_PRIMARY",
         "DIFFICULTY_VALUE": 50}
    m.update(over)
    return m


def doc(*party, **over) -> dict:
    d = {"CLASS": 3, "BATTLE_TYPE": 0, "ITEMS": ["ITEM_POTION", "ITEM_NONE", "ITEM_NONE", "ITEM_NONE"], "AI": 7, "CAN_HEAL": 0,
         "REWARD_MONEY": 20, "REWARD_ITEM": "ITEM_NONE", "PARTY": list(party) or [member()]}
    d.update(over)
    return d


class SymbolTests(unittest.TestCase):
    def test_every_symbolic_ability_and_gender(self) -> None:
        for name, value in tc.ABILITY.items():
            for gname, gvalue in tc.GENDER.items():
                _, (m,) = tc.encode(1, doc(member(ABILITY=name, GENDER=gname)), LABELS)
                self.assertEqual(m.ability_gender, (value << 4) | gvalue)
                self.assertEqual(tc.unpack_ability_gender(m.ability_gender), (value, gvalue))
        self.assertEqual(tc.ABILITY, {"BATTLE_ABILITY_RANDOM": 0, "BATTLE_ABILITY_PRIMARY": 1,
                                      "BATTLE_ABILITY_SECONDARY": 2, "BATTLE_ABILITY_HIDDEN": 3})
        self.assertEqual(tc.GENDER, {"GENDER_RANDOM": 0, "GENDER_MALE": 1, "GENDER_FEMALE": 2})

    def test_numeric_equivalents_encode_identically(self) -> None:
        for name, value in tc.ABILITY.items():
            for gname, gvalue in tc.GENDER.items():
                self.assertEqual(tc.encode_bytes(1, doc(member(ABILITY=name, GENDER=gname)), LABELS),
                                 tc.encode_bytes(1, doc(member(ABILITY=value, GENDER=gvalue)), LABELS))
        self.assertEqual(tc.encode_bytes(1, doc(member(SPECIES=495, MOVES=[33, 0, 0, 0], HELD_ITEM=17)), LABELS),
                         tc.encode_bytes(1, doc(member(MOVES=["MOVE_TACKLE", "MOVE_NONE", "MOVE_NONE", "MOVE_NONE"],
                                                       HELD_ITEM="ITEM_POTION")), LABELS))

    def test_boundaries(self) -> None:
        for field, ok, bad in (("DIFFICULTY_VALUE", (0, 255), (-1, 256)), ("LEVEL", (1, 100), (0, 101)),
                               ("FORM", (0, 0xFFFF), (-1, 0x10000)), ("ABILITY", (0, 3), (-1, 4)), ("GENDER", (0, 2), (-1, 3))):
            for v in ok:
                tc.encode(1, doc(member(**{field: v})), LABELS)
            for v in bad:
                with self.assertRaises(tc.TrainerFieldError) as ctx:
                    tc.encode(9, doc(member(), member(**{field: v})), LABELS)
                self.assertIn(f"trainer 9 party slot 1 field {field}: value {v}", str(ctx.exception))
        for field, hi in (("CLASS", 255), ("BATTLE_TYPE", 3), ("AI", 0xFFFFFFFF), ("CAN_HEAL", 255), ("REWARD_MONEY", 255)):
            tc.encode(1, doc(**{field: hi}), LABELS)
            with self.assertRaises(tc.TrainerFieldError) as ctx:
                tc.encode(4, doc(**{field: hi + 1}), LABELS)
            self.assertIn(f"trainer 4 field {field}", str(ctx.exception))
        tc.encode(1, doc(*[member()] * 6), LABELS)
        with self.assertRaises(tc.TrainerFieldError):
            tc.encode(1, doc(*[member()] * 7), LABELS)

    def test_unknown_and_wrong_table_symbols(self) -> None:
        cases = [("ABILITY", "BATTLE_ABILITY_QUATERNARY"), ("ABILITY", "GENDER_MALE"), ("GENDER", "GENDER_UNKNOWN"),
                 ("GENDER", "BATTLE_ABILITY_HIDDEN"), ("SPECIES", "SPECIES_MISSINGNO"), ("HELD_ITEM", "ITEM_BOGUS"),
                 ("DIFFICULTY_VALUE", "DIFFICULTY_MAX"), ("LEVEL", "5"), ("FORM", "FORM_ALOLA"), ("ABILITY", True)]
        for field, value in cases:
            with self.assertRaises(tc.TrainerFieldError, msg=f"{field}={value!r}") as ctx:
                tc.encode(12, doc(member(), member(), member(**{field: value})), LABELS)
            message = str(ctx.exception)
            self.assertIn(f"trainer 12 party slot 2 field {field}", message)
            self.assertIn(repr(value) if not isinstance(value, bool) else "True", message)
        with self.assertRaises(tc.TrainerFieldError) as ctx:
            tc.encode(3, doc(member(MOVES=["MOVE_TACKLE", "MOVE_FAKE", "MOVE_NONE", "MOVE_NONE"])), LABELS)
        self.assertIn("trainer 3 party slot 0 field MOVES[1]: unknown symbol 'MOVE_FAKE'", str(ctx.exception))
        with self.assertRaises(tc.TrainerFieldError) as ctx:
            tc.encode(3, doc(AI="AI_SMART"), LABELS)
        self.assertIn("trainer 3 field AI", str(ctx.exception))
        with self.assertRaises(tc.TrainerFieldError) as ctx:
            tc.encode(3, doc(member(ABILITY=None)), LABELS)
        self.assertIn("trainer 3 party slot 0 field ABILITY", str(ctx.exception))
        bad = member()
        del bad["GENDER"]
        with self.assertRaises(tc.TrainerFieldError) as ctx:
            tc.encode(3, doc(bad), LABELS)
        self.assertIn("trainer 3 party slot 0 field GENDER: missing", str(ctx.exception))

    def test_invalid_packed_combinations(self) -> None:
        for byte in (0x40, 0x03, 0xF0, 0x0F, 0x43):
            with self.assertRaises(tc.TrainerFieldError) as ctx:
                tc.unpack_ability_gender(byte, "trainer 5 party slot 1 field ABILITY/GENDER")
            self.assertIn(f"trainer 5 party slot 1 field ABILITY/GENDER: packed byte 0x{byte:02x} is invalid", str(ctx.exception))
        trdata, trpoke = tc.encode_bytes(5, doc(member(), member()), LABELS)
        corrupt = bytearray(trpoke)
        corrupt[8 + 1] = 0x43
        with self.assertRaises(tc.TrainerFieldError) as ctx:
            tc.decode(5, trdata, bytes(corrupt))
        self.assertIn("trainer 5 party slot 1", str(ctx.exception))

    def test_round_trip_and_determinism(self) -> None:
        d = doc(member(ABILITY="BATTLE_ABILITY_HIDDEN", GENDER="GENDER_FEMALE", HELD_ITEM="ITEM_POTION",
                       MOVES=["MOVE_TACKLE", "MOVE_NONE", "MOVE_NONE", "MOVE_NONE"]),
                member(ABILITY=2, GENDER=1, LEVEL=100, FORM=2, DIFFICULTY_VALUE=255), BATTLE_TYPE=1)
        before = copy.deepcopy(d)
        records = tc.encode(7, d, LABELS)
        trdata, trpoke = tc.encode_bytes(7, d, LABELS)
        self.assertEqual(tc.decode(7, trdata, trpoke), records)
        self.assertEqual(tc.compare(7, tc.decode(7, trdata, trpoke), records), [])
        self.assertEqual([tc.encode_bytes(7, d, LABELS) for _ in range(3)], [(trdata, trpoke)] * 3)
        self.assertEqual(d, before, "encoding must not mutate the source document")
        _, members = records
        self.assertEqual([tc.unpack_ability_gender(m.ability_gender) for m in members], [(3, 2), (2, 1)])

    def test_compare_reports_every_field_by_trainer_and_slot(self) -> None:
        plan = tc.encode(8, doc(member(), member(ABILITY="BATTLE_ABILITY_SECONDARY")), LABELS)
        rom = tc.encode(8, doc(member(), member(ABILITY="BATTLE_ABILITY_HIDDEN", LEVEL=6), AI=8), LABELS)
        rom_copy, plan_copy = copy.deepcopy(rom), copy.deepcopy(plan)
        problems = tc.compare(8, rom, plan)
        self.assertIn("trainer 8 field ai: ROM 8 != plan 7", problems)
        self.assertIn("trainer 8 party slot 1 field ability_gender: ROM 48 != plan 32", problems)
        self.assertIn("trainer 8 party slot 1 field level: ROM 6 != plan 5", problems)
        self.assertEqual((rom, plan), (rom_copy, plan_copy))

    def test_build_error_names_trainer_and_slot(self) -> None:
        data = mock.Mock(enums=ENUMS, species_id=lambda label: ENUMS["species"][label])
        with self.assertRaises(BuildError) as ctx:
            builders.encode_trainer_document(data, doc(member(), member(GENDER="GENDER_OTHER")), 42)
        self.assertIn("trainer 42 party slot 1 field GENDER: unknown symbol 'GENDER_OTHER'", str(ctx.exception))
        d, p = builders.encode_trainer_document(data, doc(member(ABILITY="BATTLE_ABILITY_PRIMARY")), 1)
        self.assertEqual(trn.decode_trpoke(p, trn.decode_trdata(d).format, 1)[0].ability_gender, 0x10)


class CheckoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.checkout = cls.tmp / "W2U"
        cls.facts = sw.make_checkout(cls.checkout)
        cls.white, cls.black = sw.make_roms(cls.tmp / "roms")
        cls.pins = sw.make_pins(cls.tmp / "pins", cls.facts, cls.white, cls.black)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_w2u_enum_tables_must_match_codec(self) -> None:
        data = W2UData.load(self.checkout)
        self.assertEqual(data.enums["btl_abil"], tc.ABILITY)
        symbolic = data.trainers[6]["PARTY"]
        self.assertEqual([m["ABILITY"] for m in symbolic], ["BATTLE_ABILITY_PRIMARY", "BATTLE_ABILITY_HIDDEN"])
        before = copy.deepcopy(data.trainers)
        for i in data.trainers:
            builders.encode_trainer_document(data, data.trainers[i], i)
        self.assertEqual(data.trainers, before)
        self.assertEqual(tc.check_w2u_enums({**data.enums, "btl_abil": {**tc.ABILITY, "BATTLE_ABILITY_HIDDEN": 4}}),
                         ["tools/mkdata/enum/btl_abil.toml differs from the codec mapping"])
        with mock.patch.object(tc, "ABILITY", {**tc.ABILITY, "BATTLE_ABILITY_EXTRA": 4}), \
                mock.patch.dict(tc.W2U_ENUMS, {"btl_abil": ("tools/mkdata/enum/btl_abil.toml", {**tc.ABILITY, "X": 4})}):
            with self.assertRaises(DataError):
                W2UData.load(self.checkout)

    def request(self, name: str, **options) -> pipeline.BuildRequest:
        return pipeline.BuildRequest(rom=self.white, seed="trainer-codec", auxiliary={"w2u": self.checkout},
                                     output_dir=self.tmp / f"out-{name}", workroot_base=self.tmp / f"work-{name}", pins_dir=self.pins,
                                     species_max=1023, options={"builder": "mock", "patch_pins": sw.patch_pins(self.facts), **options})

    def test_semantic_verification_passes_with_symbolic_fields(self) -> None:
        req = self.request("ok")
        result = pipeline.run(req)
        checks = {c["name"]: c for c in result["verification"]["checks"]}
        self.assertEqual(checks["trainers_match_plan"]["status"], "pass")
        self.assertIn("match field-for-field", checks["trainers_match_plan"]["detail"])

    def test_trainer_mismatch_withholds_outputs(self) -> None:
        real = tc.compare
        with mock.patch.object(tc, "compare", lambda i, r, p: real(i, r, p) or (["trainer 6 party slot 0 field level: ROM 1 != plan 2"] if i == 6 else [])):
            req = self.request("mismatch")
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        self.assertIn("trainers_match_plan", str(ctx.exception))
        self.assertIn("trainer 6 party slot 0", str(ctx.exception))
        self.assertFalse(req.output_dir.exists())

    def test_unexpected_verifier_exception_is_clean_failure(self) -> None:
        def boom(*args, **kwargs):
            raise ValueError("invalid literal for int() with base 10: 'BATTLE_ABILITY_PRIMARY'")
        req = self.request("crash")
        from b2er.adapters.gen5 import white2
        with mock.patch.object(white2.White2Adapter, "verify", boom):
            with self.assertRaises(BuildError) as ctx:
                pipeline.run(req)
        message = str(ctx.exception)
        self.assertTrue(message.startswith("FAILED: semantic verification could not complete (ValueError"))
        self.assertIn("no outputs were published", message)
        self.assertNotIn("Traceback", message)
        self.assertFalse(req.output_dir.exists())
        kept = list(req.workroot_base.iterdir())
        self.assertEqual(len(kept), 1)
        logs = list(kept[0].rglob(pipeline.VERIFIER_LOG))
        self.assertEqual(len(logs), 1)
        self.assertIn("Traceback", logs[0].read_text())


if __name__ == "__main__":
    unittest.main()
