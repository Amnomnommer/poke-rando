from __future__ import annotations

import json
from pathlib import Path
import unittest

from b2er.features.contracts import FeatureState
from b2er.features.egg_mode import EggCandidate, EggMode, plan_egg_mode
from b2er.features.followers import FollowerSettings, plan_follower
from b2er.features.gimmicks import (
    BattleMember,
    PlayerGimmickMode,
    plan_champion_gimmicks,
    plan_gym_gimmick,
    plan_player_gimmick,
)
from b2er.features.modern_rules import ModernRulesSupport, REQUIRED_1_0_DOMAINS, RuleStatus
from b2er.features.overworld import EncounterPresentation, OverworldSettings, plan_overworld
from b2er.features.quick_start import QuickStartSettings, StarterChoice, plan_quick_start
from b2er.features.settings import DEFAULTS, gui_sections, normalize_v2
from b2er.features.sprite_catalog import AssetPermission, SpriteAsset
from b2er.features.story import StoryCheckpoint, validate_story_path
from b2er.features.supplies import SupplySettings, plan_supplies


ROOT = Path(__file__).resolve().parents[1]


class SettingsTests(unittest.TestCase):
    def test_defaults_are_the_product_decisions(self):
        normalized = normalize_v2({"schema_version": 2})
        self.assertEqual(5, normalized["supplies"]["master_balls"])
        self.assertEqual(999, normalized["supplies"]["rare_candies"])
        self.assertTrue(normalized["supplies"]["preserve_normal_poke_balls"])
        self.assertEqual("generation_9", normalized["modern_rules"]["target"])
        self.assertEqual(0, normalized["follower"]["party_slot"])

    def test_rejects_removed_or_unwanted_product_concepts(self):
        for raw in (
            {"schema_version": 2, "nuzlocke": {"enabled": True}},
            {"schema_version": 2, "gym_themes": {"enabled": True}},
            {"schema_version": 2, "follower": {"party_slot": 1}},
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_v2(raw)

    def test_gui_sections_are_build_time_roadmap_fields(self):
        sections = {item["id"]: item["release"] for item in gui_sections()}
        self.assertEqual("1.0", sections["supplies"])
        self.assertEqual("1.0", sections["egg_mode"])
        self.assertEqual("1.3", sections["fast_story"])
        self.assertEqual("1.4", sections["sprite_customization"])

    def test_json_schemas_parse(self):
        for path in (
            ROOT / "schemas/randomizer-settings-v2.json",
            ROOT / "schemas/sprite-asset-provenance-v1.json",
            ROOT / "b2er/features/modern_rules.json",
        ):
            with self.subTest(path=path):
                json.loads(path.read_text(encoding="utf-8"))


class SupplyTests(unittest.TestCase):
    def test_diagnostic_forces_default_aid(self):
        plan = plan_supplies(SupplySettings(), diagnostic=True, max_stack=999)
        self.assertTrue(plan.settings["enabled"])
        self.assertEqual([5, 999], [op["quantity"] for op in plan.operations])
        self.assertTrue(plan.settings["preserve_normal_poke_balls"])
        self.assertTrue(plan.metadata["diagnostic_forced"])

    def test_adapter_stack_limit_is_not_silently_clamped(self):
        with self.assertRaises(ValueError):
            plan_supplies(SupplySettings(enabled=True), max_stack=99)


class EggModeTests(unittest.TestCase):
    def test_full_mode_is_deterministic_and_covers_sites(self):
        kwargs = dict(
            mode=EggMode.FULL,
            seed="same seed",
            candidates=[EggCandidate(1), EggCandidate(650), EggCandidate(703)],
            route_sites=["route19"],
            gift_sites=["daycare"],
        )
        first = plan_egg_mode(**kwargs)
        second = plan_egg_mode(**kwargs)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(5, len(first.operations))

    def test_mode_without_eligible_site_is_refused(self):
        with self.assertRaises(ValueError):
            plan_egg_mode(EggMode.ROUTE, seed="x", candidates=[EggCandidate(1)])


class QuickStartTests(unittest.TestCase):
    def test_records_three_choices_and_selected_starter(self):
        choices = [StarterChoice(1), StarterChoice(4), StarterChoice(7)]
        plan = plan_quick_start(
            QuickStartSettings(enabled=True, player_name="A", rival_name="B", starter_index=2),
            starters=choices,
            max_name_length=7,
        )
        self.assertEqual(7, plan.operations[2]["species"])
        self.assertEqual(3, len(plan.metadata["displayed_starters"]))
        self.assertEqual(4, len(plan.requirements))

    def test_invalid_name_or_choice_is_refused(self):
        choices = [StarterChoice(1), StarterChoice(4), StarterChoice(7)]
        for settings in (
            QuickStartSettings(enabled=True, player_name=""),
            QuickStartSettings(enabled=True, player_name="TOO-LONG", starter_index=1),
            QuickStartSettings(enabled=True, player_name="A", starter_index=3),
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                plan_quick_start(settings, starters=choices, max_name_length=7)


class GimmickTests(unittest.TestCase):
    def test_player_gets_exactly_one_seeded_gimmick(self):
        first = plan_player_gimmick(PlayerGimmickMode.SEEDED, seed="run")
        second = plan_player_gimmick(PlayerGimmickMode.SEEDED, seed="run")
        self.assertEqual(first.operations, second.operations)
        self.assertEqual(1, len(first.operations))

    def test_gym_priority_is_mega_then_signature_z(self):
        mega = plan_gym_gimmick(
            [BattleMember(0, 1), BattleMember(1, 6, is_ace=True, mega_eligible=True, signature_z_move="x")],
            seed="s", boss_id="gym1",
        )
        z_move = plan_gym_gimmick(
            [BattleMember(0, 1), BattleMember(1, 25, is_ace=True, signature_z_move="catastropika")],
            seed="s", boss_id="gym1",
        )
        self.assertEqual("mega", mega.operations[0]["gimmick"])
        self.assertEqual("z_move", z_move.operations[0]["gimmick"])

    def test_champion_uses_four_distinct_roles_and_steel_finale(self):
        plan = plan_champion_gimmicks([
            BattleMember(0, 1),
            BattleMember(1, 6, mega_eligible=True),
            BattleMember(2, 25, signature_z_move="catastropika"),
            BattleMember(3, 9, is_ace=True),
            BattleMember(4, 150),
            BattleMember(5, 149),
        ])
        self.assertEqual(["dynamax", "mega", "z_move", "terastallize"], [op["gimmick"] for op in plan.operations])
        self.assertEqual(4, len({op["slot"] for op in plan.operations}))
        self.assertEqual("steel", plan.operations[-1]["tera_type"])


class LaterFeatureTests(unittest.TestCase):
    def test_modern_rules_are_all_required(self):
        support = ModernRulesSupport("test", {name: RuleStatus.IMPLEMENTED for name in REQUIRED_1_0_DOMAINS})
        self.assertTrue(support.complete)
        degraded = dict(support.domains)
        degraded["fairy_type"] = RuleStatus.PARTIAL
        self.assertEqual(("fairy_type",), ModernRulesSupport("test", degraded).unresolved())

    def test_only_lead_can_follow(self):
        self.assertEqual(0, plan_follower(FollowerSettings(enabled=True)).operations[0]["party_slot"])
        with self.assertRaises(ValueError):
            plan_follower(FollowerSettings(enabled=True, party_slot=1))

    def test_overworld_is_a_future_capability_contract(self):
        plan = plan_overworld(OverworldSettings(mode=EncounterPresentation.OVERWORLD))
        self.assertTrue(plan.missing({"overworld_encounter_actor": FeatureState.UNAVAILABLE}))

    def test_story_path_requires_proven_order(self):
        result = validate_story_path([
            StoryCheckpoint("intro", grants=("starter",)),
            StoryCheckpoint("center", requires=("starter",), grants=("pokedex",)),
        ])
        self.assertEqual(("pokedex", "starter"), result)
        with self.assertRaises(ValueError):
            validate_story_path([StoryCheckpoint("center", requires=("starter",))])

    def test_sprite_asset_requires_reuse_evidence(self):
        valid = SpriteAsset(
            asset_id="lead-650", source_url="https://example.test/asset", creator="Artist",
            permission=AssetPermission.EXPRESS_PERMISSION, license_or_permission="Permission archived as P-1",
            art_style="gen5", directions=4, frames_per_direction=3, species=650,
        )
        valid.validate()
        with self.assertRaises(ValueError):
            SpriteAsset(
                asset_id="lead-650", source_url="https://example.test/asset", creator="Artist",
                permission=AssetPermission.EXPRESS_PERMISSION, license_or_permission="",
                art_style="gen5", directions=4, frames_per_direction=3, species=650,
            ).validate()


if __name__ == "__main__":
    unittest.main()
