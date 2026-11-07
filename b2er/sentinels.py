"""Evidence scanner for species-ID sentinels (Egg 1024 / Bad Egg 1025) in a local ARM9 image.

Purpose: the #1024/#1025 decision (docs/species-id-model.md) requires every
native site that treats 1023/1024/1025/1026 specially to be found before the
Egg and Bad Egg records can be relocated. Those constants live in W2U's
prebuilt ``pmc/ARM9PMC.bin`` (no source). This tool lists *candidate* sites
for review against symbols and a debugger. It reads the user's local files,
prints addresses and symbol names only, and never copies image bytes.

Candidates:
* 4-byte-aligned literal-pool words equal to a watched value;
* Thumb ``movs rD, #1`` + ``lsls rD, rD, #10`` (1024), optionally + ``adds rD, #1`` (1025);
* Thumb ``movs rD, #imm`` + ``lsls`` sequences producing a watched value.
"""

from __future__ import annotations

import bisect
import re
import struct
from dataclasses import dataclass

WATCHED = {1023: "max ordinary species", 1024: "Egg", 1025: "Bad Egg", 1026: "first form record"}


@dataclass(frozen=True)
class Candidate:
    address: int
    kind: str
    value: int
    symbol: str | None


def load_symbols(text: str, segment: int = 0) -> list[tuple[int, str]]:
    symbols = []
    name = None
    seg = None
    for line in text.splitlines():
        if m := re.fullmatch(r"\s*- Name: (\S+)", line):
            name, seg = m.group(1), None
        elif m := re.fullmatch(r"\s*Segment: (0x[0-9a-fA-F]+|\d+)", line):
            seg = int(m.group(1), 0)
        elif (m := re.fullmatch(r"\s*Address: (0x[0-9a-fA-F]+|\d+)", line)) and name is not None:
            if seg in (None, segment):
                symbols.append((int(m.group(1), 0) & ~1, name))
            name = None
    return sorted(symbols)


def _symbol(symbols: list[tuple[int, str]], address: int) -> str | None:
    index = bisect.bisect_right([a for a, _ in symbols], address) - 1
    if index < 0:
        return None
    base, name = symbols[index]
    return f"{name}+0x{address - base:x}"


def scan(arm9: bytes, base: int = 0x02000000, symbols: list[tuple[int, str]] = ()) -> list[Candidate]:
    found: list[Candidate] = []
    for offset in range(0, len(arm9) - 3, 4):
        value = struct.unpack_from("<I", arm9, offset)[0]
        if value in WATCHED:
            found.append(Candidate(base + offset, "literal word", value, _symbol(symbols, base + offset)))
    halfwords = [struct.unpack_from("<H", arm9, o)[0] for o in range(0, len(arm9) - 1, 2)]
    for i, hw in enumerate(halfwords[:-1]):
        if hw >> 11 != 0b00100:  # movs rD, #imm8
            continue
        rd, imm = (hw >> 8) & 7, hw & 0xFF
        nxt = halfwords[i + 1]
        if nxt >> 11 == 0 and (nxt & 7) == rd and ((nxt >> 3) & 7) == rd:  # lsls rD, rD, #imm5
            value = imm << ((nxt >> 6) & 0x1F)
            kind = "thumb movs+lsls"
            if i + 2 < len(halfwords):
                after = halfwords[i + 2]
                if after >> 11 == 0b00110 and (after >> 8) & 7 == rd:  # adds rD, #imm8
                    value += after & 0xFF
                    kind = "thumb movs+lsls+adds"
            if value in WATCHED:
                address = base + i * 2
                found.append(Candidate(address, kind, value, _symbol(symbols, address)))
    return found


def report(candidates: list[Candidate]) -> str:
    lines = [f"{len(candidates)} candidate species-sentinel sites (review each against the disassembly and a debugger):"]
    for c in candidates:
        lines.append(f"  0x{c.address:08x}  {c.kind:22}  {c.value:5} ({WATCHED[c.value]})  {c.symbol or '?'}")
    return "\n".join(lines) + "\n"
