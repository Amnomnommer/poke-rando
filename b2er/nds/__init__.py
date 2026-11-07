"""Original, clean-room Nintendo DS container tooling (header, FNT/FAT, overlays, ARM9, NARC, BLZ).

Implemented from public file-format facts. No ROM bytes are embedded; tests use
synthetic images generated at runtime.
"""

class FormatError(ValueError):
    """Structurally invalid or out-of-bounds data."""
