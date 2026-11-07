# Alpha 0.1 — build, run, and first-play guide

Alpha 0.1 is the first integrated, locally buildable candidate. It runs the full core loop in one command:

- expanded engine (W2U);
- deterministic starter, wild, trainer and item randomization;
- obtainability and evolution normalization;
- progression constraints;
- major-battle Doubles;
- finished-ROM verification.

It is **not runtime-accepted**. Each build reports three separate states: *build completed*, *structural verification passed*, *runtime acceptance not yet performed*.

## Host prerequisites (checked together before any work starts)

- Linux or macOS shell, Git, Python 3.11+ with `Pillow`, `ndspy`, `PyYAML` (W2U's own requirements)
- A JDK (`java` on `PATH`, or `--java` / `JAVA=`)
- `ninja`, and the Arm GNU toolchain: `arm-none-eabi-gcc`, `g++`, `as`, `ld`, `objcopy`, `nm`, `readelf`
- Network access once per build to fetch Meson at the pinned commit `897b6fcd` (or pass `--meson-source` pointing at a local Meson clone)
- Your clean ROMs:

  | ROM | Game code | SHA-256 |
  | --- | --- | --- |
  | White 2 | `IRDO` | `3e50aec3…7837446` |
  | Black 2 | `IREO` | `2e6b2415…f297683a` |

- A clean W2U-Project checkout at `9489aebf…`, with swan initialized: `git clone --recurse-submodules https://github.com/hzla/W2U-Project && git -C W2U-Project checkout 9489aebf42a870fa6a60816c273b0aa22fe5c1fe && git -C W2U-Project submodule update --init`. Do not run `make meson` or other setup inside it; the verifier requires a pristine checkout.
- For play: melonDS 1.1 (Flatpak `net.kuribo64.melonDS`); optional `gdb-multiarch`.

## Identify, inspect capabilities, build

```sh
./b2er.sh identify /path/to/cleanwhite2.nds        # read-only identification report
./b2er.sh capabilities                             # adapter capability matrix

./Build-Alpha-0.1.sh \
  --white2 /path/to/cleanwhite2.nds \
  --black2 /path/to/cleanblack2.nds \
  --w2u /path/to/W2U-Project \
  --seed "example-seed" \
  --species-max 1023 \
  [--settings settings.example.json]
```

`--species-max 1023` is required for White 2 Alpha 0.1. The pinned engine cannot represent #1024/#1025, and the build refuses to reduce the range silently (see `docs/species-id-model.md`). The reduction is recorded in the manifest.

Diagnostic isolation build (Route 19 Patrat #504 L5 / Carbink #703 L6 / Chespin #650 L7, weighted 35/35/30, all four seasons, nothing else changed):

```sh
./Build-Alpha-0.1.sh --white2 ... --black2 ... --w2u ... --seed diagnostic --species-max 1023 --diagnostic-route19
```

What the command does, in order:

1. **Identification and preflight.** The ROM is identified (it must be the approved clean White 2), the adapter is selected, and requested capabilities are checked. Every problem is aggregated and reported together. This covers:
   - both ROMs verified against `pins/inputs.json`;
   - the W2U checkout verified against `pins/upstream.json` (clean, exact HEAD, pinned binaries, swan initialized and pinned);
   - settings validated;
   - host tools present.
2. **Workroot.** A unique disposable folder under `.alpha-work/`.
3. **Staging.** W2U and swan are staged from Git objects (no checkout, filters or hooks). The ROMs are copied into the workroot read-only, and the clean White 2 file system is extracted as `IRDO_Extracted/`.
4. **Project patches**, each checked against its pinned pre-image:
   - every `MainMenuSkip(1).dll` is deleted and its Meson input removed;
   - a stale ARM9 `compressedStaticEnd` in the staged prebuilt ARM9 is cleared;
   - `White2Upgrade.cmproj` is written.
5. **Generation.** Deterministic edits to W2U's TOML sources (see `docs/randomizer.md`).
6. **Assembly.** W2U's Meson/Ninja/CTRMap build runs in the workroot.
7. **Post-build:**
   - the plugin's absence is asserted;
   - the ARM9 compression state is re-audited and repaired if stale;
   - script-level starter, gift, static and item patches are applied;
   - header CRCs and the signature are refreshed.
8. **Verification.** Header, CRCs and ARM9 state are checked. Encounters and trainers are decoded and compared with independent encodings of the plan. The Doubles conversions, script patches and the Route 19 fixture (in diagnostic mode) are checked.
9. **Inputs re-verified.** The clean ROMs must be byte-identical, with the same file identity, as before the build.
10. **Outputs written** to `Alpha-0.1-builds/` (gitignored), and the workroot is removed. A failed build keeps it for diagnosis.

## Outputs (`Alpha-0.1-builds/`)

| File | Content |
| --- | --- |
| `<label>.nds` | the ROM (local only; never commit or share) |
| `<label>.manifest.json` | detected identity, adapter and version, species codec, seed, settings, tool/dependency versions, provenance, capabilities used (supported/experimental), unsupported capabilities, transformations, output SHA-256, verification states, warnings |
| `<label>.plan.json` | machine-readable randomization plan, including script patches |
| `<label>.spoiler.txt` | human-readable spoiler log |
| `<label>.settings.json` | normalized settings |
| `<label>.verification.json` | every structural/semantic check |

None of these contain ROM bytes or absolute local paths; the pipeline enforces this. `<label>` is `gen5-white2-<build key prefix>` (`gen5-white2-diagnostic-...` for diagnostic builds).

## Launch and smoke test

```sh
./Launch-Alpha-0.1.sh Alpha-0.1-builds/<label>.nds              # play
./Launch-Alpha-0.1.sh Alpha-0.1-builds/<label>.nds --smoke 90   # automated boot-log smoke (not acceptance)
./Launch-Alpha-0.1.sh Alpha-0.1-builds/<label>.nds --gdb        # ARM9 GDB capture; enable melonDS's GDB stub (port 3333) first
```

Logs go to `emulator-logs/` (gitignored).

## Smallest first-play checklist

1. Build with `--diagnostic-route19`, start a **New Game**, and confirm the title and New Game path work.
2. On Route 19, encounter all three species. For each, record the species shown, the level (5/6/7), the name in battle, the capture text after catching, and the summary screen.
3. Open the Pokédex, then its habitat and list views, and confirm nothing is corrupt and there is no `ARM9: data abort`.
4. Save, close melonDS completely, reopen, and **Continue**. Confirm the party and the Pokédex entries persisted.
5. Build a randomized ROM with a seed. Confirm the three starters shown and received match the spoiler log. Fight the first rival (Singles, by design) and Cheren's gym (Doubles). Note any trainer or wild species/level/name anomaly.

## Known limits

See `docs/randomizer.md`, "Known defects and risks", and `docs/species-id-model.md`.
