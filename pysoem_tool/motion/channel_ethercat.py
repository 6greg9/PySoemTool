"""AxisChannel for a real CiA 402 drive on the bus (port of EtherCatAxisChannel.cs).

Object locations come from the drive's PDO assignment / mapping read over SDO (handoff §6.20),
so any vendor mapping works as long as 0x6040 and 0x6041 are in the process data. pysoem gives
each slave its own output / input bytes, so bit offsets start at 0 for that slave.
"""
from __future__ import annotations

import struct
from typing import Protocol

from . import cia402 as c
from .channel import Cia402Image, PdoEntry


class SdoService(Protocol):
    layouts: list

    def sdo_read(self, slave: int, index: int, sub: int, size: int = 0) -> bytes: ...
    def sdo_write(self, slave: int, index: int, sub: int, data: bytes) -> None: ...


class ChannelError(RuntimeError):
    pass


RECOMMENDED_RX = ((c.CONTROLWORD, 16), (c.TARGET_POSITION, 32), (c.TARGET_VELOCITY, 32),
                  (c.MODES_OF_OPERATION, 8))
RECOMMENDED_TX = ((c.STATUSWORD, 16), (c.POSITION_ACTUAL_VALUE, 32), (c.VELOCITY_ACTUAL_VALUE, 32),
                  (c.ERROR_CODE, 16), (c.MODES_OF_OPERATION_DISPLAY, 8))


def read_bits(buf: bytes, bit: int, length: int) -> int:
    """Little-endian bit field (LSB first), clipped to the buffer."""
    if bit % 8 == 0 and length % 8 == 0 and (bit + length) // 8 <= len(buf):
        return int.from_bytes(buf[bit // 8:(bit + length) // 8], "little")
    v = 0
    for i in range(length):
        b = bit + i
        if b >> 3 >= len(buf):
            break
        if buf[b >> 3] & (1 << (b & 7)):
            v |= 1 << i
    return v


def write_bits(buf: bytearray, bit: int, length: int, value: int) -> None:
    value &= (1 << length) - 1
    if bit % 8 == 0 and length % 8 == 0 and (bit + length) // 8 <= len(buf):
        buf[bit // 8:(bit + length) // 8] = value.to_bytes(length // 8, "little")
        return
    for i in range(length):
        b = bit + i
        if b >> 3 >= len(buf):
            break
        if (value >> i) & 1:
            buf[b >> 3] |= 1 << (b & 7)
        else:
            buf[b >> 3] &= ~(1 << (b & 7)) & 0xFF


def sign_extend(raw: int, bits: int) -> int:
    if bits <= 0 or bits >= 64:
        return raw
    sign = 1 << (bits - 1)
    return (raw & (sign - 1)) - (raw & sign)


def read_pdo_config(svc: SdoService, slave: int, assign_index: int) -> list[PdoEntry]:
    out: list[PdoEntry] = []
    count = svc.sdo_read(slave, assign_index, 0)[0]
    bit = 0
    for i in range(1, count + 1):
        pdo = struct.unpack("<H", svc.sdo_read(slave, assign_index, i)[:2])[0]
        n = svc.sdo_read(slave, pdo, 0)[0]
        for j in range(1, n + 1):
            e = struct.unpack("<I", svc.sdo_read(slave, pdo, j)[:4])[0]
            entry = PdoEntry(e >> 16, (e >> 8) & 0xFF, e & 0xFF, bit)
            out.append(entry)
            bit += entry.bit_length
    return out


class EtherCatAxisChannel:
    is_simulated = False

    def __init__(self, svc: SdoService, slave: int, name: str,
                 rx: list[PdoEntry], tx: list[PdoEntry]) -> None:
        self._svc = svc
        self.slave = slave
        self.name = name
        self.rx_pdo = rx
        self.tx_pdo = tx
        self._rx = self._index(rx)
        self._tx = self._index(tx)
        # mode shown to the controller when 0x6061 is not in the TxPDO; set after SDO checks
        self.assumed_mode_display = 0

    @staticmethod
    def _index(entries: list[PdoEntry]) -> dict[int, PdoEntry]:
        d: dict[int, PdoEntry] = {}
        for e in entries:
            if not e.is_padding and e.sub_index == 0 and e.index not in d:
                d[e.index] = e
        return d

    def is_mapped(self, index: int) -> bool:
        return index in self._rx or index in self._tx

    @property
    def missing_required(self) -> list[int]:
        miss = []
        if c.CONTROLWORD not in self._rx:
            miss.append(c.CONTROLWORD)
        if c.STATUSWORD not in self._tx:
            miss.append(c.STATUSWORD)
        return miss

    # ---------------------------------------------------------------- discovery

    @classmethod
    def create(cls, svc: SdoService, slave: int, name: str) -> "EtherCatAxisChannel":
        """Reads the PDO configuration over SDO; raises ChannelError when the slave can't be
        driven (missing 0x6040 / 0x6041, or SDO length differs from process data)."""
        rx = read_pdo_config(svc, slave, 0x1C12)
        tx = read_pdo_config(svc, slave, 0x1C13)
        ch = cls(svc, slave, name, rx, tx)
        missing = ch.missing_required
        if missing:
            raise ChannelError(
                f"PDO 中沒有 {'、'.join(f'0x{m:04X}' for m in missing)}，無法以 CiA 402 控制。"
                "可用「套用建議 PDO」設定。")
        lay = svc.layouts[slave - 1]
        rx_bits = sum(e.bit_length for e in rx)
        tx_bits = sum(e.bit_length for e in tx)
        # pysoem exposes whole bytes, so compare byte-rounded sizes
        if (rx_bits + 7) // 8 != lay.output_bytes or (tx_bits + 7) // 8 != lay.input_bytes:
            raise ChannelError(
                f"SDO 讀到的 PDO 長度 (Rx {rx_bits} / Tx {tx_bits} bits) 和目前的 process data "
                f"(Out {lay.output_bits} / In {lay.input_bits} bits) 不一致。"
                "可能是 PDO 剛被修改但尚未重新設定，請切到 PRE-OP 再回 SAFE-OP。")
        return ch

    @staticmethod
    def apply_recommended_mapping(svc: SdoService, slave: int) -> list[str]:
        """Writes the recommended mapping into 0x1600 / 0x1A00 (RAM only) and assigns them.
        Must be called in PRE-OP; then go to SAFE-OP so the master re-reads the mapping.
        Order (§6.21): assign:0=0 -> pdo:0=0 -> entries -> pdo:0=n -> assign:1=pdo -> assign:0=1."""
        done: list[str] = []
        _write_pdo(svc, slave, 0x1C12, 0x1600, RECOMMENDED_RX, done)
        _write_pdo(svc, slave, 0x1C13, 0x1A00, RECOMMENDED_TX, done)
        return done

    # ---------------------------------------------------------------- cyclic

    def _get(self, buf: bytes, index: int, fallback: int) -> int:
        e = self._tx.get(index)
        if e is None:
            return fallback
        return sign_extend(read_bits(buf, e.bit_offset, e.bit_length), e.bit_length)

    def _set(self, buf: bytearray, index: int, value: int) -> None:
        e = self._rx.get(index)
        if e is not None:
            write_bits(buf, e.bit_offset, e.bit_length, value)

    def read_inputs(self, inputs: list[bytes], img: Cia402Image, dt: float) -> None:
        buf = inputs[self.slave - 1]
        img.statusword = self._get(buf, c.STATUSWORD, img.statusword) & 0xFFFF
        img.position_actual = self._get(buf, c.POSITION_ACTUAL_VALUE, img.position_actual)
        img.velocity_actual = self._get(buf, c.VELOCITY_ACTUAL_VALUE, img.velocity_actual)
        img.torque_actual = self._get(buf, c.TORQUE_ACTUAL_VALUE, img.torque_actual)
        img.error_code = self._get(buf, c.ERROR_CODE, img.error_code) & 0xFFFF
        img.following_error = self._get(buf, c.FOLLOWING_ERROR_ACTUAL_VALUE, img.following_error)
        img.digital_inputs = self._get(buf, c.DIGITAL_INPUTS, img.digital_inputs) & 0xFFFFFFFF
        img.mode_display = (self._get(buf, c.MODES_OF_OPERATION_DISPLAY, 0)
                            if c.MODES_OF_OPERATION_DISPLAY in self._tx else self.assumed_mode_display)

    def write_outputs(self, outputs: list[bytearray], img: Cia402Image) -> None:
        buf = outputs[self.slave - 1]
        self._set(buf, c.CONTROLWORD, img.controlword)
        self._set(buf, c.MODES_OF_OPERATION, img.mode_of_operation)
        self._set(buf, c.TARGET_POSITION, img.target_position)
        self._set(buf, c.TARGET_VELOCITY, img.target_velocity)
        self._set(buf, c.TARGET_TORQUE, img.target_torque)
        self._set(buf, c.PROFILE_VELOCITY, img.profile_velocity)
        self._set(buf, c.PROFILE_ACCELERATION, img.profile_acceleration)
        self._set(buf, c.PROFILE_DECELERATION, img.profile_deceleration)

    # ---------------------------------------------------------------- acyclic

    def write_parameter(self, index: int, sub: int, value: int, byte_size: int) -> None:
        data = (int(value) & ((1 << (8 * byte_size)) - 1)).to_bytes(byte_size, "little")
        self._svc.sdo_write(self.slave, index, sub, data)

    def read_parameter(self, index: int, sub: int, byte_size: int, signed: bool) -> int:
        data = self._svc.sdo_read(self.slave, index, sub)[:8]
        v = int.from_bytes(data, "little")
        return sign_extend(v, len(data) * 8) if signed and data else v


def _write_pdo(svc: SdoService, slave: int, assign: int, pdo: int,
               entries: tuple[tuple[int, int], ...], done: list[str]) -> None:
    def w(idx: int, sub: int, data: bytes, what: str) -> None:
        try:
            svc.sdo_write(slave, idx, sub, data)
        except Exception as e:
            raise ChannelError(f"寫入 0x{idx:04X}:{sub:02X}（{what}）失敗：{e}"
                               + (f"（已完成：{'、'.join(done)}）" if done else "")) from e
        done.append(f"0x{idx:04X}:{sub:02X}")

    w(assign, 0, b"\x00", "清除 PDO assign")
    w(pdo, 0, b"\x00", "清除 PDO mapping")
    for i, (idx, bits) in enumerate(entries, 1):
        w(pdo, i, struct.pack("<I", (idx << 16) | bits), f"mapping entry {i}")
    w(pdo, 0, bytes([len(entries)]), "mapping entry 數")
    w(assign, 1, struct.pack("<H", pdo), "PDO assign")
    w(assign, 0, b"\x01", "PDO assign 數")
