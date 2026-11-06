"""Transport between AxisController and a drive, plus the simulated channel and its runner."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

from . import cia402 as c
from .sim_drive import SimulatedCia402Drive


class Cia402Image:
    """Cyclic CiA 402 data. Fields not in the drive's PDO are simply not transferred."""
    __slots__ = ("statusword", "position_actual", "velocity_actual", "torque_actual", "mode_display",
                 "error_code", "following_error", "digital_inputs",
                 "controlword", "mode_of_operation", "target_position", "target_velocity",
                 "target_torque", "profile_velocity", "profile_acceleration", "profile_deceleration")

    def __init__(self) -> None:
        # drive -> master
        self.statusword = 0
        self.position_actual = 0
        self.velocity_actual = 0
        self.torque_actual = 0
        self.mode_display = 0
        self.error_code = 0
        self.following_error = 0
        self.digital_inputs = 0
        # master -> drive
        self.controlword = 0
        self.mode_of_operation = 0
        self.target_position = 0
        self.target_velocity = 0
        self.target_torque = 0
        self.profile_velocity = 0
        self.profile_acceleration = 0
        self.profile_deceleration = 0


@dataclass(frozen=True)
class PdoEntry:
    index: int
    sub_index: int
    bit_length: int
    bit_offset: int

    @property
    def is_padding(self) -> bool:
        return self.index == 0

    def describe(self) -> str:
        if self.is_padding:
            return f"(padding)  {self.bit_length:2d}b"
        return (f"{self.index:04X}:{self.sub_index:02X} {self.bit_length:2d}b @{self.bit_offset:<3d} "
                f"{c.OBJECT_NAMES.get(self.index, '')}")

    def __str__(self) -> str:
        return self.describe()


class AxisChannel(Protocol):
    name: str
    is_simulated: bool
    rx_pdo: list[PdoEntry]     # master -> drive
    tx_pdo: list[PdoEntry]     # drive -> master

    def is_mapped(self, index: int) -> bool: ...
    def read_inputs(self, inputs: list[bytes], img: Cia402Image, dt: float) -> None: ...
    def write_outputs(self, outputs: list[bytearray], img: Cia402Image) -> None: ...
    def write_parameter(self, index: int, sub: int, value: int, byte_size: int) -> None: ...
    def read_parameter(self, index: int, sub: int, byte_size: int, signed: bool) -> int: ...


def _layout(*entries: tuple[int, int]) -> list[PdoEntry]:
    out, bit = [], 0
    for idx, bits in entries:
        out.append(PdoEntry(idx, 0, bits, bit))
        bit += bits
    return out


class SimulatedAxisChannel:
    """Channel backed by SimulatedCia402Drive; every object counts as "mapped"."""
    name = "模擬軸"
    is_simulated = True

    _MAPPED = {c.CONTROLWORD, c.MODES_OF_OPERATION, c.TARGET_POSITION, c.TARGET_VELOCITY,
               c.PROFILE_VELOCITY, c.PROFILE_ACCELERATION, c.PROFILE_DECELERATION,
               c.STATUSWORD, c.MODES_OF_OPERATION_DISPLAY, c.POSITION_ACTUAL_VALUE,
               c.VELOCITY_ACTUAL_VALUE, c.TORQUE_ACTUAL_VALUE, c.ERROR_CODE}

    def __init__(self, drive: SimulatedCia402Drive) -> None:
        self.drive = drive
        self._last = Cia402Image()
        self.rx_pdo = _layout((c.CONTROLWORD, 16), (c.MODES_OF_OPERATION, 8), (c.TARGET_POSITION, 32),
                              (c.TARGET_VELOCITY, 32), (c.PROFILE_VELOCITY, 32),
                              (c.PROFILE_ACCELERATION, 32), (c.PROFILE_DECELERATION, 32))
        self.tx_pdo = _layout((c.STATUSWORD, 16), (c.MODES_OF_OPERATION_DISPLAY, 8),
                              (c.POSITION_ACTUAL_VALUE, 32), (c.VELOCITY_ACTUAL_VALUE, 32),
                              (c.TORQUE_ACTUAL_VALUE, 16), (c.ERROR_CODE, 16))

    def is_mapped(self, index: int) -> bool:
        return index in self._MAPPED

    def read_inputs(self, inputs, img: Cia402Image, dt: float) -> None:
        self.drive.step(self._last, dt)
        self.drive.fill_inputs(img)

    def write_outputs(self, outputs, img: Cia402Image) -> None:
        l = self._last
        l.controlword = img.controlword
        l.mode_of_operation = img.mode_of_operation
        l.target_position = img.target_position
        l.target_velocity = img.target_velocity
        l.profile_velocity = img.profile_velocity
        l.profile_acceleration = img.profile_acceleration
        l.profile_deceleration = img.profile_deceleration

    def write_parameter(self, index: int, sub: int, value: int, byte_size: int) -> None:
        self.drive.write_object(index, sub, value)

    def read_parameter(self, index: int, sub: int, byte_size: int, signed: bool) -> int:
        return self.drive.read_object(index, sub)


class SimulationRunner:
    """Runs a controller's on_cycle on its own thread at a fixed period (simulation only).

    The controller advances cyclic-mode trajectories by its nominal period, so the measured dt
    passed here only feeds the drive model.
    """

    def __init__(self, controller, period_ms: int = 1) -> None:
        self._controller = controller
        self._period_ns = max(1, int(period_ms)) * 1_000_000
        self._running = True
        self._thread = threading.Thread(target=self._run, name="Axis simulation", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from ..services import winrt
        winrt.time_begin_period(1)
        winrt.set_current_thread_priority(winrt.THREAD_PRIORITY_ABOVE_NORMAL)
        try:
            pc = time.perf_counter_ns
            nxt = last = pc()
            while self._running:
                nxt += self._period_ns
                while True:
                    rem = nxt - pc()
                    if rem <= 0:
                        break
                    time.sleep(rem / 1e9 if rem > 300_000 else 0)
                now = pc()
                dt = (now - last) / 1e9
                last = now
                if now - nxt > self._period_ns * 10:
                    nxt = now
                self._controller.on_cycle([], [], min(dt, 0.05), True)
        finally:
            winrt.time_end_period(1)

    def dispose(self) -> None:
        self._running = False
        self._thread.join(1.0)
