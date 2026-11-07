# Pokémon White 2 Expanded Randomizer

Recovery and development repository for a deterministic Pokémon White 2 randomizer with expanded species support.

## Repository state

The original `0.1.0-alpha.0873` working source package is unavailable. This repository begins from the surviving project specification, confirmed runtime evidence, known upstream revision, and implementation contracts. It does **not** claim to contain the lost `.0873` implementation.

The first engineering milestone is a clean-room recovery from the pinned upstream W2U revision, followed by reproduction of the confirmed boot fixes and the three-species Route 19 diagnostic.

## Core requirements

1. Support National Pokédex species #001–#1025 in battle, party, PC, Pokédex, and saves.
2. Randomize starters, wild encounters, trainers, and items.
3. Ensure every selected Pokémon is obtainable and able to evolve.
4. Prevent progression locks, unusable teams, and impossible requirements.
5. Generate deterministic, reproducible ROMs from a clean White 2 ROM.
6. Remain stable through a complete playthrough.
7. Convert every major battle to Doubles.

Stretch goals are deferred until all core gates pass.

## Alpha 0.1

The first integrated, locally buildable candidate is here. It is **structurally verified only**: runtime acceptance has not been performed.

```sh
./b2er.sh identify /path/to/rom              # any ROM: identity, generation, adapter, evidence
./b2er.sh capabilities                        # per family/title/release status and capability levels
./Build-Alpha-0.1.sh --white2 /path/to/cleanwhite2.nds --black2 /path/to/cleanblack2.nds \
                     --w2u /path/to/W2U-Project --seed "example-seed" --species-max 1023
./Launch-Alpha-0.1.sh Alpha-0.1-builds/<label>.nds [--smoke 90 | --gdb]
```

- [`docs/coverage.md`](docs/coverage.md): all 29 required games, engine families, and the recognized → playthrough-validated status ladder.
- [`docs/architecture.md`](docs/architecture.md): identification, adapters, normalized model, capabilities, pipeline.
- [`docs/alpha-0.1.md`](docs/alpha-0.1.md): prerequisites, build steps, outputs, launcher, first-play checklist.
- [`docs/randomizer.md`](docs/randomizer.md): features, guarantees, known defects.
- [`docs/species-id-model.md`](docs/species-id-model.md): species-ID layout and the open #1024/#1025 decision.

## Start here

- [`B2ER_PM_ARCHITECT_HANDOFF.md`](B2ER_PM_ARCHITECT_HANDOFF.md) — product scope, architecture, evidence, and acceptance gates.
- [`CLAUDE_IMPLEMENTATION_PROMPT.md`](CLAUDE_IMPLEMENTATION_PROMPT.md) — implementation-engineer operating contract and first recovery assignment.
- [`RECOVERY_BASELINE.md`](RECOVERY_BASELINE.md) — what survived, what was lost, and the bounded reconstruction sequence.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — repository safety and contribution rules.

## Copyright boundary

This repository must never contain commercial ROMs, saves, firmware, extracted game files, generated ROMs, or proprietary assets. Users supply legally obtained clean ROMs locally. Project patches, source code, tests, manifests, and documentation may be tracked only when they contain no copyrighted game data.

No license has yet been selected for original project code. Until the product owner chooses one, no permission to redistribute or reuse original repository material is implied.
