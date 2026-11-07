# Architecture: universal randomizer platform

White 2 is the first complete vertical slice. Each layer below is game-independent, and supporting a new game means adding a fingerprint plus an adapter.

```
ROM ──► identify ──► adapter registry ──► capability check ──► workspace ──► adapter.read_model ──► policy (normalized model)
                                                                                                       │
manifest ◄── inputs unchanged ◄── adapter.verify ◄── adapter.postprocess ◄── adapter.rebuild ◄── adapter.apply
```

| Layer | Module | Responsibility |
| --- | --- | --- |
| Identification | `b2er/identify.py`, `b2er/fingerprints.json` | Read-only platform probes (NDS, 3DS, GBA, GB/GBC, GameCube, Switch). Declarative title fingerprints (Generations 1–7). Exact clean-hash authority from `pins/inputs.json`. Status `clean_known`, `recognized_modified`, `recognized_unverified` or `unknown`, with evidence. Each report also carries the release (code, revision, region, languages) and its support status. Filenames are never used. |
| Adapters | `b2er/adapters/` | The `GameAdapter` contract (`base.py`) and the `AdapterRegistry`: one adapter per fingerprint, no central conditional. `gen5/common.py` holds the Generation V layouts shared by Black 2 and White 2. `gen5/white2.py` is the full adapter; `gen5/black2.py` is recognition only. |
| Normalized model | `b2er/model.py` | `SpeciesKey` (National Dex number + form); a versioned, bounds-checked `SpeciesCodec` with reserved non-Pokédex identities (Egg, Bad Egg); species, moves, evolutions with normalized methods, encounter areas/tables/slots, trainers with roles and battle formats, Pokémon sites, item locations. Engine detail lives in `ext`. |
| Capabilities | `b2er/capabilities.py` | Each adapter declares `implemented`, `partial` or `unavailable` per capability, with evidence and numeric limits. Requests are checked statically before a workspace exists, and dynamically (against discovered script sites) before any data is written. |
| Policy | `b2er/randomizer/policy.py` | Every randomization decision and constraint, on the model only. Independent RNG streams are keyed by the seed, the inputs, and that feature's own settings sections. |
| Pipeline | `b2er/pipeline.py` | The universal 14-step flow, the manifest and the spoiler log. |
| Engine families | `b2er/families/` | GB/GBC, GBA, DS Gen IV, 3DS Gen VI/VII containers and codecs, plus `RecognizedFamilyAdapter`. Coverage, status ladder and claim audit in `b2er/coverage.py` and `b2er/coverage.json` (see `docs/coverage.md`). |
| Platform formats | `b2er/nds/` | NDS header/FAT/FNT/overlays/CRC, ARM9 module parameters and compression state, BLZ, strict NARC. |
| Generation V formats | `b2er/gamedata/` | Encounter and trainer codecs, Route 19 fixture verification, fail-closed script disassembler. |
| White 2 engine glue | `b2er/w2u_data.py`, `b2er/adapters/gen5/w2u_model.py`, `b2er/assembly/` | W2U source-data adapter and model translation; workroot, staging, project patches, W2U/mock builders, reports. |

## Adding a game

1. Add a fingerprint entry to `b2er/fingerprints.json`, and its approved hash to `pins/inputs.json`.
2. Implement a `GameAdapter`, reusing the generation-level module if one exists, and declare its capabilities honestly.
3. Register it in `b2er/adapters/__init__.py`.

The policy and the pipeline do not change.

## Species identity

National Dex numbers are not assumed to equal engine IDs. Each adapter supplies a `SpeciesCodec`.

The pinned W2U layout (`w2u-9489aeb` v1) maps #1–#1023 directly and reserves 1024/1025 for Egg/Bad Egg. The codec therefore refuses #1024/#1025, and the White 2 adapter declares `species_expansion` with `national_dex_max: 1023`.

A build requesting the default #1–#1025 is refused before anything is modified. `--species-max 1023` makes the reduction explicit, and it is recorded in the manifest warnings. The codec also rejects any layout where reserved IDs collide with National Dex IDs. The relocated layout from `docs/species-id-model.md` (Option B) passes this check, and tests cover it.
