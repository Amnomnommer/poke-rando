# Pokémon White 2 Expanded Randomizer

## PM/Architect Handoff to Claude

**Project owner:** Amelie

**PM and systems architect:** Codex/ChatGPT

**Implementation engineer:** Claude

**Original handoff date:** November 6, 2026

**Recovery update:** November 7, 2026

**Last known working package:** `0.1.0-alpha.0873` (source unavailable)

> Recovery notice: the `.0873` source package, caches, generated artifacts, and runtime files are no longer available. File names and implementation behavior below describe the last known package and form a reconstruction specification; they are not evidence that those files currently exist. See `RECOVERY_BASELINE.md`.

## 1. Project Outcome

Build a deterministic Pokémon White 2 randomizer that expands the game engine to support National Pokédex species #001–#1025 and produces stable, completable ROMs from a verified clean White 2 ROM.

This is an engineering project, not merely a data randomizer. The expanded engine must correctly carry selected Pokémon through wild encounters, trainers, battles, capture, party, PC, Pokédex, saving, reloading, evolution, and progression logic.

No clean ROM, generated ROM, save, firmware, copyrighted game asset, or extracted proprietary game data may be committed, uploaded, or included in a distributable package. The user supplies her own ROMs locally.

## 2. Roles and Authority

### Amelie — Product Owner and Runtime Tester

- Defines desired player experience and priorities.
- Supplies legally obtained clean ROMs locally.
- Performs only the runtime checks that cannot yet be automated.
- Reports exact observed behavior, screenshots, logs, and save behavior.
- Approves scope changes and architecture tradeoffs.

Amelie should not be asked to hand-edit source files, run ad hoc Python replacements, or repeatedly perform long manual test loops when those actions can be scripted.

### Codex — PM and Systems Architect

- Owns the roadmap, requirements, architecture, acceptance gates, and task sequencing.
- Converts runtime evidence into bounded implementation tickets.
- Reviews Claude’s change reports, test evidence, risks, and proposed architecture.
- Decides whether a change requires an incremental build, clean build, or new runtime matrix.
- Tracks core-requirement coverage and prevents stretch-goal diversion.
- Does not serve as the primary coder after this handoff.

### Claude — Implementation Engineer

- Owns code changes, automated tests, build scripts, migration scripts, packaging, and implementation documentation.
- Inspects the existing implementation before changing it.
- Works from bounded tickets and reports conflicts instead of silently changing scope.
- Returns reproducible commands, changed-file summaries, automated test results, risks, and remaining manual checks.
- Must not claim a requirement passes based only on structural verification.

## 3. Core Requirements

These are the project’s release-driving requirements, in priority order:

1. Support Pokémon #001–#1025 in battle, party, PC, Pokédex, and saves.
2. Randomize starters, wild encounters, trainers, and items.
3. Ensure every selected Pokémon is obtainable and able to evolve.
4. Prevent progression locks, unusable teams, and impossible requirements.
5. Generate deterministic, reproducible ROMs from a clean White 2 ROM.
6. Remain stable through a complete playthrough.
7. Convert every major battle to Doubles.

The core is not complete. Random starter/item/wild/trainer generation was not implemented in the last known `.0873` diagnostic design.

## 4. Deferred Stretch Goals

No stretch work should begin until the core engine and generation pipeline have passed their defined gates.

### High priority after core

- Substantially improved trainer AI.
- Challenge Mode integration and custom level caps.
- Sophisticated rival-team continuity.
- Comprehensive item-location logic beyond safe randomization.
- Extensive cosmetic options or alternate interfaces.

### Medium priority after core

- Rare Candy or level-management systems.
- Early Old Rod and expanded fishing access.
- Early Fly or other navigation conveniences.
- Deep Gym identities or themed-team generation.

### Low priority after core

- Expanded mechanics matching every Generation 8/9 detail.
- FVX compatibility.
- Additional cosmetic/interface work not required for core usability.

## 5. Technical Baseline

### Target and pinned inputs

- Target game: Pokémon White Version 2, US/Europe, game code `IRDO`.
- Clean White 2 SHA-256: `3e50aec3db401332175a5d2b5fe2a68ac1a05ec63995dba9d1506b1b51837446`.
- W2U also requires a clean Black 2 input, game code `IREO`.
- Clean Black 2 SHA-256: `2e6b2415354aa41471bc7617068dce059a59931bf5c4348a264f8043f297683a`.
- Pinned upstream engine: `hzla/W2U-Project`.
- Pinned upstream commit: `9489aebf42a870fa6a60816c273b0aa22fe5c1fe`.
- Primary runtime emulator: melonDS 1.1 Flatpak, `net.kuribo64.melonDS`.
- Debugger: `gdb-multiarch` through melonDS ARM9 stub on port 3333.

### Last known package layout

The unavailable implementation was distributed as an extracted package named approximately:

`Pokemon-White-2-Expanded-Randomizer-0.1.0-alpha.0873`

Important entry points include:

- `Run-Tester.sh`: clean/full pinned build.
- `Run-Incremental-Build.sh`: bounded incremental encounter build when a valid cache exists.
- `Run-Automated-Boot-Test.sh`: automated boot/runtime log probe and optional GDB capture.
- `tester/Launch-melonDS-Phase0.sh`: guided manual acceptance session.
- `scripts/phase0-alpha086.sh`: full White 2/W2U build pipeline.
- `scripts/incremental-alpha0873.sh`: `.0873` scoped incremental pipeline.
- `scripts/alpha086_fixture.py`: deterministic Route 19 diagnostic fixture.
- `scripts/alpha087_arm9_boot_fix.py`: ARM9 compressed-static-end repair.
- `scripts/alpha0871_main_menu_fix.py`: removal of the development-only forced-Continue DLL.
- `src/main/java/dev/b2er/Alpha086Verifier.java`: final-ROM structural verification.
- `src/main/java/dev/b2er/NarcArchive.java`: strict NARC parser/editor support.
- `scripts/test.sh`: Java, script, fixture, and live-interface tests.

Names still containing earlier alpha numbers are historical and should eventually be replaced by responsibility-based names, but renaming is not itself a Phase 0 priority.

## 6. Confirmed History

### Startup failure and repair

Earlier W2U-derived candidates aborted at ARM9 address `0x02004E44`. The clean ROM booted normally. Investigation traced the failure to `MIi_UncompressBackward` receiving invalid data because the assembled ROM restored a stale ARM9 `compressedStaticEnd` value.

`.087` cleared the obsolete compressed endpoint and added a post-build ARM9 audit.

### Forced-Continue failure and repair

After startup was repaired, the game entered a fatal path because W2U’s development-only `MainMenuSkip(1).dll` forced Continue without a valid save and passed zone ID `0xFFFF`.

`.0872` physically removed that DLL from the staged VFS before CTRMap assembly. Removing only the Meson dependency was insufficient because CTRMap imports the complete VFS tree.

### `.0872` runtime results

Confirmed passes:

- Normal title/New Game path launched.
- Bag opened and passed the initial check.
- Rival battle completed smoothly.
- Rival used Carbink (#703), which displayed correctly in trainer battle.
- In-game save was written and loaded.
- Party persisted after reload.
- Party screen opened correctly.
- Player name displayed correctly on blackout.

Confirmed failures or missing behavior:

- Starter choices remained vanilla; randomizer generation was not implemented.
- A Route 19 expanded wild encounter displayed at level 100 instead of level 7.
- Capture text showed a corrupted/question-mark species name.
- Pokédex habitat/list rendering was severely corrupted, including repeated `999 ?????` entries.
- Pokédex use eventually produced `ARM9: data abort (0203A086)`.

The `0x0203A086` helper is near `GFL_HeapGetBlockHeapID`; the first debugger capture was a normal invocation with a valid RAM pointer and therefore a false-positive breakpoint, not the corrupting call.

## 7. Current `.0873` Diagnostic Design

`.0873` is an isolation build, not a Phase 0 exit candidate.

Every seasonal Route 19 ordinary-grass table is intended to contain:

| Species | National ID | Level | Purpose | Weighted rate |
| --- | ---: | ---: | --- | ---: |
| Patrat | 504 | 5 | Native-format control | 35% |
| Carbink | 703 | 6 | Expanded control already proven in trainer battle | 35% |
| Chespin | 650 | 7 | First post-649 boundary control | 30% |

The verifier decodes all 48 grass slots across four seasonal containers and requires:

- Exact species ID.
- Form 0.
- Exact minimum and maximum levels.
- Exact byte offset.
- Exact 928-byte Route 19 record size.

At the last handoff point, Amelie had started a clean `.0873` build and reported that it was generating learnsets. Runtime results were not yet available.

## 8. Unresolved Architecture Blockers

### Species #1024 and #1025

The pinned W2U runtime currently exposes ordinary species data through #1023. Internal IDs #1024 and #1025 are used as egg records. The core requirement nevertheless calls for National species #001–#1025.

This is not a documentation discrepancy. It is an unresolved ID-layout conflict affecting data archives, eggs, validation, save compatibility, graphics lookup, forms, evolution, encounters, party, PC, Pokédex, and possibly script constants.

Claude must not mark #1024/#1025 support complete until the engine has a documented ID-allocation design and runtime tests for both species and eggs.

### Wild encounter corruption

The compiled Gen 5 encounter slot layout is expected to be:

- `u16`: species in low 11 bits, form in high 5 bits.
- `u8`: minimum level.
- `u8`: maximum level.

The source serializer appears to produce that shape, but `.0872` runtime behavior did not match its intended species/name/level. Possible fault domains include:

- Encounter archive member selection or runtime record loading.
- Expanded species lookup during wild Pokémon construction.
- Personal-data or growth-rate lookup for particular expanded species.
- Species-name message archive lookup.
- A corrupt Pokémon object subsequently poisoning Pokédex state.
- A separate Pokédex overlay bug exposed independently.

Do not patch Pokédex code speculatively before using `.0873`’s Patrat/Carbink/Chespin results to narrow the fault domain.

### Pokédex correctness and heap safety

The Pokédex must support seen/caught state, names, entries, habitat display, save/reload, and all supported species without heap corruption. Structural presence of `White2UpgradePokedex.dll` is not evidence of correctness.

The current upstream code includes expanded limits, save-bit extensions, regional-table handling, and overlay patches. These need white-box review against the exact White 2 overlay revision and memory layout.

## 9. Required Engineering Rules

1. Preserve the clean ROMs byte-for-byte and hash-check before and after every build.
2. Pin all external source revisions and dependencies.
3. Keep generated caches disposable; never treat cache presence as correctness.
4. Use incremental builds only when the dependency boundary is explicit and the final ROM is independently verified.
5. Use clean builds for runtime-code changes, build-system changes, milestone candidates, and releases.
6. Every generated ROM must have a SHA-256 and machine-readable build manifest.
7. Structural verification and runtime acceptance are separate gates.
8. No build may be called a Phase 0 exit candidate while a known core path crashes or corrupts data.
9. Automate repetitive emulator/log/debugger work when technically possible.
10. Never ask Amelie to download, patch, rename, and rerun several variants when one scripted matrix can produce the same evidence.
11. Preserve unrelated user changes and do not use destructive repository commands without explicit scope and justification.
12. Do not begin stretch goals while core gates are open.

## 10. Build Strategy

### Incremental development build

Appropriate only when:

- The changed source and every downstream generated artifact are known.
- The build script explicitly rebuilds those dependencies.
- The finished ROM verifier decodes and checks the changed data or code signature.
- The output is clearly labeled diagnostic or development.

### Clean build

Required when:

- Runtime/native code changes.
- Overlay layout or injection changes.
- Build-system or dependency changes.
- Cache provenance is uncertain.
- A milestone is proposed complete.
- A release package is produced.

The clean build must reset a disposable source cache to the pinned upstream commit, apply project patches deterministically, rebuild, assemble, finalize, verify, and emit a manifest.

## 11. Phase and Acceptance Model

### Phase 0 — Expanded-engine stability

Phase 0 is complete only when supported species function in:

- Trainer and wild battle.
- Capture and capture text.
- Party summary and moves.
- PC deposit, display, movement, and withdrawal.
- Pokédex seen/caught/name/entry/habitat state.
- In-game save and cold reload.
- Evolution.
- Representative Fairy/type behavior.
- A bounded multi-area playthrough without crashes or save corruption.

Tests must include native controls, #650 boundary, multiple later-generation IDs, highest supported IDs, forms, gender differences where applicable, eggs, and #1024/#1025 after the ID conflict is resolved.

### Phase 1 — Deterministic randomizer generation

Implement and test:

- Seed and settings normalization.
- Starter randomization.
- Wild encounter randomization.
- Trainer randomization.
- Item randomization.
- Reproducible output hash for identical input/settings/toolchain.
- A spoiler/build manifest that contains no ROM bytes.

### Phase 2 — Validity and progression safety

Implement and test:

- Obtainability coverage.
- Evolution-method normalization.
- HM/key-item/progression protection.
- Level and move legality.
- Team usability constraints.
- Impossible-requirement rejection.

### Phase 3 — Major-battle Doubles

Implement and test conversion of every designated major battle to Doubles, including partner/team assumptions, scripts, AI, and progression consequences.

### Phase 4 — Full-playthrough stability and release

- Automated structural and deterministic-generation suite.
- Runtime route/battle/save matrix.
- Complete human playthrough.
- Clean rebuild reproducibility.
- Release documentation and known limitations.

## 12. Immediate Recovery and Implementation Sequence

Claude should follow this order unless Codex issues a revised ticket:

1. Record that the `.0873` implementation source is unavailable; do not invent a baseline test result.
2. Audit this recovery repository and its proprietary-asset safeguards.
3. Review the pinned upstream revision and its licensing before importing reusable source.
4. Reconstruct the immutable-input, provenance, clean-build, and finished-ROM verification framework.
5. Reproduce the two confirmed boot repairs with regression tests.
6. Reconstruct the strict encounter editor and three-species Route 19 diagnostic from `RECOVERY_BASELINE.md`.
7. Produce a clean local diagnostic build without distributing ROM bytes.
8. Await or ingest runtime results for Patrat, Carbink, and Chespin.
9. Trace the first divergent runtime path indicated by those controls.
10. Return a bounded change report to Codex for architectural review.

If `.0873` results are not yet available, Claude may improve noninvasive build provenance, manifests, cache validation, and test ergonomics, but must not make a speculative encounter/Pokédex runtime patch.

## 13. Claude Deliverable Contract

Every implementation response must include:

1. Outcome in one paragraph.
2. Root cause or working hypothesis, clearly labeled.
3. Files changed and why.
4. Tests added or changed.
5. Exact commands run.
6. Test results with pass/fail counts.
7. Whether the build was incremental or clean and why.
8. Generated artifact names and SHA-256 values, excluding ROM distribution.
9. Remaining risks and unknowns.
10. The smallest manual runtime test still required from Amelie.
11. Recommended next ticket, without beginning it unless authorized.

Claude should stop and ask Codex/Amelie when:

- A choice changes save compatibility or species ID allocation.
- A fix would drop a core requirement.
- A clean ROM revision differs from the pinned hashes.
- A dependency must be upgraded or replaced.
- The proposed change expands into stretch-goal work.
- Copyrighted ROM content would need to leave the user’s machine.

## 14. Definition of Done for the Handoff

The recovery handoff is successful when Claude can reconstruct a provenance-checked diagnostic implementation from the pinned upstream revision, reproduce the two confirmed startup repairs and three-species fixture contract, explain the Phase 0 blockers, and return bounded evidence without asking Amelie to reconstruct the project history.
