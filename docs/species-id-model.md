# Species-ID model and the #1024/#1025 decision

Status: **open architecture decision**. Alpha 0.1 ships the pinned W2U layout, which cannot represent National Dex #1024 (Terapagos) or #1025 (Pecharunt). The core requirement (#001–#1025) is **not met** for those two species. Every build manifest records this (`species_model.requirement_met: false`, `missing_required_national_dex: [1024, 1025]`).

## Current layout (W2U 9489aeb, confirmed from source data)

| Internal ID | Meaning | Evidence |
| --- | --- | --- |
| 0 | `SPECIES_NONE` | `tools/mkdata/enum/species.toml` |
| 1–1023 | Ordinary species (#001–#1023) | enum and `data/pml/meson.build` source order |
| 1024 | `SPECIES_EGG` | enum; `data/pml/egg/` |
| 1025 | `SPECIES_BAD_EGG` | enum; `data/pml/bad_egg/` |
| 1026–1289 | Alternate-form personal records | enum `SPECIES_1026`…`SPECIES_1289`; referenced by each species' `Form Data Offset` |
| personal member 1290 | Regional-dex table | `REGIONAL_DEX_FILE_INDEX 1290` in W2U's Pokédex code |

`b2er.species.SpeciesModel` derives this from the staged enum and rejects any layout where Egg/Bad Egg are not adjacent, ordinary IDs are not contiguous, or IDs exceed 16 bits. Nothing else in this project hard-codes 1024 or 1025.

## Field widths audited

| Field | Width | Fits #1025 and a relocated egg block? |
| --- | --- | --- |
| Party/PC Pokémon species | 16 bits | yes |
| Wild encounter slot species | 11 bits (≤ 2047) | yes |
| Trainer party species | 16 bits | yes |
| Evolution target | 16 bits | yes |
| Pokédex save flags | legacy bits for #1–672, plus 9 extension slots × 44 bytes (352 bits, covering #673–#1024) inside a 432-byte block | **#1025 does not fit.** Widening to 45 bytes per slot (9 × 45 = 405 ≤ 432) fits; this is a save-layout change. |

Bit widths therefore do **not** make a safe design impossible.

## Why it is still open

The native Egg/Bad Egg handling is not in W2U's source tree. It lives in the prebuilt, game-derived `pmc/ARM9PMC.bin` (pinned by SHA-256) and possibly in overlays. W2U's source raises only the *limit* constant (`PkmnCnt = 1023`).

`scripts/scan_species_sentinels.py`, run locally against the pinned `ARM9PMC.bin` with `pmc/IRDO.yml` symbols, reports:

- **No** literal or Thumb-immediate encodings of 1025 in the ARM9 static image.
- Sixteen 1024 candidates, all in unrelated code (graphics, sound, math library, heap). None are evident egg checks.
- Nine literal words equal to 1023 near species-handling code (for example `PML_PkmSetMove+0x48`, `ListMenuCore_*`).

**Inference (unconfirmed):** the engine probably derives the Egg/Bad Egg IDs from the species-count limit (`limit + 1`, `limit + 2`) rather than storing 1024/1025. If so, raising the limit to 1025 moves Egg to 1026 and collides with the first form record. A relocation must then shift every record after #1023.

## Options

| Option | Layout | Save impact | Assessment |
| --- | --- | --- | --- |
| A. Keep W2U (Alpha 0.1) | 1024/1025 = eggs | none | Ships now; fails the #1024/#1025 requirement. |
| B. Shift block (+2) | 1024 Terapagos, 1025 Pecharunt, 1026 Egg, 1027 Bad Egg, 1028–1291 forms, regional dex member 1292 | new saves only (alpha may break experimental saves); Pokédex extension widened to 45 bytes per slot | **Recommended.** Consistent with a limit-derived egg ID. Requires shifting every species-indexed archive (personal, learnsets, evolutions, names, Pokédex text, graphics, icons, cries) and every `Form Data Offset`, plus the evidence below. |
| C. Virtualize | Store Terapagos/Pecharunt at free internal IDs and translate dex numbers | wide native changes to dex numbering | Rejected: every dex/number display and list routine needs translation hooks. |
| D. Drop eggs | — | breaks breeding | Rejected: eggs are a core path. |

## Evidence required before implementing Option B

1. A GDB capture (`./Launch-Alpha-0.1.sh <rom> --gdb`) with a breakpoint on `PML_PkmIsEgg` and on the PML personal-load path, confirming how the Egg ID is computed.
2. A sentinel scan of the decompressed battle, field, Pokédex, and PC overlays (the scanner accepts any local image).
3. Runtime tests on a new save: species #1023, #1024, #1025; Egg receipt and hatching; Bad Egg handling; save, cold reload and identity of each.

Until then, the randomizer's pools exclude IDs ≥ 1024, and the verifier rejects any egg-sentinel or form-record ID in wild, trainer or gift data.
