"""Synthetic tests for the NDS, NARC, BLZ, ARM9, encounter, trainer, and script codecs."""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2er.gamedata import encounters as enc, scripts, trainers as trn  # noqa: E402
from b2er.nds import FormatError, arm9, blz  # noqa: E402
from b2er.nds.narc import Narc  # noqa: E402
from b2er.nds.rom import Rom, build_rom, crc16  # noqa: E402


class RomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = {"a/0/1/6": b"personal", "a/1/2/7": b"encounters!", "patches/White2Upgrade.dll": b"plugin", "z.bin": b"z"}
        self.image = build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), files=self.files,
                               overlays=[(36, b"overlay 36", False), (167, b"overlay 167", True)])
        self.rom = Rom(self.image)

    def test_paths_and_lookup(self) -> None:
        self.assertEqual(set(self.rom.paths()), set(self.files))
        for path, data in self.files.items():
            self.assertEqual(self.rom.read_path(path), data)
        self.assertEqual(self.rom.game_code, "IRDO")
        with self.assertRaises(FormatError):
            self.rom.file_id("missing")

    def test_overlays(self) -> None:
        overlays = self.rom.overlays()
        self.assertEqual([(o.overlay_id, o.compressed) for o in overlays], [(36, False), (167, True)])
        self.assertEqual(self.rom.file_bytes(overlays[0].file_id), b"overlay 36")

    def test_integrity_detects_and_refreshes(self) -> None:
        self.assertEqual(self.rom.integrity_problems(), [])
        self.rom.data[0x4010] ^= 0xFF  # inside the secure area
        self.assertIn("secure_crc is stale", self.rom.integrity_problems())
        self.rom.refresh_integrity()
        self.assertEqual(self.rom.integrity_problems(), [])

    def test_crc16_known_value(self) -> None:
        self.assertEqual(crc16(b"123456789"), 0x4B37)  # CRC-16/MODBUS check value

    def test_replace_in_place_bounds(self) -> None:
        fid = self.rom.file_id("z.bin")
        self.rom.replace_file_in_place(fid, b"Z")
        self.assertEqual(self.rom.read_path("z.bin"), b"Z")
        with self.assertRaises(FormatError):
            self.rom.replace_file_in_place(self.rom.file_id("a/0/1/6"), b"x" * 0x10000)

    def test_out_of_bounds_fat_rejected(self) -> None:
        bad = bytearray(self.image)
        fat = struct.unpack_from("<I", bad, 0x48)[0]
        struct.pack_into("<II", bad, fat, 0, len(bad) + 100)
        with self.assertRaises(FormatError):
            Rom(bad)


class NarcTests(unittest.TestCase):
    def test_round_trip_preserves_bytes(self) -> None:
        for members in ([], [b""], [b"a", b"bc", b"def", b"ghij", b"klmno"], [bytes(range(256)) * 3, b"", b"x"]):
            data = Narc(members).build()
            parsed = Narc.parse(data)
            self.assertEqual(parsed.members, members)
            self.assertEqual(parsed.build(), data)

    def test_unaligned_and_unpadded_layouts_round_trip(self) -> None:
        for kwargs in ({"alignment": 1}, {"pad_tail": False}, {"pad_byte": 0x00}):
            data = Narc([b"abc", b"de", b"f"], **kwargs).build()
            self.assertEqual(Narc.parse(data).build(), data)

    def test_member_replacement_preserves_others(self) -> None:
        original = Narc([b"one", b"two", b"three"])
        changed = Narc.parse(original.build())
        changed.members[1] = b"TWO"
        self.assertEqual(Narc.parse(changed.build()).members, [b"one", b"TWO", b"three"])

    def test_rejects_malformed(self) -> None:
        good = Narc([b"abc"]).build()
        cases = [b"NOPE" + good[4:], good[:-4], good + b"\x00", good.replace(b"BTAF", b"XXXX")]
        bad_range = bytearray(good)
        struct.pack_into("<I", bad_range, 0x10 + 12 + 4, 0x1000)
        cases.append(bytes(bad_range))
        for data in cases:
            with self.subTest(data=data[:8]):
                with self.assertRaises(FormatError):
                    Narc.parse(data)


class BlzTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        for size in (64, 300, 4096):
            data = bytes((i * 7) % 5 for i in range(size)) * 2
            packed = blz.compress(data)
            self.assertIsNotNone(blz.footer(packed))
            self.assertEqual(blz.decompress(packed), data)

    def test_prefix_is_preserved(self) -> None:
        data = b"\x00" * 600
        packed = b"RAWPREFIX" + blz.compress(data)
        self.assertEqual(blz.decompress(packed), b"RAWPREFIX" + data)

    def test_invalid_footer(self) -> None:
        self.assertIsNone(blz.footer(b"\x00" * 16))
        with self.assertRaises(FormatError):
            blz.decompress(b"\x01" * 32)


class Arm9Tests(unittest.TestCase):
    def test_zero_end_is_uncompressed(self) -> None:
        state = arm9.classify(arm9.synthetic_arm9())
        self.assertEqual((state.state, state.params.offset), ("uncompressed", 0xFB0))

    def test_stale_end_regression_is_repaired(self) -> None:
        stale = arm9.synthetic_arm9(compressed_static_end=0x020776CC)  # the value found in W2U's prebuilt ARM9
        self.assertEqual(arm9.classify(stale).state, "stale")
        fixed, before, changed = arm9.repaired(stale)
        self.assertTrue(changed)
        self.assertEqual(before.params.compressed_end_field_offset, 0xFC4)
        self.assertEqual(arm9.classify(fixed).state, "uncompressed")
        self.assertEqual(struct.unpack_from("<I", fixed, 0xFC4)[0], 0)
        self.assertEqual(fixed[:0xFC4] + fixed[0xFC8:], stale[:0xFC4] + stale[0xFC8:])

    def test_in_range_garbage_end_is_stale(self) -> None:
        self.assertEqual(arm9.classify(arm9.synthetic_arm9(compressed_static_end=0x02003000)).state, "stale")

    def test_valid_compressed_image_is_left_alone(self) -> None:
        head = arm9.synthetic_arm9(size=0x2000)
        tail = blz.compress(b"\x11\x22" * 0x800)
        image = bytearray(head + tail)
        struct.pack_into("<I", image, 0xFB0 + 0x14, 0x02000000 + len(image))
        fixed, state, changed = arm9.repaired(bytes(image))
        self.assertEqual(state.state, "compressed")
        self.assertFalse(changed)
        self.assertEqual(fixed, bytes(image))

    def test_marker_must_be_unique(self) -> None:
        data = bytearray(arm9.synthetic_arm9())
        data[0x2000:0x2008] = arm9.MAGIC
        with self.assertRaises(FormatError):
            arm9.find_module_params(bytes(data))
        with self.assertRaises(FormatError):
            arm9.find_module_params(b"\x00" * 0x5000)


class EncounterTests(unittest.TestCase):
    def container(self, species: int = 504) -> enc.Container:
        tables = {(c, k): [enc.Slot(species, 0, 3, 5)] * n for c, k, n in enc.TABLES}
        return enc.Container((10,) * 8, tables)

    def test_round_trip_and_sizes(self) -> None:
        one = enc.encode_record([self.container()])
        four = enc.encode_record([self.container()] * 4)
        self.assertEqual((len(one), len(four)), (232, 928))
        self.assertEqual(enc.decode_record(four)[3].tables, self.container().tables)

    def test_species_form_packing(self) -> None:
        c = self.container()
        c.tables[("GRASS", "SINGLES")] = [enc.Slot(1023, 31, 1, 100)] * 12
        data = enc.encode_container(c)
        self.assertEqual(struct.unpack_from("<H", data, 8)[0], (31 << 11) | 1023)
        self.assertEqual(enc.decode_container(data).tables[("GRASS", "SINGLES")][0], enc.Slot(1023, 31, 1, 100))

    def test_rejects_out_of_range(self) -> None:
        for slot in (enc.Slot(2048, 0, 1, 1), enc.Slot(5, 32, 1, 1), enc.Slot(5, 0, 6, 5), enc.Slot(5, 0, 0, 5),
                     enc.Slot(5, 0, 1, 101), enc.Slot(0, 0, 1, 1)):
            c = self.container()
            c.tables[("SURF", "SINGLES")] = [slot] * 5
            with self.subTest(slot=slot), self.assertRaises(FormatError):
                enc.encode_container(c)
        with self.assertRaises(FormatError):
            enc.decode_record(b"\x00" * 500)

    def test_wrong_slot_count_rejected(self) -> None:
        c = self.container()
        c.tables[("GRASS", "SINGLES")] = [enc.Slot(504, 0, 2, 2)] * 11
        with self.assertRaises(FormatError):
            enc.encode_container(c)

    def test_route19_fixture_exact(self) -> None:
        slots = enc.fixture_grass_slots()
        self.assertEqual(enc.weights(slots), {504: 35, 703: 35, 650: 30})
        self.assertEqual({(s.species, s.min_level, s.max_level, s.form) for s in slots},
                         {(504, 5, 5, 0), (703, 6, 6, 0), (650, 7, 7, 0)})
        record = enc.encode_record(enc.apply_route19_fixture([self.container(519)] * 4))
        self.assertEqual(enc.verify_route19_fixture(record), [])
        broken = bytearray(record)
        struct.pack_into("<H", broken, 232 * 2 + 8, 505)
        problems = enc.verify_route19_fixture(bytes(broken))
        self.assertTrue(any("season 2 grass slot 0 at byte 0x1d8" in p for p in problems), problems)
        self.assertTrue(enc.verify_route19_fixture(record[:232]))


class TrainerCodecTests(unittest.TestCase):
    def test_trdata_round_trip(self) -> None:
        t = trn.TrainerData(3, 115, 1, 2, (17, 0, 0, 0), 0x87, 0, 30, 0)
        data = trn.encode_trdata(t)
        self.assertEqual(len(data), 20)
        self.assertEqual(trn.decode_trdata(data), t)

    def test_trpoke_formats(self) -> None:
        member = struct.pack("<BBHHH", 50, 0x21, 12, 504, 0)
        self.assertEqual(trn.decode_trpoke(member * 2, 0, 2)[1].species, 504)
        with_moves = member + struct.pack("<H4H", 17, 33, 45, 0, 0)
        got = trn.decode_trpoke(with_moves, 3, 1)[0]
        self.assertEqual((got.held_item, got.moves), (17, (33, 45, 0, 0)))
        with self.assertRaises(FormatError):
            trn.decode_trpoke(member, 0, 2)
        with self.assertRaises(FormatError):
            trn.decode_trpoke(struct.pack("<BBHHH", 50, 0, 0, 504, 0), 0, 1)


TABLE_TEXT = """0x2:
  Name: VMHalt
  CommandType: End

0x4:
  Name: VMCall
  Parameters:
    - Name: addr
      Type: int
  CommandType: Call

0x1e:
  Name: VMJump
  Parameters:
    - Name: addr
      Type: int
  CommandType: Jump

0x1f:
  Name: VMJumpIf
  Parameters:
    - Name: cond
      Type: byte
    - Name: addr
      Type: int
  CommandType: Jump
  HasCondition: true

0x5:
  Name: VMReturn
  CommandType: Return

0xb5:
  Name: ItemAdd
  Parameters:
    - Name: itemId
      Type: ushort
    - Name: amount
      Type: ushort
    - Name: pSuccess
      Type: ref ushort
      IsReturn: true
      ReturnType: bool

0x10c:
  Name: PokePartyAdd
  Parameters:
    - Name: pSuccess
      Type: ref ushort
    - Name: species
      Type: ushort
    - Name: forme
      Type: ushort
    - Name: level
      Type: ushort

0x99:
  Name: Mystery
  Parameters:
    - Name: x
      Type: strangetype
"""


class ScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = scripts.load_opcode_table(TABLE_TEXT)

    def test_table_parsing(self) -> None:
        self.assertEqual(self.table[0x10C].size, 10)
        self.assertEqual(self.table[0xB5].operand_offset("itemId"), 2)
        self.assertTrue(self.table[0x1F].conditional)
        self.assertIsNone(self.table[0x99].size)

    def build(self, *commands: bytes, entries: int = 1) -> bytes:
        header_len = 4 * entries + 2
        body = b"".join(commands)
        header = b"".join(struct.pack("<i", header_len - 4 * (i + 1)) for i in range(entries))
        return header + struct.pack("<H", 0xFD13) + body

    def test_follows_flow_and_finds_constants(self) -> None:
        give = struct.pack("<5H", 0x10C, 0x8000, 495, 0, 5)
        give_var = struct.pack("<5H", 0x10C, 0x8000, 0x8004, 0, 5)
        item = struct.pack("<4H", 0xB5, 17, 1, 0x8000)
        jump = struct.pack("<Hi", 0x1E, len(item))  # skip the first ItemAdd
        call = struct.pack("<Hi", 0x04, 2)  # call the subroutine after the halt
        data = self.build(jump, item, give, give_var, call, struct.pack("<H", 0x02), struct.pack("<H", 0x05), item)
        sites = scripts.constant_sites(0, data, self.table, {"PokePartyAdd": "species", "ItemAdd": "itemId"})
        found = [(s.opcode_name, s.value) for s in sites]
        self.assertEqual(found, [("PokePartyAdd", 495)])  # skipped item never reached; var operand skipped
        self.assertEqual(dict(sites[0].context)["level"], 5)

    def test_stops_at_unknown_or_unsized_opcode(self) -> None:
        give = struct.pack("<5H", 0x10C, 0x8000, 495, 0, 5)
        for blocker in (struct.pack("<H", 0x777), struct.pack("<HH", 0x99, 1)):
            data = self.build(blocker, give, struct.pack("<H", 0x02))
            dis = scripts.disassemble(data, self.table)
            self.assertTrue(dis.stopped)
            self.assertEqual(scripts.constant_sites(0, data, self.table, {"PokePartyAdd": "species"}), [])

    def test_patch_is_guarded(self) -> None:
        give = struct.pack("<5H", 0x10C, 0x8000, 495, 0, 5)
        data = bytearray(self.build(give, struct.pack("<H", 0x02)))
        [site] = scripts.constant_sites(0, bytes(data), self.table, {"PokePartyAdd": "species"})
        scripts.patch_u16(data, site, 650, self.table)
        self.assertEqual(struct.unpack_from("<H", data, site.value_offset)[0], 650)
        with self.assertRaises(FormatError):
            scripts.patch_u16(data, site, 651, self.table)  # no longer holds the original value
        with self.assertRaises(FormatError):
            scripts.patch_u16(bytearray(self.build(give)), site, 0x9000, self.table)

    def test_bad_headers(self) -> None:
        for data in (b"\x00\x00", struct.pack("<i", 100) + struct.pack("<H", 0xFD13)):
            with self.assertRaises(FormatError):
                scripts.entry_points(data)


if __name__ == "__main__":
    unittest.main()
