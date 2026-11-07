# Game coverage and support status

The product must fully support all 29 mainline games distributed as GB/GBC, GBA, Nintendo DS and Nintendo 3DS images. This page records exactly where each one stands. The live view is `./b2er.sh capabilities` (add `--json` or `--verbose`).

## Status ladder

| Status | Meaning |
| --- | --- |
| recognized | The release is identified (platform header, game code or title, revision, region) and validated structurally. Nothing is built. |
| buildable | An adapter safely unpacks, rebuilds, and verifies an approved clean release. |
| feature-complete | Every required capability is implemented (`FEATURE_COMPLETE_REQUIRES` in `b2er/capabilities.py`). |
| runtime-validated | The required gameplay checks have passed on hardware or an emulator. |
| playthrough-validated | A complete playthrough has passed, including saving and reloading. |

Rules:

- **Statuses belong to releases, not titles.** A status attaches only to a pinned release: fingerprint + game code + approved clean SHA-256 in `b2er/coverage.json`. Every other region, language or revision of the same title is at most *recognized*, and the identifier reports it as `recognized_unverified`, or as `recognized_modified` when the title has a pinned release but the hash differs.
- **Claims are audited.** `coverage.audit()` rejects any claim the adapter's declared capabilities don't back. *Buildable* needs repacking and deterministic verification implemented. *Feature-complete* needs every required capability implemented. The pipeline refuses to run while any claim fails, and tests try over-claims.
- **Capability levels are a separate vocabulary.** They are `implemented`, `partial` and `unavailable`, never "supported". A recognized-only title is never described as supported.

## Current state

| Family | Engine module | Titles | Status |
| --- | --- | --- | --- |
| `gb` | `b2er/families/gb.py` (header + global checksum validate/refresh) | Red, Green, Blue, Yellow, Gold, Silver, Crystal | recognized |
| `gba` | `b2er/families/gba.py` (header checksum, BIOS LZ77 codec) | Ruby, Sapphire, Emerald, FireRed, LeafGreen | recognized |
| `nds-gen4` | `b2er/families/nds_gen4.py` + shared `b2er/nds` (container, NARC) | Diamond, Pearl, Platinum, HeartGold, SoulSilver | recognized |
| `nds-gen5` | `b2er/adapters/gen5` | Black, White, Black 2 | recognized |
| `nds-gen5` | `b2er/adapters/gen5` | **White 2 (`IRDO`, approved hash)** | **buildable** |
| `ctr-gen6` | `b2er/families/ctr.py` (NCSD/NCCH/CIA inspection with crypto state; strict GARC codec) | X, Y, Omega Ruby, Alpha Sapphire | recognized |
| `ctr-gen7` | `b2er/families/ctr.py` | Sun, Moon, Ultra Sun, Ultra Moon | recognized |

White 2 is not yet feature-complete. It is missing #1024/#1025, hidden items, shops and save-format expansion. It is not yet runtime-validated either: that needs the first-play checklist in `docs/alpha-0.1.md`.

## Engine-family strategy

Each family module owns the shared containers, formats and transformations. A game adapter contributes only:

- its fingerprint;
- its pinned releases and approved hashes;
- its archive identities, offsets and script differences;
- its version-specific exceptions.

Adding a family never replaces identification, the normalized model, policy, RNG, settings, constraints, the manifest, the verification framework or the CLI.

Every title is recognized the moment its fingerprint exists, through `RecognizedFamilyAdapter`. A full adapter replaces that entry in `b2er/adapters/__init__.py` once its build path exists and the audit accepts its claim.

## Compatibility implementations required per family

These are deliberate substitutes, not removals:

| Family | Required compatibility work |
| --- | --- |
| `gb` | 8-bit species IDs and a 251-species engine need a new species-ID/save architecture for #1025; type-chart extension for Steel/Dark/Fairy (Gen I) and Fairy (Gen II); modern evolution methods mapped to achievable ones; Doubles is not available in the engine (documented substitute required). |
| `gba` | Species tables, Pokédex flags and saves must grow past 412 internal slots; Fairy type; abilities and moves beyond Generation III; Doubles exists natively (Generation III). |
| `nds-gen4` | Species/Pokédex/save expansion past 493; Fairy type; forms; reuse of the shared NDS/NARC layers. |
| `nds-gen5` | The #1024/#1025 Egg-ID relocation (`docs/species-id-model.md`). |
| `ctr-gen6` / `ctr-gen7` | Decrypted-image requirement (no keys handled); species past #721/#809; GARC-based data. |

## Expansion order

White 2 runtime-stable, then Black 2, Black/White, one Generation IV game, the remaining Generation IV games, Generation III, Generations VI–VII, and finally Generations I–II. The order may change on technical evidence, but every listed title remains required.
