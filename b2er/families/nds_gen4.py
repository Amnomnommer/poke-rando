"""Nintendo DS Generation IV family: shares the NDS container and NARC layers with Generation V."""

from __future__ import annotations

from ..nds import FormatError
from ..nds.rom import Rom


def validate(data: bytes) -> list[str]:
    try:
        rom = Rom(data)
        rom.paths()
    except FormatError as error:
        return [f"NDS structure invalid: {error}"]
    return [f"header integrity: {p}" for p in rom.integrity_problems()]
