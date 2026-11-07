# R-001 — Pinned Upstream Audit and Recovery Scaffold Proposal

**Ticket:** [#1](https://github.com/Amnomnommer/poke-rando/issues/1)
**Audit date:** November 7, 2026
**Scope:** audit and planning only. No upstream code imported, no ROM bytes used, no runtime fixes.

Evidence labels used throughout:

- **Confirmed**: directly observed in pinned upstream files or in this repository during this audit.
- **Recorded**: taken from the surviving handoff/runtime notes; not re-observed here.
- **Inference**: reasoning from confirmed facts; must be tested before it is relied on.

## 1. Baseline status

- **Confirmed:** this repository contains only documentation, `.gitignore`, a CI workflow, and `scripts/check-repository-safety.sh`. The `0.1.0-alpha.0873` package (`Run-Tester.sh`, `scripts/test.sh`, `Alpha086Verifier.java`, `NarcArchive.java`, fixture scripts, and so on) is not present.
- **Confirmed:** no `.0873` implementation baseline exists to run, so there is no baseline test result. Nothing in this audit is claimed to be byte-identical to `.0873`.
- **Confirmed:** `bash scripts/check-repository-safety.sh` → `Repository safety check passed.` (exit 0, 8 tracked files before this audit).

## 2. Pinned upstream identity

| Item | Value |
| --- | --- |
| Repository | `https://github.com/hzla/W2U-Project` |
| Commit | `9489aebf42a870fa6a60816c273b0aa22fe5c1fe` (2026-10-04, "Refresh README for current Pokeweb runtime and build") |
| Tracked files | 99,471 (≈667 MB working tree) |
| Submodule | `include/swan` → `kingdom-of-ds-hacking/swan` @ `07187e940a097309500769c83aac313032f970ac` |
| Vendored natives | `include/extlib` (HelloOO7/ExtLib @ `4fcc85b7…`), `include/NitroKernel` (HelloOO7/NitroKernel @ `3cad669b…`), per `include/native-dependencies.json` |
| Bundled binary tool | `tools/CTRMap/CTRMapV-dirty.jar`, SHA-256 `29739c1942211434a372487f5ec7f30c10c8ffeefa6314da974883bdbfdd2b35` (custom ROMBuilder/RPMTool build, no source in repo) |
| Python deps | `requirements.txt`: `Pillow`, `ndspy`, `PyYAML`, all **unpinned** |
| Meson | 1.7.0, cloned by `make meson` from GitHub by tag (tag, not commit) |

The commit was fetched with `git fetch --depth 1` into a disposable scratch directory outside this repository. Nothing from it was copied here.

## 3. Upstream architecture (confirmed from source)

- **Build system:** Meson + Ninja with an ARM cross file (`meson/nitro.ini`); `Makefile` is only a wrapper.
- **Native code:** C++/Thumb assembly under `src/` (gameplay, Mega, terrain, battle modules, Pokédex expansion, type expansion, battle log, PWAN graphics) compiled to ELF and packaged as PMC RPM "DLLs" under `vfs/data/patches/` and `vfs/data/lib/w2u_battle/`.
- **Code-injection runtime:** PMC, supplied as prebuilt binaries in `pmc/` (`ARM9PMC.bin`, `OVL344.bin`, `RPMSYM-PMC.rpm`) plus symbol databases (`IRDO.yml`, `IREO.yml`, `Symbols.map`).
- **Data:** TOML sources under `data/` serialized by `tools/mkdata/mkdata.py` (generic struct, encounter, trainer, LZ, text, y9 serializers).
- **Staging:** generated files are written into the *source-tree* `vfs/` directory. CTRMap treats `../IRDO_Extracted` as `VFSBase` and `vfs/` as `VFSOverlay` (`White2Upgrade.cmproj.example`).
- **Assembly:** `ctrmap.cli.ROMBuilder` builds `White2Upgrade.nds` from the cmproj. Then `tools/patch_arm9_footer.py` runs, then `tools/finalize_white2_rom.py` (root `meson.build`, target `White2Upgrade.nds`).
- **Inputs read from outside the repository:** `../IRDO_Extracted` (extracted clean W2 file system), `../Port-Pokeweb/cleanwhite2.nds`, `../Port-Pokeweb/cleanblack2.nds`. **No hash gate exists for any of them.**
- **Fast path:** `tools/quick_rom_rebuild.sh` / `tools/dev_rom_patch.py` patch NARCs in place inside a previous ROM through the FAT/FNT. It is explicitly dev-only.

## 4. Files relevant to the five recovery areas

### 4.1 ARM9 compressed-static-end handling

| File | Role |
| --- | --- |
| `pmc/ARM9PMC.bin` | Prebuilt, **decompressed** PMC-patched ARM9 image (633,920 bytes, SHA-256 `8c6c2a4b…6431`). |
| `tools/item_icons/build_item_icon_patch.py` | Patches an item-graphics table in that image and writes it to `vfs/arm9.bin` (target `pmc_arm9`). |
| `tools/patch_arm9_footer.py` | After ROMBuilder, copies the 4-byte word at ARM9-relative `0x0FC4` from `vfs/arm9.bin` into the ROM at `arm9_rom_offset + 0x0FC4`. |
| root `meson.build` | Wires the above into the `White2Upgrade.nds` command. |

**Confirmed (static analysis of `pmc/ARM9PMC.bin`):**

- The NITRO module-parameter block sits at ARM9-relative `0xFB0`. `0xFCC` = `0xDEC00621` and `0xFD0` = `0x2106C0DE` are the SDK markers.
- `compressedStaticEnd` (params + `0x14`) is at ARM9-relative **`0xFC4`** and holds **`0x020776CC`**, a non-zero value.
- The 8 bytes before that address do not form a valid backward-LZ footer (header length decodes to 237, compressed length to `0x2EF000`), so the image is not BLZ-compressed.
- So upstream's footer-restore step writes a non-zero "compressed end" into a ROM whose ARM9 is stored uncompressed. That is the mechanism the handoff records: `MIi_UncompressBackward` runs on uncompressed data and aborts at `0x02004E44`.

**Inference to verify:** `RECOVERY_BASELINE.md` gives the field as "ARM9-relative offset `0x4fc4`" with ARM9 ROM offset `0x4000`. Upstream uses ARM9-relative `0xFC4`, and `0x4000 + 0xFC4 = 0x4FC4`, so the recorded `0x4fc4` is most likely a **ROM-absolute** offset that was labelled "ARM9-relative". The reconstructed audit must locate the field from the module-parameter block, not from a hard-coded constant. It should also assert that `compressedStaticEnd == 0` whenever the stored ARM9 is not BLZ-compressed.

### 4.2 Staged-VFS / plugin inclusion

| File | Role |
| --- | --- |
| `MainMenuSkip(1).dll` (repo root) and `vfs/data/patches/MainMenuSkip(1).dll` | Same bytes, SHA-256 `cd1da75c…a67f`. |
| root `meson.build` line 88 | `main_menu_skip_patch` is listed as an input of the ROM target. |
| `White2Upgrade.cmproj.example` | `VFSOverlay: vfs` makes ROMBuilder import the whole `vfs/` tree. |
| `tools/mkdata/pack_narc.py` | Writes staged NARC folders (`.arc` marker + numbered members) into `vfs/data/...`. |
| `tools/stage_w2u_battle_modules.py`, `tools/stage_double_battle_fix.py` | Stage generated DLLs into `vfs/`. |

**Confirmed:** the DLL is tracked inside the VFS overlay that ROMBuilder imports wholesale. That matches the recorded finding that dropping the Meson dependency alone is insufficient.

**Confirmed hazard:** `vfs/` is a shared, mutable, partly tracked and partly ignored staging tree. Both `build/` and `build-stripped/` write into it (upstream README warns against concurrent builds). Stale staged files survive between builds unless something deletes them. This is the main cache-provenance risk for any recovery pipeline.

### 4.3 NARC editing

| File | Role |
| --- | --- |
| `tools/mkdata/pack_narc.py` | Does not build NARCs. It writes a CTRMap `.arc` directory (members `0..N-1`), and CTRMap does the packing. **It renumbers inputs by sorted trailing integer**, so a missing input silently shifts every later member index. |
| `tools/dev_rom_patch.py` | Packs a staged `.arc` folder and replaces one ROM file through FAT/FNT (uses `ndspy.fnt`). |
| `docs/narc-structures.md` | Format notes. |

**Confirmed:** upstream has no strict NARC parser or verifier that decodes a finished ROM's archive and compares it to the intended members. The recorded `.0873` `NarcArchive.java` was project code and is lost.

### 4.4 Encounter serialization

| File | Role |
| --- | --- |
| `tools/mkdata/impl/enc.py` | `EncounterSerializer`. Writes 8 rate bytes, then per slot `u16 (form << 11) \| species`, `u8 min`, `u8 max`. |
| `data/encounters/{0..134}.toml` | 135 sources → `vfs/data/a/1/2/7/` (`data/encounters/meson.build`). |
| `tools/mkdata/enum/species.toml` | Label → ID map. |

**Confirmed:**

- Layout matches the recorded expectation. Each season container is `8 + 56 × 4 = 232` bytes (12 grass singles, 12 grass doubles, 12 grass special, 5+5 surf, 5+5 fish). 123 files have 1 container (232 bytes). 12 files have 4 seasonal containers (**928 bytes**), which matches the recorded Route 19 record size.
- The 135 sources are contiguous `0..134`, so the renumbering hazard in 4.3 is latent at this commit.
- `species.toml` maps `SPECIES_EGG = 1024` and `SPECIES_BAD_EGG = 1025`, with ordinary species only up to `SPECIES_1023`. This confirms the #1024/#1025 conflict at the pinned commit.

**Confirmed latent defects in `enc.py` (none triggered by upstream's own data; every upstream slot uses a string label):**

1. If `Species` is an integer rather than a label, the code assigns `species = slot['Form']`. An integer species ID is replaced by the form number (normally 0).
2. Neither species nor form is range-checked before `(form << 0xB) | species`. A species ≥ 2048 or a form ≥ 32 corrupts the adjacent field or raises.
3. Slot counts per table are not enforced, and slot order follows TOML key order. A missing or extra slot changes the record size and shifts every later slot.
4. `GenericSerializer` prints "Label not defined" for an unknown label, then **continues with value 0** instead of failing the build.

**Inference only:** Route 19 is most likely `data/encounters/129.toml` (archive member 129). It is the only seasonal file with early-game grass levels: Patrat, Pidove, Purrloin and Sewaddle at levels 2–4. This must be confirmed against the zone→encounter mapping before a fixture targets it. Whether any of the four defects above contributed to the `.0872` level-100 / `?` symptom **cannot be determined**, because the lost fixture's method of writing slots is unknown. Per the handoff, no encounter fix is proposed.

### 4.5 Finished-ROM assembly and verification

| File | Role |
| --- | --- |
| `tools/finalize_white2_rom.py` | Checks the NTR/TWL boundary, FAT ranges, digest-table placement and ARM9i/ARM7i placement. Rewrites the secure-area CRC, logo CRC, header CRC and the CTRMap DSi header signature. Has a `--verify-only` mode. |
| `tools/tests/test_finalize_white2_rom.py` | Synthetic host test for the above. |
| `tools/verify_white2upgrade_hashes.py`, `tools/verify_w2u_battle_*.py`, `tools/rpm_verify_*.py` | Module/RPM consistency checks, not ROM semantic checks. |

**Confirmed gaps:**

- Nothing checks the input ROM hashes.
- Nothing audits ARM9 module params or `compressedStaticEnd`.
- Nothing checks that development plugins are absent.
- Nothing decodes and checks encounter, personal or text content in the finished ROM.
- No machine-readable build manifest covers inputs, toolchain and output.

The finalizer validates container layout only.

## 5. Licenses and redistribution constraints

| Component | License status (confirmed by file presence) |
| --- | --- |
| W2U-Project as a whole | **No top-level LICENSE, COPYING or NOTICE file.** Default copyright applies, so no permission to copy or redistribute is granted. |
| `include/extlib` | GPL-3.0 (`include/extlib/LICENSE`). |
| `tools/nitrogfx` | MIT, © 2015 YamaArashi. |
| `include/NitroKernel` | Vendored; no license file found in the vendored tree. Upstream repository not checked in this ticket. |
| `include/swan` | Submodule; not fetched and license not checked in this ticket. |
| `tools/CTRMap/CTRMapV-dirty.jar` | Binary with modifications; no license or source shipped with it. |
| PMC binaries (`pmc/*.bin`, `.rpm`) | No license file. |

**Confirmed: upstream tracks material that this project's rules forbid importing or redistributing:**

- `dump/`: 21,367 files that `docs/data-dumpers.md` says are dumped "from an extracted, unmodified White 2 ROM" (personal, learnsets, trainers, items, sprites, text).
- `vfs/data/a/0/0/4` (28,281 files) and other `vfs/data/a/**` members: staged NARC members, including graphics.
- `pmc/ARM9PMC.bin` and `vfs/y9.bin`: a full patched ARM9 image and an overlay table derived from game code.
- `data/**` TOML/`.bin`/`.png`: game-data tables and graphics edited from dumped originals.

**Consequences:**

1. Importing upstream source into this repository needs a license grant that does not exist today. Until upstream adds a license or the authors give written permission, **no upstream file may be copied here**. That includes the "small" Python tools such as `finalize_white2_rom.py`.
2. Upstream must stay an **external dependency, fetched by the user locally** at the pinned commit. It must never be redistributed with this project, and never fetched with `dump/`/`vfs/`/`pmc/` content into any cloud environment beyond read-only audit. (This audit did fetch it into a disposable cloud scratch directory to read it. Nothing was copied out.)
3. The **current repository safety guard would not catch** much of this material if it were committed by accident. It blocks `arm9.bin`, `*.narc` and `*.dll`, but not extensionless NARC members (`vfs/data/a/0/0/4/123`), `pmc/ARM9PMC.bin`, `dump/**.toml`, `dump/**.bin` or `vfs/**`. Section 7 proposes closing this gap.

## 6. Import vs. external dependency

| Category | Decision | Reason |
| --- | --- | --- |
| Any upstream file (code, scripts, tests, docs) | **External, pinned, not imported** | No license grant (5.1). |
| `include/extlib` (GPL-3.0) | External | Importing would make this repo GPL-3.0-bound. That is a product-owner decision, and no license is chosen yet. |
| `tools/nitrogfx` (MIT) | External unless needed | Importable with attribution if ever needed. Not needed for Phase 0 recovery. |
| CTRMap jar, PMC binaries | External, hash-pinned | Binary, unlicensed, game-derived (PMC ARM9). |
| `dump/`, `vfs/`, `data/` content, `pmc/*.bin` | **Never imported, never cached in cloud** | Proprietary game data or derived from it. |
| Format knowledge (ARM9 module params, BLZ footer, NDS header/FAT, NARC, encounter layout) | **Clean-room project code** | Facts and file formats; reimplement from public format specs and our own tests. Do not copy upstream code. |

Project-original code (hash gates, manifest writer, ARM9 audit, VFS plugin-exclusion check, strict NARC reader, encounter codec, finished-ROM verifier) should be written fresh in this repository, with synthetic tests.

## 7. Proposed minimal recovery scaffold (for authorization; not implemented)

Design principles: the upstream checkout and every ROM-derived artifact live outside the repository in a user-local work root; the repository holds only original code, pins and tests; every step is hash-checked and recorded.

```
poke-rando/
  pins/
    upstream.json        # repo URL, commit, submodule commits, CTRMap jar SHA-256,
                         # PMC binary SHA-256s, Meson version+commit, python dep pins
    inputs.json          # approved IRDO/IREO SHA-256s (the two already documented)
  requirements.lock      # exact versions for Pillow/ndspy/PyYAML (+hashes)
  b2er/                  # original Python package (stdlib-first)
    provenance.py        # sha256 streaming, input gate, before/after immutability check
    workroot.py          # disposable work root layout, cache keys, cache validation
    manifest.py          # machine-readable build manifest (inputs, pins, toolchain, outputs, hashes)
    nds/header.py        # NDS header/FAT/FNT reader (read-only)
    nds/arm9.py          # module-param locator, BLZ footer check, compressedStaticEnd audit
    nds/narc.py          # strict NARC reader (BTAF/BTNF/GMIF bounds, exact member sizes)
    data/encounter.py    # strict encounter codec: fixed slot counts, range checks, exact size
    verify/plugins.py    # staged-VFS + finished-ROM development-plugin exclusion
  scripts/
    check-repository-safety.sh   # extended (see below)
    b2er-build.sh        # user-local orchestrator: gate → clean upstream checkout → build → audit → manifest
  tests/
    synthetic/           # generated-in-test fixtures only; zero ROM bytes
```

**Hash gates and immutability.** Hash both clean ROMs before the build and again after it. Abort if either changes or does not match `pins/inputs.json`. Build from a copy or a read-only open; never from the user's path in place.

**Dependency pinning.** Pin the upstream commit, the submodule commit and the Meson commit (not just the tag). Pin the SHA-256 of the CTRMap jar and the PMC binaries. Pin the Python deps with hashes. Verify every pin before configuring.

**Disposable caches.** Create a fresh clone or `git worktree` of upstream at the pinned commit for every clean build, then `git clean -xdf` it. Provenance does not depend on the source-tree `vfs/` from earlier runs. Cache key = SHA-256 over (pins, input hashes, project patch set, toolchain versions). A cache whose key file does not match is deleted, never reused.

**Manifest and output hashes.** Write `manifest.json` next to each output, listing:

- tool versions;
- pins;
- input hashes (only the approved values, never other ROM identifiers);
- the project patch-set hash;
- the output ROM SHA-256 and size;
- the verifier results.

The manifest contains no ROM bytes.

**Finished-ROM verification (structural only, separate from runtime acceptance):**

- Header CRCs and FAT bounds, via a reimplementation and not by copying the finalizer.
- ARM9 `compressedStaticEnd` consistent with the stored ARM9's compression state, located through the module-param markers.
- `MainMenuSkip(1).dll` absent from the staged VFS and the finished FNT.
- The encounter archive decoded member by member, with exact sizes.

**Synthetic tests.** These run in CI with no ROM:

- Build fake NDS headers, ARM9 images with synthetic module params and BLZ footers, NARCs and encounter records in-test.
- Assert that the auditors accept good inputs and reject each known failure mode:
  - stale `compressedStaticEnd`;
  - plugin present;
  - integer-species/form swap;
  - species > 2047;
  - wrong slot count;
  - shifted NARC member index;
  - wrong record size.

**Safety-guard extension.** Add `dump/`, `vfs/`, `pmc/` and `IRDO_Extracted*/` path blocks. Add a content check that rejects any tracked file starting with NARC (`NARC`), Nitro graphics magics, or a DS header (game code at `0x0C`). Add a size cap for binary files.

**Proposed implementation order:**

| Ticket | Contents |
| --- | --- |
| R-002 | Safety-guard extension + `pins/` + provenance/manifest modules + synthetic tests. |
| R-003 | NDS header/ARM9 auditor + plugin-exclusion verifier + synthetic tests for both confirmed boot defects. |
| R-004 | Strict NARC reader + encounter codec + fixture-contract verifier. |
| R-005 | User-local clean-build orchestrator around the pinned external upstream. Applies the two boot repairs as project-side post-steps (delete the plugin from the disposable VFS; zero or recompute `compressedStaticEnd`), not as edits to upstream files. |

## 8. Open questions for Codex/Amelie

1. **License:** may we ask the W2U authors for a license or written permission? Until then, the plan assumes no upstream file is imported.
2. **GPL-3.0 exposure:** is this project willing to become GPL-3.0 if ExtLib-derived code is ever linked into shipped artifacts? (No action is needed for R-002 to R-004.)
3. **#1024/#1025 ID allocation:** still unresolved. It is confirmed at the pinned commit (`SPECIES_EGG = 1024`, `SPECIES_BAD_EGG = 1025`). Any resolution changes save format and species ID allocation, so it needs an explicit architecture decision.
4. **Route 19 member index:** confirm that `129.toml` is Route 19 from the zone table (needs a local, user-side lookup) before the fixture is rebuilt.
5. **Distribution model:** because upstream cannot be redistributed, the user-facing tool must fetch upstream at the pinned commit on the user's machine. Is that acceptable?
