# ROM input verifier

`scripts/verify_rom_inputs.py` checks the two user-supplied clean ROM inputs against `pins/inputs.json`. It is read-only and offline, uses only the standard library, and is original project code. It never modifies, copies, uploads, locks, or prints ROM bytes.

```sh
python3 -I scripts/verify_rom_inputs.py --white2 "/path/to/White 2.nds" --black2 "/path/to/Black 2.nds"
python3 -I scripts/verify_rom_inputs.py --white2 ... --black2 ... --json
```

Both paths are required. There is no discovery, no default location, and no network access. ROMs must never be placed in this repository; the safety guard rejects them.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Both inputs verified. Warnings for unresolved pins may still be present. |
| `1` | Verification failure: wrong hash, game code, size (when resolved) or file type; duplicate identity; truncated header; changed during read. |
| `2` | Operational error: invalid arguments, missing or unreadable path, or a missing or malformed pin file. |

## Checks per input

| Check | Defect category |
| --- | --- |
| Path is a regular file (not a symlink, directory, FIFO, socket or device) | `not_regular_file` |
| Streamed SHA-256 equals the approved hash for the role (`white2` / `black2`) | `wrong_hash`. The detail says when an input matches the *other* role, meaning the paths appear swapped. |
| Bytes `0x0C..0x0F` equal the role's game code (`IRDO` / `IREO`) | `wrong_game_code` |
| File has at least `0x10` bytes | `truncated_header` |
| Size equals the pin when `size_bytes` is resolved | `wrong_size` |
| Metadata, byte count and path identity are unchanged by the end of the read | `changed_during_read` |
| The two roles name different files (path, or device + inode for hard links) | `duplicate_identity` |

`size_bytes` is currently unresolved for both inputs. The observed size is reported as evidence, with an `unresolved_pin` warning, and is never written back to the pin.

## Safe reading

1. `lstat` the path. Anything other than a regular file is rejected without being opened.
2. Open it with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_NOCTTY | O_CLOEXEC`, plus `O_NOATIME` when the kernel allows it.
   - `O_NOFOLLOW` refuses a symlink swapped in after step 1.
   - `O_NONBLOCK` keeps a FIFO from blocking.
   - On platforms without a flag, it is omitted and steps 3 and 5 still fail closed.
3. `fstat` the descriptor. It must be a regular file with the same device and inode as step 1.
4. Stream SHA-256 from the descriptor in 1 MiB chunks. Only the 4 game-code bytes are inspected, and they are reported only when they form a well-formed code (`[A-Z0-9]{4}`).
5. Repeat `fstat` and `lstat`. Any change in device, inode, size, mtime or ctime, a byte count that differs from the size, or a path that now names another file, is reported as `changed_during_read`.

Nothing is ever written to an input or its directory. Access time may still update where `O_NOATIME` is not permitted.

## Reports are local-only

Text and JSON reports contain absolute input paths, device and inode numbers, and timestamps. They identify a local machine and are unsuitable for committed or published manifests. Every report carries a `local_only` notice.

## Library API (for a future build orchestrator)

```python
from b2er import rom_inputs

before = rom_inputs.verify_inputs(white2_path, black2_path, "pins/inputs.json")
# ... a future build step ...
after = rom_inputs.verify_inputs(white2_path, black2_path, "pins/inputs.json")
assert before.verified and after.verified
assert rom_inputs.compare_fingerprints(before, after) == []   # same files, same bytes, same metadata
```

- `verify_inputs()` returns an `InputVerification` (`verified`, `exit_code`, `inputs`, `defects`, `warnings`, `report()`, `fingerprint()`), or raises `OperationalError` for exit-code-2 conditions.
- `fingerprint()` covers role, path, SHA-256, size, device, inode, mtime and ctime. It detects a file replaced by identical bytes, which hash checks alone would miss.

This ticket does not implement the orchestrator.
