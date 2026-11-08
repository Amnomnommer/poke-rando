# Product scope

This is the canonical feature scope. A release number is a target, not a claim
that every adapter already implements it. Adapter capability reports and
runtime acceptance remain authoritative.

## Product rules

- The app identifies the supplied ROM, selects its adapter, and exposes only
  settings that adapter can safely apply and verify.
- Settings live in the pre-build randomizer GUI. They are not an in-game menu.
- Randomization is deterministic from the seed and feature settings.
- A feature is not available merely because its planner exists. Its adapter
  must apply it, independently verify the output, and pass runtime acceptance.
- No ROMs, extracted game data, firmware, saves, or unlicensed art assets enter
  the repository.
- Trainer randomization uses global strength, legality, moves, items, ability,
  evolution, level, and Doubles-viability rules. It does not invent Gym themes
  or hand-author themed teams.

## 1.0 — modernized randomizer baseline

- Pokémon identities #1–#1025, with Egg and Bad Egg kept outside ordinary
  Pokédex identities. Temporary adapter caps must be explicit.
- Fairy type and the current type chart.
- Physical/special split.
- Current status behavior; modern weather and terrain.
- Modern moves, abilities, held items, evolution methods, learnsets,
  compatibility, and configurable modern experience behavior.
- Deterministic randomization for starters, wild encounters, trainers, gifts,
  statics, field items, hidden items, shops, evolutions, and major-battle
  Doubles where the adapter supports them.
- Randomized Egg Mode: starter Eggs, route-encounter Eggs, gift Eggs, or full
  Egg mode, with safe pools and quick hatching.
- Fast Start: choose the player name, rival name, avatar, and one of the three
  randomized starters before building; begin at the first Pokémon Center with
  a legal state equivalent to winning the first rival battle and completing
  the catching tutorial.
- Quick-Start Supplies: keep the normal early Poké Balls and optionally add
  five Master Balls and 999 Rare Candies (or the adapter's verified safe
  maximum). Diagnostic builds enable this automatically.

## 1.1 — run systems and convenience

- Improved AI, level management and caps, Challenge Mode-style options, rival
  continuity, full item logic, early fishing/Fly access, HM convenience, and
  infinite repel.
- Lead Pokémon follower only.
- Optional Mega Evolution, Z-Moves, Dynamax, and Terastallization. The player
  selects one gimmick for the run in the pre-build GUI, or lets the seed choose;
  the game prevents use of the other three.
- Gym boss assignment happens after team randomization: Mega ace when eligible,
  otherwise signature Z-Move ace, otherwise seed-selected Dynamax lead or Tera
  ace.
- Champion exception in Doubles: Dynamax lead, a distinct guaranteed Mega, a
  distinct Z-Move user, and a final Steel-Tera ace. The team remains randomized;
  these are roles assigned afterward.

## Cross-game parity and later releases

- **1.1.5:** at least one representative mainline ROM game per generation has
  reached the 1.1 contract.
- **1.2:** optional overworld encounters, with deterministic spawning and
  licensed species/form sprites. Lead follower support remains lead-only.
- **1.2.5:** every supported mainline ROM game reaches 1.2 parity.
- **1.3:** per-game/per-generation streamlined-story adapters. This is not a
  generic flag guesser.
- **1.3.5:** every supported game reaches 1.3 parity.
- **1.4:** character sprite selection from compatible art-style/engine catalogs
  for player, rival, Gym Leaders, Champion, NPCs, and trainers. Every asset must
  carry provenance and reuse permission.

## Explicit non-goals

- No Nuzlocke rule enforcement.
- No themed Gym-team generator or bespoke boss-team authoring.
- No party-wide follower system; only the lead follows.
- No importing sprite work merely because another ROM hack uses it. Closed
  projects or multi-artist packs require permission for each included asset.
