# Runtime acceptance log

Runtime results reported by the product owner from melonDS. Structural verification (automated, in
the build) and runtime acceptance (a person playing the ROM) are recorded separately; a build is not
"runtime-validated" for anything not listed as passed here.

## 2026-11-08 — White 2 diagnostic Route 19, main `90a1cc14783b361c10e7777605fcc69a01e486eb`

Build: `./Build-Alpha-0.1.sh ... --species-max 1023 --diagnostic-route19` (starters, trainers and
wild encounters vanilla W2U passthrough; Route 19 diagnostic fixture applied). New save.

### Passed

- Route 19 resolves through the clean ROM's zone table: zone 437 -> encounter record 128
  (`b2er/gamedata/zones.py`). Patrat L5, Carbink L6 and Chespin L7 appeared.
- PR #19 player-name fix: throwing a Poke Ball shows "A used the Poke Ball!" (previously corrupted
  because W2U's wild-setup hook overwrote the player-info pointer at setup+0x34).
- Capturing works. Running from a wild battle works.
- Carbink: species #703, correct cry, level, Route 19 met data, OT A.
- Chespin: species #650, level 7, Route 19 met data, OT A.
- The earlier erroneous Chespin Egg history did not reproduce.
- No visible crash.

### Known defects (not fixed)

- Chespin plays Hypno's cry. Low-priority cosmetic.
- Choosing Tepig made the first rival battle use Carbink. This is inconsistent W2U trainer data passed
  through unchanged; diagnostic mode does not randomize trainers.

### Not tested

- Save/reload, PC deposit/withdraw and Pokedex persistence for the captured Chespin: the session closed
  without saving after the capture.

### Earlier results on the same diagnostic path (for context)

- Diagnostic ROM `ee123785...` (main `36ced1b`): Route 19 showed W2U's own record 128 (all Gible L100)
  because the old species heuristic had patched record 129 (zone 446, the next route); the Poke Ball and
  whiteout messages showed a corrupted player name. Both are fixed above. A level-50 Ferrothorn on the
  next route was a trainer (W2U `data/trainers/164.toml`), not a wild encounter.
