# Precode foundation

The `b2er.features` package captures product decisions without pretending that
unknown ROM sites are solved. It is deliberately not wired into the live White
2 pipeline yet.

## Boundary

Each planner accepts normalized settings and model facts and returns a
serializable `FeaturePlan` containing:

- requested settings;
- engine-neutral operations;
- adapter capabilities required to apply them;
- warnings and deterministic evidence.

An adapter may expose a feature only after it can:

1. discover proven modification sites;
2. translate the operations into engine data;
3. rebuild the ROM;
4. independently read back and verify the result;
5. pass the feature's runtime checklist.

The v2 settings model and JSON Schema are the future GUI/API boundary. The live
White 2 pipeline continues using v1 until a migration is implemented and tested.

## Implemented planners

- Quick-Start Supplies (defaults: five Master Balls, 999 Rare Candies, normal
  Poké Balls preserved; diagnostic mode forces the aid on).
- Randomized Egg Mode.
- Fast Start identity/starter/checkpoint state.
- One-gimmick player-run selection, Gym priority rules, and the four-role
  Champion contract.
- Generation 9 mechanics completeness matrix.
- Lead-only follower and future overworld encounter contracts.
- Per-game story checkpoint validation.
- Sprite-asset provenance and permission validation.

None of these modules contains guessed addresses, archive members, script IDs,
save offsets, or copyrighted assets.
