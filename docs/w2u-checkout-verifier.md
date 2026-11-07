# W2U checkout verifier

`scripts/verify_w2u_checkout.py` checks a **user-supplied local** W2U checkout against `pins/upstream.json`. It is read-only and offline, uses only the standard library, and is original project code. It never copies, modifies, builds, fetches, or executes upstream content.

```sh
python3 -I scripts/verify_w2u_checkout.py /path/to/W2U-Project          # human-readable report
python3 -I scripts/verify_w2u_checkout.py /path/to/W2U-Project --json   # machine-readable report
python3 -I scripts/verify_w2u_checkout.py /path/to/W2U-Project --pins other-pins.json
```

The checkout path is required. There is no default location and no network access.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Verified: every resolved pin matches and the checkout has no disallowed state. Warnings may still be present. |
| `1` | Verification failure: any defect below. |
| `2` | Operational error: invalid arguments, a path that does not exist or is not the top level of a Git worktree, an unreadable checkout, or a missing or malformed pin file. |

## What is checked

| Check | Failure category |
| --- | --- |
| `HEAD` is exactly `upstream.commit` | `wrong_commit` |
| `include/swan` in `HEAD` is a gitlink to the pinned commit | absent or not a gitlink: `missing_dependency`; other commit: `wrong_commit` |
| When swan is initialized, its checked-out `HEAD` is the pinned commit and its worktree is clean | `wrong_commit`; dirty-state categories, with paths prefixed `include/swan/` |
| Each resolved `external_binaries[].sha256` matches a regular file inside the checkout (symlinks rejected) | missing or not regular: `missing_dependency`; mismatch: `wrong_hash` |
| Index matches `HEAD` (`kind: staged`) and worktree matches index (`kind: worktree`): content, deletion, file mode, type | `tracked_modification` |
| Unmerged index entries | `conflicted_entry` |
| Untracked, non-ignored files | `untracked_file` |
| Ignored files, such as `build/`, `subprojects/` or staged `vfs/` output | `ignored_file` |

A dirty checkout fails, and that includes ignored files. The R-001 audit found that ignored, stale staging output under upstream's source-tree `vfs/` is the main cache-provenance risk, so a verified checkout must be a pristine clone at the pinned commit. Upstream's own `make meson` and `White2Upgrade.cmproj` produce ignored files, so the verifier should be run before upstream setup steps, against a fresh clone.

## Warnings (never verified facts)

| Category | Meaning |
| --- | --- |
| `unresolved_pin` | A pin field with `"status": "unresolved"`, identified by its JSON pointer, with the recorded reason. |
| `unverified_dependency` | A declared dependency this tool does not verify. Vendored ExtLib and NitroKernel trees, and Meson, keep their recorded evidence labels. |
| `dependency_not_initialized` | swan is not initialized, so its checked-out commit was not verified. |

An external binary whose hash is unresolved gets a `warn` check and is never reported as verified.

## Read-only and no-execution guarantees

- Only read-only Git plumbing runs: `rev-parse`, `ls-tree`, `ls-files`, `config --get`.
- Git runs with:
  - system and global config disabled;
  - `GIT_OPTIONAL_LOCKS=0`, so the index is never refreshed or rewritten;
  - `core.fsmonitor=false`;
  - hooks pointed at `/dev/null`;
  - the untracked cache off;
  - `protocol.allow=never`;
  - all inherited `GIT_*` variables removed;
  - `GIT_CEILING_DIRECTORIES` set to the parent of the given path, so a parent repository is never discovered.
- Worktree content is compared by computing Git blob IDs in Python (SHA-1 or SHA-256 object format). Git never applies clean or smudge filters, textconv drivers, or other programs configured by the checkout. `tests/test_w2u_checkout.py` plants such programs and asserts none runs.
- File contents are read only to compute hashes. Reports contain paths, categories and hashes, never contents.

## Known limits

- Checkouts made with `core.autocrlf` or content filters (such as Git LFS) differ from their blobs on disk, so they report `tracked_modification`. This fails closed.
- Ownership-protected repositories ("dubious ownership") are an operational error. The verifier does not override `safe.directory`.
- The JSON report includes the absolute checkout path. Keep reports local; they are not meant to be committed.
