# Recovery Baseline

**Recovery date:** November 7, 2026

**Product owner:** Amelie

**PM/systems architect:** Codex/ChatGPT

**Implementation engineer:** Claude

## Status

The complete `0.1.0-alpha.0873` source package, generated outputs, caches, and runtime artifacts are unavailable. The repository must be reconstructed from:

- The surviving PM/architecture handoff.
- The surviving implementation prompt.
- Confirmed terminal logs and runtime observations recorded in the handoff.
- Pinned upstream `hzla/W2U-Project` commit `9489aebf42a870fa6a60816c273b0aa22fe5c1fe`.
- The contracts and acceptance gates below.

No recovered file should be described as byte-identical to `.0873` unless independently proven.

## Confirmed provenance

| Input | Identifier |
| --- | --- |
| White 2 target | US/Europe, `IRDO` |
| Clean White 2 SHA-256 | `3e50aec3db401332175a5d2b5fe2a68ac1a05ec63995dba9d1506b1b51837446` |
| Required Black 2 | US/Europe, `IREO` |
| Clean Black 2 SHA-256 | `2e6b2415354aa41471bc7617068dce059a59931bf5c4348a264f8043f297683a` |
| Upstream | `hzla/W2U-Project` |
| Upstream commit | `9489aebf42a870fa6a60816c273b0aa22fe5c1fe` |
| Primary emulator | melonDS 1.1 Flatpak |

Hashes identify acceptable local inputs; input bytes must never enter this repository or any cloud coding environment.

## Confirmed repairs to reproduce

### ARM9 boot metadata

Earlier W2U-derived ROMs aborted at `0x02004E44`. The confirmed cause was a stale ARM9 BLZ `compressedStaticEnd` value passed into backward decompression. The recovered pipeline must clear or correctly regenerate this field and audit the assembled ARM9 before runtime testing.

The last successful structural log reported:

- ARM9 ROM offset: `0x4000`.
- `compressedStaticEnd` field at ARM9-relative offset `0x4fc4` equal to zero.

These values are recovery evidence, not a universal format assumption. Tests must validate them against the pinned target revision.

### Development-only forced Continue

W2U's `MainMenuSkip(1).dll` forced Continue without a valid save and propagated zone ID `0xFFFF`. The working repair removed the DLL from the staged VFS before CTRMap assembly. Removing only its Meson dependency was insufficient because assembly imported the full VFS tree.

The recovery build must assert that this development plugin is absent from the production VFS and finished candidate.

## Confirmed `.0872` runtime evidence

Passed:

- Normal title and New Game path.
- Bag opening.
- Rival battle completion.
- Rival Carbink #703 rendered and battled correctly.
- In-game save and reload.
- Party persistence and party-screen opening.
- Correct player name on blackout.

Failed or missing:

- Starters remained vanilla because randomizer generation was not implemented.
- A Route 19 expanded encounter displayed at level 100 instead of level 7.
- Capture text displayed a corrupt/question-mark species name.
- Pokédex rendering showed repeated `999 ?????` entries.
- Pokédex use eventually produced ARM9 data abort `0x0203A086`.
- The first breakpoint capture near `GFL_HeapGetBlockHeapID` was a valid invocation and not the corrupting call.

## Diagnostic fixture contract to reconstruct

Every seasonal Route 19 ordinary-grass table should contain only:

| Species | ID | Exact level | Weight | Purpose |
| --- | ---: | ---: | ---: | --- |
| Patrat | 504 | 5 | 35% | Native-format control |
| Carbink | 703 | 6 | 35% | Expanded control proven in trainer battle |
| Chespin | 650 | 7 | 30% | First post-649 boundary control |

The verifier must decode all 48 modified grass slots across four seasonal containers and check species ID, form 0, exact minimum/maximum level, slot offset, and exact 928-byte Route 19 record size.

Expected compiled encounter slot layout:

- `u16` little-endian: species in low 11 bits, form in high 5 bits.
- `u8`: minimum level.
- `u8`: maximum level.

This layout must be verified against pinned upstream code and finished-ROM bytes before it is treated as authoritative.

## Unresolved architecture decision

W2U ordinary species data reportedly ends at #1023 because internal IDs #1024 and #1025 are egg records. The product requirement still includes National species #1024 and #1025. Recovery must document an ID allocation that preserves eggs, saves, party/PC data, battles, graphics, evolution, encounters, and Pokédex behavior. This cannot be solved by silently lowering the supported range.

## Reconstruction sequence

1. Establish repository tests and proprietary-asset guards.
2. Fetch the pinned upstream revision into a disposable local build area.
3. Inventory upstream licenses and redistribution constraints before importing source.
4. Build an immutable-input and provenance layer with exact hash gates.
5. Reproduce and test the ARM9 boot-metadata repair.
6. Reproduce and test production-VFS exclusion of `MainMenuSkip(1).dll`.
7. Reconstruct strict NARC editing and finished-ROM semantic verification.
8. Reconstruct the Route 19 three-species diagnostic fixture.
9. Produce a clean local diagnostic build and request the smallest runtime matrix.
10. Use Patrat/Carbink/Chespin results to locate the earliest divergence before changing Pokédex code.

Randomizer generation and stretch goals remain out of scope until Phase 0 expanded-engine stability passes.
