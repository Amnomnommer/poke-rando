# Pin records

Machine-readable identifiers for every external input the recovery build depends on. These files contain **identifiers and hashes only**, never external bytes.

| File | Contents |
| --- | --- |
| `inputs.json` | Approved clean ROM inputs (White 2 `IRDO`, Black 2 `IREO`) by SHA-256. |
| `upstream.json` | Pinned W2U commit, its submodule/vendored dependencies, required external binaries, and prohibited development plugins. |

## Conventions

- `schema_version` is an integer; bump it on any incompatible field change.
- SHA-256 values are 64 lowercase hex characters. Git commits and tag objects are 40 lowercase hex characters (`tag_object` records an annotated tag; `commit` is always the peeled commit).
- `path` values are relative to the root of the user's local W2U checkout, not to this repository.
- A value that has not been audited is never guessed. It is written as an explicit unresolved field:

  ```json
  { "status": "unresolved", "value": null, "reason": "why it is unknown" }
  ```

  A resolved field of the same shape uses `"status": "resolved"` with a non-null `value`.
- Every pinned value names its `evidence`, such as the audit section or the handoff document it came from.

## Policy

- W2U stays external. The recovery tool will verify a user-supplied local checkout against `upstream.json`. It does not clone upstream automatically, and nothing from upstream is copied here.
- Only the two approved clean-ROM hashes may appear in `inputs.json` (see `CONTRIBUTING.md`).
- `tests/test_pins.py` checks the structure and the approved values.
- `scripts/verify_w2u_checkout.py` verifies a local checkout against `upstream.json`; see [`docs/w2u-checkout-verifier.md`](../docs/w2u-checkout-verifier.md).
