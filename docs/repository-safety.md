# Repository safety guard

`scripts/check-repository-safety.sh` runs `scripts/repository_safety.py` (stdlib-only, original project code) against the **Git index**: the blobs that would be committed, not the working tree. Filenames are read NUL-delimited, so names containing spaces, newlines, or non-ASCII characters are checked exactly. Violations are printed with Python `repr()` so hidden characters stay visible.

Exit codes: `0` pass, `1` prohibited content found, `2` the check could not run.

## Rules

### Entry type

- Submodules (gitlinks) are rejected. An upstream tree must not be attached by reference.
- Symlinks are path-checked only. Their blob is a link-target string and is never followed.

### Path policy (case-insensitive)

| Rule | Values |
| --- | --- |
| Directory names, at any depth | `dump/`, `vfs/`, `pmc/` (upstream W2U trees, R-001 audit section 5); `NitroFS/`; `build/`, `build-*/`, `output/`, `outputs/`, `cache/`, `temp/`, `tmp/`, `user-data/` |
| Directory prefixes, at any depth | `IRDO_Extracted*/`, `IREO_Extracted*/` |
| Directory sequences | `tester/input/`, `tester/cache/`, `tester/output/`, `tester/results/` |
| Extensions | ROM/save/emulator/firmware/key: `nds srl sav dsv duc mch ml1–ml8 state bios firmware nand key keys`. Nitro: `narc ncgr nclr nscr ncer nanr nmcr nmar nftr sdat sseq ssar sbnk swar strm`. Code/dumps: `overlay dll rpm core` |
| Exact basenames | `header.bin arm7.bin arm9.bin y7.bin y9.bin banner.bin` |

The `vfs/` and `pmc/` rules cover extensionless staged NARC members such as `vfs/data/a/0/0/4/123` and prebuilt game-derived binaries such as `pmc/ARM9PMC.bin`, which the extension rules alone miss.

### Content policy (regular files)

| Check | Rejected when |
| --- | --- |
| Nitro magic | The first 4 bytes are any of `NARC NCGR NCLR NSCR NCER NANR NMCR NMAR NFTR SDAT SSEQ SSAR SBNK SWAR STRM`, in either byte order. Nitro resources store the identifier reversed on disk (`RGCN` for NCGR). |
| DS ROM header | Bytes `0x0C..0x0F` are `IRDO` (White 2) or `IREO` (Black 2), the header game-code field. |
| Binary size | The file is binary (NUL byte or invalid UTF-8 in the first 8 KiB) and larger than **64 KiB**, and is not allowlisted. |

**Why 64 KiB.** It is far below any ROM, NARC, overlay or graphics bank of concern, and far above any reasonable hand-written project binary. Tests generate their binary fixtures at runtime instead of committing them. A legitimate project-original binary above the limit must be added to `scripts/repository-safety-allowlist.json` with its exact path, SHA-256, and a reason. A changed file no longer matches its entry and fails again.

## Known limits

- Detection is signature- and path-based. It cannot recognise game data that has been re-encoded, for example as text tables, base64, or LZ-compressed blobs below the size limit. Review remains required.
- Large **text** files are not size-limited. Dumped TOML is caught only through the `dump/` path rule.
- Git history is not scanned. Content removed from the index can still exist in earlier commits.

## Tests

`tests/test_repository_safety.py` builds throwaway Git repositories containing synthetic bytes only, and asserts each rule passes or fails as documented. Run locally:

```sh
bash scripts/check-repository-safety.sh
python3 -I -m unittest discover -s tests -v
```

CI runs both (`.github/workflows/repository-safety.yml`).
