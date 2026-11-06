"""Software model of a CiA 402 servo drive (port of SimulatedCia402Drive.cs).

It implements the drive side on its own (state machine, PP/PV/HM/CSP/CSV, set-point and homing
handshakes, following-error fault) and deliberately shares no logic with AxisController, so
handshake mistakes in the controller show up in tests (handoff §6.22).
"""
from __future__ import annotations

import math
import threading
from typing import TYPE_CHECKING

from . import cia402 as c
from .cia402 import DriveState, OperationMode
from .trajectory import TrajectoryGenerator

if TYPE_CHECKING:
    from .channel import Cia402Image

DS = DriveState
OM = OperationMode


def _iround(x: float) -> int:
    return int(round(x))


class SimulatedCia402Drive:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._profile = TrajectoryGenerator()
        self._state = DS.SWITCH_ON_DISABLED
        self._last_cw = 0
        self._mode = 0
        self._pos = 0.0
        self._vel = 0.0
        self._error_code = 0
        self._pp_ack = False
        self._target_reached = True
        self._homing_attained = False
        self._homing_error = False
        self._homing_running = False
        self._following_error = False
        self._homing_travel = 0.0
        self._fault_reaction_cycles = 0
        self.max_velocity = 200_000.0   # CSP: max position change per second before 0x8611
        self._od: dict[tuple[int, int], int] = {
            (c.PROFILE_VELOCITY, 0): 10_000,
            (c.PROFILE_ACCELERATION, 0): 50_000,
            (c.PROFILE_DECELERATION, 0): 50_000,
            (c.QUICK_STOP_DECELERATION, 0): 200_000,
            (c.HOMING_METHOD, 0): 35,
            (c.HOMING_SPEEDS, 1): 5_000,
            (c.HOMING_SPEEDS, 2): 1_000,
            (c.HOMING_ACCELERATION, 0): 50_000,
            (c.HOME_OFFSET, 0): 0,
            (c.SUPPORTED_DRIVE_MODES, 0): (1 << 0) | (1 << 2) | (1 << 5) | (1 << 7) | (1 << 8),
            (c.MODES_OF_OPERATION, 0): 0,
            (0x1018, 1): 0,
            (0x1008, 0): 0,
        }

    # ------------------------------------------------------------------ test / UI hooks

    def inject_fault(self, error_code: int = 0x1000) -> None:
        with self._lock:
            self._enter_fault(error_code)

    @property
    def state(self) -> DriveState:
        return self._state

    def read_object(self, index: int, sub: int) -> int:
        with self._lock:
            if (index, sub) == (c.STATUSWORD, 0):
                return self._build_statusword()
            if (index, sub) == (c.MODES_OF_OPERATION_DISPLAY, 0):
                return self._mode
            if (index, sub) == (c.POSITION_ACTUAL_VALUE, 0):
                return _iround(self._pos)
            if (index, sub) == (c.VELOCITY_ACTUAL_VALUE, 0):
                return _iround(self._vel)
            if (index, sub) == (c.ERROR_CODE, 0):
                return self._error_code
            if (index, sub) in self._od:
                return self._od[(index, sub)]
            raise KeyError(f"模擬驅動器沒有物件 0x{index:04X}:{sub:02X}")

    def write_object(self, index: int, sub: int, value: int) -> None:
        with self._lock:
            if (index, sub) == (c.MODES_OF_OPERATION, 0):
                self._od[(index, 0)] = value
                return
            if (index, sub) not in self._od:
                raise KeyError(f"模擬驅動器沒有物件 0x{index:04X}:{sub:02X}")
            self._od[(index, sub)] = value

    # ------------------------------------------------------------------ cycle

    def step(self, cmd: "Cia402Image", dt: float) -> None:
        with self._lock:
            self._apply_controlword(cmd.controlword)
            self._od[(c.MODES_OF_OPERATION, 0)] = cmd.mode_of_operation
            if cmd.mode_of_operation != self._mode:
                self._mode = cmd.mode_of_operation
                self._profile.reset(self._pos)
                self._homing_running = False

            if self._state == DS.OPERATION_ENABLED:
                self._move(cmd, dt)
            elif self._state in (DS.QUICK_STOP_ACTIVE, DS.FAULT_REACTION_ACTIVE):
                self._decelerate(dt)
            else:
                self._vel = 0.0
                self._profile.reset(self._pos)

            if self._state == DS.FAULT_REACTION_ACTIVE:
                self._fault_reaction_cycles -= 1
                if self._fault_reaction_cycles <= 0 and self._vel == 0:
                    self._state = DS.FAULT

            self._last_cw = cmd.controlword

    def fill_inputs(self, img: "Cia402Image") -> None:
        with self._lock:
            img.statusword = self._build_statusword()
            img.position_actual = _iround(self._pos)
            img.velocity_actual = _iround(self._vel)
            img.torque_actual = max(-32768, min(32767, _iround(self._vel / 100.0)))
            img.mode_display = self._mode
            img.error_code = self._error_code
            img.following_error = 0

    # ------------------------------------------------------------------ internals

    def _apply_controlword(self, cw: int) -> None:
        reset_edge = bool(cw & c.CW_FAULT_RESET) and not (self._last_cw & c.CW_FAULT_RESET)
        if self._state == DS.FAULT:
            if reset_edge:
                self._state = DS.SWITCH_ON_DISABLED
                self._error_code = 0
                self._following_error = False
            return
        if self._state == DS.FAULT_REACTION_ACTIVE:
            return

        cmd = cw & 0x8F
        disable_voltage = (cw & c.CW_ENABLE_VOLTAGE) == 0
        quick_stop = (not disable_voltage) and (cw & c.CW_QUICK_STOP) == 0
        st = self._state
        if st == DS.SWITCH_ON_DISABLED:
            if (cw & 0x87) == c.CMD_SHUTDOWN:
                self._state = DS.READY_TO_SWITCH_ON
        elif st == DS.READY_TO_SWITCH_ON:
            if disable_voltage or quick_stop:
                self._state = DS.SWITCH_ON_DISABLED
            elif cmd in (c.CMD_SWITCH_ON, c.CMD_ENABLE_OPERATION):
                self._state = DS.SWITCHED_ON          # enable operation needs two steps
        elif st == DS.SWITCHED_ON:
            if disable_voltage or quick_stop:
                self._state = DS.SWITCH_ON_DISABLED
            elif (cw & 0x87) == c.CMD_SHUTDOWN:
                self._state = DS.READY_TO_SWITCH_ON
            elif cmd == c.CMD_ENABLE_OPERATION:
                self._state = DS.OPERATION_ENABLED
                self._profile.reset(self._pos)
        elif st == DS.OPERATION_ENABLED:
            if disable_voltage:
                self._state = DS.SWITCH_ON_DISABLED
            elif quick_stop:
                self._state = DS.QUICK_STOP_ACTIVE
            elif (cw & 0x87) == c.CMD_SHUTDOWN:
                self._state = DS.READY_TO_SWITCH_ON
            elif cmd == c.CMD_SWITCH_ON:
                self._state = DS.SWITCHED_ON
        elif st == DS.QUICK_STOP_ACTIVE:
            if disable_voltage:
                self._state = DS.SWITCH_ON_DISABLED
            elif cmd == c.CMD_ENABLE_OPERATION and self._vel == 0:
                self._state = DS.OPERATION_ENABLED
        if self._state in (DS.SWITCH_ON_DISABLED, DS.READY_TO_SWITCH_ON, DS.SWITCHED_ON):
            self._vel = 0.0

    def _move(self, cmd: "Cia402Image", dt: float) -> None:
        halt = bool(cmd.controlword & c.CW_HALT)
        bit4 = bool(cmd.controlword & c.CW_NEW_SET_POINT)
        bit4_edge = bit4 and not (self._last_cw & c.CW_NEW_SET_POINT)
        od = self._od
        p = self._profile
        mode = self._mode

        if mode == OM.PROFILE_POSITION:
            if bit4_edge:
                vmax = cmd.profile_velocity or od[(c.PROFILE_VELOCITY, 0)]
                acc = cmd.profile_acceleration or od[(c.PROFILE_ACCELERATION, 0)]
                dec = cmd.profile_deceleration or od[(c.PROFILE_DECELERATION, 0)]
                rel = bool(cmd.controlword & c.CW_RELATIVE)
                goal = p.goal_position + cmd.target_position if rel else cmd.target_position
                p.move_to(goal, vmax, acc, dec)
                self._pp_ack = True
                self._target_reached = False
            if not bit4:
                self._pp_ack = False
            if halt:
                p.stop()
            p.step(dt)
            self._pos, self._vel = p.position, p.velocity
            self._target_reached = not p.is_moving

        elif mode == OM.PROFILE_VELOCITY:
            acc = cmd.profile_acceleration or od[(c.PROFILE_ACCELERATION, 0)]
            dec = cmd.profile_deceleration or od[(c.PROFILE_DECELERATION, 0)]
            goal = 0 if halt else cmd.target_velocity
            p.run_at(goal, acc, dec)
            p.step(dt)
            self._pos, self._vel = p.position, p.velocity
            self._target_reached = abs(self._vel - goal) < 1

        elif mode == OM.HOMING:
            if bit4_edge:
                method = od[(c.HOMING_METHOD, 0)]
                self._homing_attained = self._homing_error = self._target_reached = False
                if method in (35, 37):
                    self._pos = float(od[(c.HOME_OFFSET, 0)])
                    p.reset(self._pos)
                    self._homing_attained = self._target_reached = True
                elif method < -4 or method > 37 or method == 0:
                    self._homing_error = self._target_reached = True
                else:
                    # pretend the home switch is 20 000 units away in the search direction
                    self._homing_running = True
                    self._homing_travel = 0.0
                    direction = 1 if method % 2 == 0 else -1
                    p.reset(self._pos)
                    acc = od[(c.HOMING_ACCELERATION, 0)]
                    p.run_at(direction * od[(c.HOMING_SPEEDS, 1)], acc, acc)
            if not bit4 and self._homing_running:
                self._homing_running = False
                p.stop()
            if halt:
                self._homing_running = False
                p.stop()
            before = p.position
            p.step(dt)
            self._pos, self._vel = p.position, p.velocity
            if self._homing_running:
                self._homing_travel += abs(self._pos - before)
                if self._homing_travel >= 20_000:
                    self._homing_running = False
                    self._pos = float(od[(c.HOME_OFFSET, 0)])
                    self._vel = 0.0
                    p.reset(self._pos)
                    self._homing_attained = self._target_reached = True

        elif mode == OM.CYCLIC_SYNC_POSITION:
            step = cmd.target_position - self._pos
            if dt > 0 and abs(step) / dt > self.max_velocity * 1.5:
                self._following_error = True
                self._enter_fault(0x8611)         # following error
                return
            self._vel = step / dt if dt > 0 else 0.0
            self._pos = float(cmd.target_position)
            self._target_reached = True

        elif mode == OM.CYCLIC_SYNC_VELOCITY:
            self._vel = float(max(-self.max_velocity, min(self.max_velocity, cmd.target_velocity)))
            self._pos += self._vel * dt
            self._target_reached = True

        else:
            self._vel = 0.0

        if mode in (OM.CYCLIC_SYNC_POSITION, OM.CYCLIC_SYNC_VELOCITY):
            p.reset(self._pos)

    def _decelerate(self, dt: float) -> None:
        dec = self._od[(c.QUICK_STOP_DECELERATION, 0)] * dt
        self._vel = 0.0 if abs(self._vel) <= dec else self._vel - math.copysign(dec, self._vel)
        self._pos += self._vel * dt
        self._profile.reset(self._pos)

    def _enter_fault(self, code: int) -> None:
        self._error_code = code
        self._state = DS.FAULT_REACTION_ACTIVE
        self._fault_reaction_cycles = 5
        self._homing_running = False
        self._pp_ack = False

    def _build_statusword(self) -> int:
        sw = {
            DS.NOT_READY_TO_SWITCH_ON: 0x0000,
            DS.SWITCH_ON_DISABLED: 0x0040,
            DS.READY_TO_SWITCH_ON: 0x0021,
            DS.SWITCHED_ON: 0x0023 | c.SW_VOLTAGE_ENABLED,
            DS.OPERATION_ENABLED: 0x0027 | c.SW_VOLTAGE_ENABLED,
            DS.QUICK_STOP_ACTIVE: 0x0007 | c.SW_VOLTAGE_ENABLED,
            DS.FAULT_REACTION_ACTIVE: 0x000F,
            DS.FAULT: 0x0008,
        }.get(self._state, 0)
        sw |= c.SW_REMOTE
        if self._state == DS.OPERATION_ENABLED:
            if self._target_reached:
                sw |= c.SW_TARGET_REACHED
            m = self._mode
            if m == OM.PROFILE_POSITION:
                if self._pp_ack:
                    sw |= c.SW_OP_MODE_SPECIFIC_12
                if self._following_error:
                    sw |= c.SW_OP_MODE_SPECIFIC_13
            elif m == OM.PROFILE_VELOCITY:
                if abs(self._vel) < 1:
                    sw |= c.SW_OP_MODE_SPECIFIC_12
            elif m == OM.HOMING:
                if self._homing_attained:
                    sw |= c.SW_OP_MODE_SPECIFIC_12
                if self._homing_error:
                    sw |= c.SW_OP_MODE_SPECIFIC_13
            elif m in (OM.CYCLIC_SYNC_POSITION, OM.CYCLIC_SYNC_VELOCITY):
                sw |= c.SW_OP_MODE_SPECIFIC_12
        return sw
