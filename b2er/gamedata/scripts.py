"""Generation V event-script container parsing and fail-closed disassembly.

Container: a header of ``u32`` relative offsets (each relative to the end of its
own field), terminated by ``u16 0xFD13``. Each entry is a script; commands are a
``u16`` opcode followed by operands.

Opcode metadata (names, operand types, flow kinds) is *not* stored in this
repository. :func:`load_opcode_table` reads it at build time from the
user's pinned local CTRMap SDK description (see b2er/assembly/w2u_sdk.py), or tests
pass a synthetic table.

Safety rule: disassembly follows control flow from every entry point and stops
a path at any unknown opcode or any operand whose size is not known exactly.
A patch site is only reported when its command was decoded on a fully known
path, so the scan can miss sites but does not fabricate them.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from ..nds import FormatError

HEADER_TERMINATOR = 0xFD13
OPERAND_SIZES = {"ushort": 2, "ref ushort": 2, "const ushort": 2, "fx16": 2, "byte": 1, "int": 4, "fx32": 4}
VARIABLE_MIN = 0x4000  # operand values at or above this are script variables, not constants


@dataclass(frozen=True)
class Operand:
    name: str
    type: str


@dataclass(frozen=True)
class Opcode:
    code: int
    name: str
    operands: tuple[Operand, ...]
    flow: str = ""  # "", "Jump", "Call", "End", "Return", "CallActionSeq"
    conditional: bool = False

    @property
    def size(self) -> int | None:
        total = 2
        for operand in self.operands:
            size = OPERAND_SIZES.get(operand.type)
            if size is None:
                return None
            total += size
        return total

    def operand_offset(self, name: str) -> int:
        offset = 2
        for operand in self.operands:
            if operand.name == name:
                return offset
            size = OPERAND_SIZES.get(operand.type)
            if size is None:
                raise FormatError(f"{self.name}.{name} follows an operand of unknown size")
            offset += size
        raise KeyError(name)


def load_opcode_table(text: str) -> dict[int, Opcode]:
    """Parse the SDK command description (a small, regular YAML subset)."""
    table: dict[int, Opcode] = {}
    current: dict | None = None

    def flush() -> None:
        if current is not None and current.get("name"):
            table[current["code"]] = Opcode(current["code"], current["name"], tuple(current["operands"]),
                                            current.get("flow", ""), current.get("conditional", False))

    for raw in text.splitlines():
        line = raw.rstrip()
        match = re.fullmatch(r"(0x[0-9a-fA-F]+):", line)
        if match:
            flush()
            current = {"code": int(match.group(1), 16), "operands": []}
            continue
        if current is None:
            continue
        if m := re.fullmatch(r"  Name: (\S+)", line):
            current["name"] = m.group(1)
        elif m := re.fullmatch(r"    - Name: (\S+)", line):
            current["operands"].append([m.group(1), None])
        elif m := re.fullmatch(r"      Type: (.+)", line):
            if current["operands"] and current["operands"][-1][1] is None:
                current["operands"][-1][1] = m.group(1).strip()
        elif m := re.fullmatch(r"  CommandType: (\S+)", line):
            current["flow"] = m.group(1)
        elif re.fullmatch(r"  HasCondition: true", line):
            current["conditional"] = True
    flush()
    for code, op in list(table.items()):
        table[code] = Opcode(op.code, op.name, tuple(Operand(n, t or "?") for n, t in op.operands), op.flow, op.conditional)
    return table


def entry_points(data: bytes) -> list[int]:
    entries = []
    pos = 0
    while True:
        if pos + 2 > len(data):
            raise FormatError("script header is not terminated")
        if struct.unpack_from("<H", data, pos)[0] == HEADER_TERMINATOR:
            return entries
        if pos + 4 > len(data):
            raise FormatError("script header entry is truncated")
        rel = struct.unpack_from("<i", data, pos)[0]
        target = pos + 4 + rel
        if not 0 <= target < len(data):
            raise FormatError(f"script entry {len(entries)} points outside the container")
        entries.append(target)
        pos += 4
        if len(entries) > 4096:
            raise FormatError("script header is implausibly long")


@dataclass(frozen=True)
class Instruction:
    offset: int
    opcode: Opcode
    operands: tuple[int, ...]

    def operand(self, name: str) -> int:
        return self.operands[[o.name for o in self.opcode.operands].index(name)]


@dataclass
class Disassembly:
    instructions: dict[int, Instruction] = field(default_factory=dict)
    stopped: list[tuple[int, str]] = field(default_factory=list)


def disassemble(data: bytes, table: dict[int, Opcode]) -> Disassembly:
    result = Disassembly()
    pending = list(entry_points(data))
    seen: set[int] = set()
    code_start = min(pending) if pending else len(data)
    while pending:
        pos = pending.pop()
        while True:
            if pos in seen:
                break
            if not code_start <= pos <= len(data) - 2:
                result.stopped.append((pos, "out of bounds"))
                break
            seen.add(pos)
            code = struct.unpack_from("<H", data, pos)[0]
            op = table.get(code)
            if op is None:
                result.stopped.append((pos, f"unknown opcode 0x{code:x}"))
                break
            size = op.size
            if size is None:
                result.stopped.append((pos, f"{op.name} has an operand of unknown size"))
                break
            if pos + size > len(data):
                result.stopped.append((pos, f"{op.name} is truncated"))
                break
            values = []
            cursor = pos + 2
            for operand in op.operands:
                width = OPERAND_SIZES[operand.type]
                fmt = {1: "<B", 2: "<H", 4: "<i"}[width]
                values.append(struct.unpack_from(fmt, data, cursor)[0])
                cursor += width
            result.instructions[pos] = Instruction(pos, op, tuple(values))
            next_pos = pos + size
            if op.flow in ("Jump", "Call"):
                target = next_pos + values[-1]
                pending.append(target)
                if op.flow == "Jump" and not op.conditional:
                    break
            if op.flow in ("End", "Return"):
                break
            pos = next_pos
    return result


@dataclass(frozen=True)
class ConstantSite:
    """A constant operand that may be patched in place (same width)."""

    member: int
    command_offset: int
    opcode_name: str
    operand: str
    value_offset: int
    value: int
    context: tuple[tuple[str, int], ...]  # other operand values, for filtering (e.g. level)


def constant_sites(member: int, data: bytes, table: dict[int, Opcode], wanted: dict[str, str]) -> list[ConstantSite]:
    """Find constant operands named ``wanted[opcode_name]`` on fully decoded paths."""
    dis = disassemble(data, table)
    sites = []
    for offset in sorted(dis.instructions):
        ins = dis.instructions[offset]
        name = wanted.get(ins.opcode.name)
        if name is None:
            continue
        value = ins.operand(name)
        if value >= VARIABLE_MIN:
            continue
        context = tuple((o.name, v) for o, v in zip(ins.opcode.operands, ins.operands) if o.name != name)
        sites.append(ConstantSite(member, offset, ins.opcode.name, name, offset + ins.opcode.operand_offset(name), value, context))
    return sites


def patch_u16(data: bytearray, site: ConstantSite, value: int, table: dict[int, Opcode]) -> None:
    if not 0 <= value < VARIABLE_MIN:
        raise FormatError(f"replacement {value} is not a constant operand")
    current = struct.unpack_from("<H", data, site.value_offset)[0]
    if current != site.value:
        raise FormatError(f"site at 0x{site.value_offset:x} no longer holds {site.value}")
    opcode = struct.unpack_from("<H", data, site.command_offset)[0]
    if table.get(opcode) is None or table[opcode].name != site.opcode_name:
        raise FormatError("patch site command changed")
    struct.pack_into("<H", data, site.value_offset, value)
