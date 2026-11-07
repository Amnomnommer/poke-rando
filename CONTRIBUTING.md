# Contributing

## Safety boundary

Never commit or upload:

- Nintendo DS ROMs or ROM patches containing redistributed game data.
- Save files, emulator state, firmware, BIOS, NAND, keys, or console identifiers.
- Extracted NitroFS/overlay/NARC content or proprietary game assets.
- Generated ROMs, build caches, debugger dumps, or runtime captures containing game bytes.
- Clean-ROM hashes other than the approved identifiers already documented.

Run `scripts/check-repository-safety.sh` before every commit. The CI workflow runs the same guard and its tests. Rules, thresholds, and the binary allowlist are documented in [`docs/repository-safety.md`](docs/repository-safety.md); pinned external identifiers live in [`pins/`](pins/README.md).

## Engineering rules

- Pin external revisions and verify input hashes locally.
- Never modify a clean ROM in place.
- Keep build caches disposable and outside version control.
- Separate structural verification from runtime acceptance.
- Add a regression test before a repair when technically feasible.
- Use clean builds for native/runtime code, overlays, injection, build-system changes, milestones, and releases.
- Do not begin stretch goals while core acceptance gates remain open.
- Do not claim reconstruction is identical to lost `.0873` source without evidence.

## Change report

Implementation changes must report outcome, evidence versus inference, files changed, exact tests and results, build type, artifact hashes without ROM distribution, risks, the smallest requested manual test, and the recommended next ticket.
