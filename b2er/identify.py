"""Read-only ROM identification with layered evidence.

Layers, in order of authority:
1. exact SHA-256 of an approved clean ROM (pins/inputs.json) — authoritative;
2. platform container header and game code / internal title;
3. header integrity (checksums/CRCs) and revision byte;
4. structural fingerprints (e.g. NDS file system parses);
5. adapter-specific validation (performed by the selected adapter).

A ROM that matches a supported title's header but not an approved hash is
reported as ``recognized_modified``: it is never claimed to be a clean revision.
Filenames are never consulted.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
FINGERPRINTS = PACKAGE / "fingerprints.json"
DEFAULT_PINS = PACKAGE.parent / "pins" / "inputs.json"

CLEAN_KNOWN = "clean_known"
RECOGNIZED_MODIFIED = "recognized_modified"
RECOGNIZED_UNSUPPORTED = "recognized_unsupported"
UNKNOWN = "unknown"
READ_LIMIT = 0x8000  # header probes never need more than this


@dataclass
class Header:
    platform: str
    container: str
    game_code: str | None = None
    title: str | None = None
    maker: str | None = None
    revision: int | None = None
    integrity: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)


@dataclass
class Identity:
    status: str
    sha256: str
    size: int
    header: Header | None
    fingerprint: dict | None = None
    region: dict | None = None
    adapter: str | None = None
    adapter_confidence: str | None = None
    evidence: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        fp = self.fingerprint or {}
        h = self.header
        return {
            "status": self.status,
            "sha256": self.sha256,
            "size_bytes": self.size,
            "platform": h.platform if h else None,
            "container": h.container if h else None,
            "game_code": h.game_code if h else None,
            "internal_title": h.title if h else None,
            "maker": h.maker if h else None,
            "revision": h.revision if h else None,
            "header_integrity": h.integrity if h else {},
            "franchise": "Pokémon" if fp else None,
            "fingerprint": fp.get("id"),
            "title": fp.get("title"),
            "version": fp.get("version"),
            "paired_version": fp.get("paired"),
            "generation": fp.get("generation"),
            "region": (self.region or {}).get("region"),
            "languages": (self.region or {}).get("languages", []),
            "adapter": self.adapter,
            "adapter_confidence": self.adapter_confidence,
            "evidence": self.evidence,
            "diagnostics": self.diagnostics,
        }


def _ascii(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


# --- platform probes (each returns a Header or None) ------------------------------

def probe_nds(head: bytes, size: int) -> Header | None:
    if len(head) < 0x200:
        return None
    logo_crc, header_crc = struct.unpack_from("<HH", head, 0x15C)
    if logo_crc != 0xCF56:  # fixed CRC of the boot logo region on every DS cartridge image
        return None
    code = head[0x0C:0x10]
    if not all(48 <= b <= 90 for b in code):
        return None
    header_ok = _crc16(head[:0x15E]) == header_crc
    unit = head[0x12]
    return Header("nds", "Nintendo DS cartridge image" + (" (DSi-enhanced header)" if unit & 0x02 else ""),
                  code.decode("ascii"), _ascii(head[0:12]), head[0x10:0x12].decode("ascii", "replace"), head[0x1E],
                  {"logo_crc": True, "header_crc": header_ok},
                  ["NDS logo CRC 0xCF56", f"NDS header CRC {'valid' if header_ok else 'INVALID'}"])


def probe_gba(head: bytes, size: int) -> Header | None:
    if len(head) < 0xC0 or head[0xB2] != 0x96:
        return None
    checksum = (-sum(head[0xA0:0xBD]) - 0x19) & 0xFF
    if checksum != head[0xBD]:
        return None
    code = head[0xAC:0xB0]
    return Header("gba", "Game Boy Advance cartridge image", code.decode("ascii", "replace"), _ascii(head[0xA0:0xAC]),
                  head[0xB0:0xB2].decode("ascii", "replace"), head[0xBC], {"header_checksum": True},
                  ["GBA fixed byte 0x96", "GBA header complement checksum valid"])


def probe_gb(head: bytes, size: int) -> Header | None:
    if len(head) < 0x150:
        return None
    checksum = 0
    for b in head[0x134:0x14D]:
        checksum = (checksum - b - 1) & 0xFF
    if checksum != head[0x14D] or size < 0x8000:
        return None
    cgb = head[0x143] in (0x80, 0xC0)
    title_end = 0x13F if cgb else 0x144
    code = _ascii(head[0x13F:0x143]) if cgb and all(65 <= b <= 90 for b in head[0x13F:0x143]) else None
    return Header("gb", "Game Boy Color cartridge image" if cgb else "Game Boy cartridge image", code,
                  _ascii(head[0x134:title_end]), None, head[0x14C], {"header_checksum": True},
                  ["GB header checksum valid"])


def probe_3ds(head: bytes, size: int) -> Header | None:
    if len(head) < 0x200 or head[0x100:0x104] != b"NCSD":
        return None
    part0_offset = struct.unpack_from("<I", head, 0x120)[0] * 0x200
    product = None
    if part0_offset + 0x160 <= len(head) and head[part0_offset + 0x100 : part0_offset + 0x104] == b"NCCH":
        product = _ascii(head[part0_offset + 0x150 : part0_offset + 0x160])
    code = product.split("-")[-1] if product and product.count("-") == 2 else None
    return Header("3ds", "Nintendo 3DS card image (NCSD)", code, product, None, None, {},
                  ["NCSD magic", f"NCCH product code {product}" if product else "NCCH product code unreadable"])


def probe_gamecube(head: bytes, size: int) -> Header | None:
    if len(head) < 0x60 or struct.unpack_from(">I", head, 0x1C)[0] != 0xC2339F3D:
        return None
    return Header("gamecube", "GameCube disc image", head[0:4].decode("ascii", "replace"), _ascii(head[0x20:0x60]),
                  head[4:6].decode("ascii", "replace"), head[7], {}, ["GameCube disc magic"])


def probe_switch(head: bytes, size: int) -> Header | None:
    if len(head) >= 0x104 and head[0x100:0x104] == b"HEAD":
        return Header("switch", "Nintendo Switch card image (XCI)", evidence=["XCI HEAD magic; contents are encrypted"])
    if head[:4] == b"PFS0":
        return Header("switch", "Nintendo Switch package (NSP)", evidence=["PFS0 magic; contents are encrypted"])
    return None


PROBES = (probe_nds, probe_3ds, probe_gba, probe_gamecube, probe_switch, probe_gb)


# --- fingerprints -----------------------------------------------------------------

def load_fingerprints(path: Path = FINGERPRINTS) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [t["id"] for t in data["titles"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate fingerprint ids")
    for t in data["titles"]:
        if ("code_prefix" in t) == ("title_prefix" in t):
            raise ValueError(f"fingerprint {t['id']} needs exactly one of code_prefix/title_prefix")
    return data


def load_approved(pins: Path = DEFAULT_PINS) -> dict[str, dict]:
    data = json.loads(Path(pins).read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in data["inputs"]}


def match_title(header: Header, fingerprints: dict) -> tuple[dict | None, dict | None]:
    for t in fingerprints["titles"]:
        if t["platform"] != header.platform:
            continue
        if "code_prefix" in t and header.game_code and header.game_code.startswith(t["code_prefix"]):
            region = fingerprints["regions"].get(header.game_code[3:4])
            return t, region
        if "title_prefix" in t and header.title and header.title.startswith(t["title_prefix"]):
            return t, None
    return None, None


def identify(path: Path, *, adapters=None, pins: Path = DEFAULT_PINS, fingerprints_path: Path = FINGERPRINTS) -> Identity:
    """Identify a ROM without modifying it. ``adapters`` is a registry (see b2er.adapters)."""
    digest = hashlib.sha256()
    head = b""
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(1 << 20):
            if len(head) < READ_LIMIT:
                head += chunk[: READ_LIMIT - len(head)]
            digest.update(chunk)
            size += len(chunk)
    sha = digest.hexdigest()
    header = None
    for probe in PROBES:
        header = probe(head, size)
        if header:
            break
    identity = Identity(UNKNOWN, sha, size, header)
    if header is None:
        identity.diagnostics.append("no supported platform header matched (NDS, 3DS, GBA, GB/GBC, GameCube, Switch)")
        return identity
    identity.evidence += header.evidence
    fingerprints = load_fingerprints(fingerprints_path)
    fp, region = match_title(header, fingerprints)
    if fp is None:
        identity.diagnostics.append(f"{header.container} with game code {header.game_code!r} / title {header.title!r} "
                                    "is not a known Pokémon title")
        return identity
    identity.fingerprint, identity.region = fp, region
    identity.evidence.append(f"header matches fingerprint {fp['id']}")
    approved = load_approved(pins)
    exact = None
    for entry in approved.values():
        if entry["sha256"] == sha:
            exact = entry
    adapter = adapters.for_fingerprint(fp["id"]) if adapters else None
    if exact is not None:
        if fp.get("approved_input") != exact["id"] or header.game_code != exact["game_code"]:
            identity.status = RECOGNIZED_MODIFIED
            identity.diagnostics.append("approved hash matched but the header disagrees with its pin; refusing")
            return identity
        identity.evidence.append(f"SHA-256 equals the approved clean {exact['id']} ({exact['game_code']})")
        identity.status = CLEAN_KNOWN
    elif adapter is not None and fp.get("approved_input"):
        identity.status = RECOGNIZED_MODIFIED
        identity.diagnostics.append(f"header identifies {fp['title']}, but the SHA-256 is not an approved clean revision "
                                    "(modified, trimmed, patched, or another revision); it will not be built")
    else:
        identity.status = RECOGNIZED_UNSUPPORTED
    if adapter is None:
        identity.diagnostics.append(f"{fp['title']} (Generation {fp['generation']}) is recognized but has no adapter yet")
        if identity.status == CLEAN_KNOWN:
            identity.status = RECOGNIZED_UNSUPPORTED
        return identity
    identity.adapter = adapter.id
    identity.adapter_confidence = "exact-hash" if identity.status == CLEAN_KNOWN else "header-only"
    return identity
