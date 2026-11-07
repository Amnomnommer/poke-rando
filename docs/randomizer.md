# Randomizer design (Alpha 0.1)

## Settings and determinism

`settings.example.json` documents every option. `b2er.randomizer.settings.normalize` handles them:

- defaults are filled in;
- unknown options are rejected, and stretch-goal options are rejected with an explanation;
- impossible combinations are rejected before any work starts (for example, the diagnostic mode combined with any randomization).

**Build key.** The build key is SHA-256 over:

- the normalized settings;
- the seed;
- the approved input hashes;
- the W2U and swan commits;
- the pin-file hashes;
- the project commit and whether the tree was dirty;
- the builder.

**Random streams.** All randomness comes from HMAC-SHA256 counter streams keyed by the build key, one independent stream per feature (`wild`, `starters`, `trainers`, `gifts`, `statics`, `items`). Python's `random` is not used.

**Reproducibility.** Identical inputs, revision, pins, settings and seed give an identical plan. With the mock builder they also give an identical ROM hash, which is tested. W2U's real toolchain is expected to be deterministic, but that is not yet proven.

## Features

| Feature | Level | How |
| --- | --- | --- |
| Evolutions | source | Impossible methods are rewritten. Trade becomes level 30/40. Trade + held item becomes "level up holding the item", day or night. Item, move and upside-down methods that need unobtainable items or moves become level-up. Feasible methods (level, friendship, stones obtainable in White 2, location, held items) are kept. |
| Wild | source | Per encounter record, each original species maps to one replacement of similar base-stat total. The replacement must have a damaging move by the slot's minimum level. Form is set to 0 and levels are kept. Legendaries are excluded by default. |
| Starters | source + script | Three base-stage species that evolve, BST ≤ 360, with a damaging move at level 5, preferring distinct primary types. The gift commands are patched in the finished ROM's script archive. Rival teams that used a vanilla starter line get the matching new line at the level-appropriate stage. |
| Trainers | source | Each member is replaced from the obtainable closure with a similar BST and a usable move. Explicit movesets are recomputed from level-up moves. Legendaries are replaced by legendaries. |
| Major-battle Doubles | source | Identified from game data: classes *Leader*, *Elite Four*, *Champion*, and story trainers Rival, N, Ghetsis, Colress, Zinzolin, Shadow. These get battle type Double, a party padded to at least 2, and the Doubles AI flag. The first rival battle(s) with a one-Pokémon party stay Singles, because the player may own only one Pokémon. W2U's single-NPC Doubles fix handles scripted Doubles. |
| Items | script | `ItemAdd` constant sites are shuffled within class (ordinary items, TMs). HMs, key items and unclassified items never move. |
| Gifts / statics | script | Optional (off by default). `PokePartyAdd`/`PokePartyAddEx` and `CallWildBattle(Ex)` constant species are replaced from the obtainable closure. |
| Route 19 diagnostic | source | Resolved by evidence: the unique four-season record whose grass has Patrat, Purrloin and Pidove at levels ≤ 5. Never assumed to be member 129. |

**Script-level sites.** These come from a fail-closed disassembler whose command table is read at build time from your pinned local CTRMap jar; this repository never contains it. A site is patched only when its command was decoded on a fully known control-flow path, and patches are same-width in place. If a feature's sites cannot be established, the feature is reported as `skipped (...)`, or the build fails when `unsupported_feature_policy` is `"fail"`.

## Guarantees enforced before any ROM is built

- Every wild, starter and gift species is obtainable by construction. Trainer species come from the obtainable closure (obtainable species and everything they evolve into).
- Every evolution of an obtainable species uses a method feasible in White 2 (after normalization), and its targets are inside the closure.
- Every TM/HM compatibility bit that vanilla wild Pokémon offer keeps at least 2 obtainable learners. This preserves every field HM without assuming W2U's bit layout.
- Starters have a damaging move at level 5 and an evolution line.
- Trainer parties are legal: ordinary species, levels 1–100, and a damaging move at their level. Converted Doubles have at least 2 Pokémon.
- Key items and HMs are never relocated, so no required item can be placed behind itself.
- Up to 8 deterministic attempts are made. If none satisfies every constraint, the build stops before assembly.

## Known defects and risks (Alpha 0.1)

- **#1024/#1025 unsupported.** See `docs/species-id-model.md`.
- **Starter-selection screen** may still *display* vanilla starter graphics and cries. Only the gift commands are patched, and the selection overlay is not modified.
- **Field item coverage** is limited to `ItemAdd` constant sites. Hidden items, and item balls that use a different mechanism, are reported as not randomized.
- **HM ordering.** The HM constraint is global: it does not model which route an HM learner appears on relative to the HM gate.
- **Wild levels** are kept from vanilla. Doubles conversion keeps vanilla scripts. The first rival battle remains Singles.
- **Base file-system layout.** The extracted `IRDO_Extracted` follows the layout W2U reads (`data/`, `overlay/overlay_NNNN.bin`, `arm9.bin`, `y9.bin`). CTRMap's exact expectations for the other binaries are unconfirmed until the first local build.
- **Legendary heuristic.** Undiscovered egg group, capture rate ≤ 45, and no evolutions. Edge cases are possible.
- **W2U data inconsistency.** One personal table name (Flabébé) is mis-encoded in W2U. The source-list position is used and the mismatch is recorded in the plan.
