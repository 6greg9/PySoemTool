"""EtherCatAxisChannel without hardware: PDO discovery over a fake SDO service, bit-level
access, DIO rejection, and the recommended-PDO write order."""
import struct
from dataclasses import dataclass

import pytest

from conftest import c
from pysoem_tool.motion.channel import Cia402Image
from pysoem_tool.motion.channel_ethercat import (ChannelError, EtherCatAxisChannel, read_bits,
                                                 sign_extend, write_bits)


@dataclass
class Lay:
    output_bytes: int
    input_bytes: int

    @property
    def output_bits(self):
        return self.output_bytes * 8

    @property
    def input_bits(self):
        return self.input_bytes * 8


class FakeSdo:
    def __init__(self, od: dict, layouts, fail_at=None):
        self.od = od
        self.layouts = layouts
        self.writes = []
        self.fail_at = fail_at

    def sdo_read(self, slave, index, sub, size=0):
        return self.od[(index, sub)]

    def sdo_write(self, slave, index, sub, data):
        if self.fail_at == (index, sub):
            raise RuntimeError("SDO abort 0x06010002")
        self.writes.append((index, sub, bytes(data)))
        self.od[(index, sub)] = bytes(data)


def _pdo_od(assign, pdo, entries):
    od = {(assign, 0): bytes([1]), (assign, 1): struct.pack("<H", pdo), (pdo, 0): bytes([len(entries)])}
    for i, (idx, sub, bits) in enumerate(entries, 1):
        od[(pdo, i)] = struct.pack("<I", (idx << 16) | (sub << 8) | bits)
    return od


def drive_od():
    od = _pdo_od(0x1C12, 0x1600, [(0x6040, 0, 16), (0x607A, 0, 32), (0x60FF, 0, 32), (0x6060, 0, 8)])
    od.update(_pdo_od(0x1C13, 0x1A00, [(0x6041, 0, 16), (0x6064, 0, 32), (0x606C, 0, 32),
                                       (0x603F, 0, 16), (0x6061, 0, 8)]))
    return od


def test_bits_roundtrip_unaligned():
    buf = bytearray(4)
    write_bits(buf, 3, 13, 0x1ABC)
    assert read_bits(bytes(buf), 3, 13) == 0x1ABC
    write_bits(buf, 3, 13, 0)
    assert buf == bytearray(4)
    assert sign_extend(0xFFFF, 16) == -1 and sign_extend(0x7F, 8) == 127 and sign_extend(0x80, 8) == -128


def test_discovery_and_cyclic_io():
    svc = FakeSdo(drive_od(), [Lay(11, 13)])
    ch = EtherCatAxisChannel.create(svc, 1, "drive")
    assert [e.bit_offset for e in ch.rx_pdo] == [0, 16, 48, 80]
    assert ch.is_mapped(c.MODES_OF_OPERATION) and not ch.is_mapped(c.PROFILE_VELOCITY)

    inp = bytearray(13)
    write_bits(inp, 0, 16, 0x0237)
    write_bits(inp, 16, 32, (-12345) & 0xFFFFFFFF)
    write_bits(inp, 96, 8, 8)
    img = Cia402Image()
    ch.read_inputs([bytes(inp)], img, 0.001)
    assert (img.statusword, img.position_actual, img.mode_display) == (0x0237, -12345, 8)

    out = [bytearray(11)]
    img.controlword, img.target_position, img.mode_of_operation = 0x000F, -2, 8
    ch.write_outputs(out, img)
    assert read_bits(out[0], 0, 16) == 0x000F
    assert sign_extend(read_bits(out[0], 16, 32), 32) == -2
    assert read_bits(out[0], 80, 8) == 8


def test_dio_rejected_names_missing_objects():
    od = _pdo_od(0x1C12, 0x1600, [(0x7000, 1, 16)])
    od.update(_pdo_od(0x1C13, 0x1A00, [(0x6000, 1, 16)]))
    with pytest.raises(ChannelError) as ei:
        EtherCatAxisChannel.create(FakeSdo(od, [Lay(2, 2)]), 1, "dio")
    assert "0x6040" in str(ei.value) and "0x6041" in str(ei.value)


def test_length_mismatch_rejected():
    with pytest.raises(ChannelError, match="不一致"):
        EtherCatAxisChannel.create(FakeSdo(drive_od(), [Lay(2, 2)]), 1, "drive")


def test_recommended_mapping_order_and_failure_step():
    svc = FakeSdo({}, [Lay(0, 0)])
    EtherCatAxisChannel.apply_recommended_mapping(svc, 1)
    seq = [(i, s) for i, s, _ in svc.writes]
    assert seq[:7] == [(0x1C12, 0), (0x1600, 0), (0x1600, 1), (0x1600, 2), (0x1600, 3), (0x1600, 4), (0x1600, 0)]
    assert seq[7:9] == [(0x1C12, 1), (0x1C12, 0)]
    assert svc.writes[2][2] == struct.pack("<I", 0x60400010)
    assert seq[-1] == (0x1C13, 0) and svc.writes[-1][2] == b"\x01"
    with pytest.raises(ChannelError, match="0x1A00:00"):
        EtherCatAxisChannel.apply_recommended_mapping(FakeSdo({}, [], fail_at=(0x1A00, 0)), 1)
