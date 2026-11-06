"""Master-side CiA 402 logic for one axis (port of AxisController.cs).

``on_cycle`` runs on the cycle thread (EtherCAT cyclic thread or the simulation thread). All
other public methods are called from UI / worker threads and only record requests under a lock,
so the cycle thread never blocks on them. Every cycle publishes an immutable ``AxisStatus``.

Profile modes (PP, PV, HM): the drive plans the trajectory; we hand over targets and run the
controlword handshakes. Cyclic modes (CSP, CSV): TrajectoryGenerator plans here and one target
is sent per cycle, advanced by the *nominal* cycle time (handoff §6.10).
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from enum import Enum

from . import cia402 as c
from .channel import AxisChannel, Cia402Image
from .cia402 import DriveState, OperationMode, describe_mode
from .trajectory import TrajectoryGenerator

DS = DriveState
OM = OperationMode

JOG_WATCHDOG_SECONDS = 0.4      # jog keeps running only while refreshed within this time
COMM_LOSS_CYCLES = 20           # consecutive bad cycles after which commands stop
HOMING_START_HOLD_SECONDS = 0.05
FAULT_RESET_WINDOW = 20         # bit 7 low for 10 cycles, then high for 10

_INT_MIN, _INT_MAX = -(2 ** 31), 2 ** 31 - 1


def clamp_int(v: float) -> int:
    return int(min(max(round(v), _INT_MIN), _INT_MAX))


def clamp_u(v: float) -> int:
    return int(min(max(round(abs(v)), 0), 2 ** 32 - 1))


@dataclass(frozen=True)
class AxisStatus:
    state: DriveState = DS.UNKNOWN
    statusword: int = 0
    controlword: int = 0
    requested_mode: int = OM.PROFILE_POSITION
    mode_display: int = OM.NONE
    position_actual: int = 0
    velocity_actual: int = 0
    torque_actual: int = 0
    error_code: int = 0
    following_error: int = 0
    target_position: int = 0
    target_velocity: int = 0
    enable_requested: bool = False
    is_moving: bool = False
    activity: str = "未連線"
    cycle: int = 0
    communication_ok: bool = False


@dataclass(frozen=True)
class _PendingMove:
    target: float
    relative: bool
    velocity: float
    acc: float
    dec: float


class _PpPhase(Enum):
    IDLE = 0
    WAIT_ACK = 1
    WAIT_ACK_CLEAR = 2
    MOVING = 3


class AxisError(RuntimeError):
    pass


class AxisController:
    def __init__(self, channel: AxisChannel, nominal_cycle_seconds: float) -> None:
        self.channel = channel
        self.cycle_seconds = nominal_cycle_seconds if nominal_cycle_seconds > 0 else 0.001
        self._img = Cia402Image()
        self._lock = threading.Lock()
        self._traj = TrajectoryGenerator()

        # requests from the UI (guarded by _lock)
        self._enable_requested = False
        self._quick_stop_requested = False
        self._fault_reset_cycles = 0
        self._requested_mode: int = OM.PROFILE_POSITION
        self._pending_move: _PendingMove | None = None
        self._halt_requested = False
        self._homing_start_requested = False
        self._velocity_request: float | None = None
        self._vel_acc = 1000.0
        self._vel_dec = 1000.0
        self._jog_deadline = math.inf         # cycle after which a jog stops (dead-man)

        # cycle-thread state
        self._cycle = 0
        self._pp_phase = _PpPhase.IDLE
        self._pp_target: _PendingMove | None = None
        self._homing_active = False
        self._homing_cycles = 0
        self._was_operation_enabled = False
        self._last_mode_display = OM.NONE
        self._bad_comm_cycles = 0
        self._activity = "待機"
        self.status = AxisStatus(requested_mode=self._requested_mode)

    # ================================================================== UI-side requests

    def enable(self) -> None:
        with self._lock:
            self._enable_requested = True
            self._quick_stop_requested = False

    def disable(self) -> None:
        with self._lock:
            self._enable_requested = False
            self._quick_stop_requested = False
            self._clear_motion_requests()

    def quick_stop(self) -> None:
        with self._lock:
            self._quick_stop_requested = True
            self._enable_requested = False
            self._clear_motion_requests()

    def fault_reset(self) -> None:
        with self._lock:
            self._fault_reset_cycles = FAULT_RESET_WINDOW
            self._clear_motion_requests()

    def set_mode(self, mode: int) -> None:
        """Selects the mode; only while the axis is not moving."""
        if self.status.is_moving:
            raise AxisError("軸還在動作中，請先停止再切換模式。")
        with self._lock:
            self._requested_mode = int(mode)
            self._clear_motion_requests()

    @property
    def requested_mode(self) -> int:
        with self._lock:
            return self._requested_mode

    def move(self, target: float, relative: bool, velocity: float, acc: float, dec: float) -> None:
        """PP or CSP point-to-point move (drive units)."""
        self._require_mode(OM.PROFILE_POSITION, OM.CYCLIC_SYNC_POSITION)
        if velocity <= 0 or acc <= 0 or dec <= 0:
            raise AxisError("速度、加速度、減速度都必須大於 0。")
        with self._lock:
            self._pending_move = _PendingMove(float(target), bool(relative), velocity, acc, dec)
            self._velocity_request = None
            self._halt_requested = False

    def run_velocity(self, velocity: float, acc: float, dec: float) -> None:
        """PV or CSV velocity command; keeps running until stop()."""
        self._require_mode(OM.PROFILE_VELOCITY, OM.CYCLIC_SYNC_VELOCITY)
        if acc <= 0 or dec <= 0:
            raise AxisError("加速度、減速度都必須大於 0。")
        with self._lock:
            self._velocity_request = float(velocity)
            self._vel_acc, self._vel_dec = acc, dec
            self._jog_deadline = math.inf
            self._halt_requested = False

    def jog(self, velocity: float, acc: float) -> None:
        """Jog in PV / CSP / CSV. Must be called repeatedly while the button is held; the axis
        stops by itself when calls stop for JOG_WATCHDOG_SECONDS (dead-man, handoff §6.17)."""
        self._require_mode(OM.PROFILE_VELOCITY, OM.CYCLIC_SYNC_POSITION, OM.CYCLIC_SYNC_VELOCITY)
        if acc <= 0:
            raise AxisError("加速度必須大於 0。")
        with self._lock:
            self._velocity_request = float(velocity)
            self._vel_acc = self._vel_dec = acc
            self._halt_requested = False
            self._pending_move = None
            self._jog_deadline = self._cycle + math.ceil(JOG_WATCHDOG_SECONDS / self.cycle_seconds)

    def start_homing(self) -> None:
        self._require_mode(OM.HOMING)
        with self._lock:
            self._homing_start_requested = True
            self._halt_requested = False

    def stop(self) -> None:
        """Controlled stop: halt bit in profile modes, ramp to zero in cyclic modes."""
        with self._lock:
            self._halt_requested = True
            self._pending_move = None
            self._velocity_request = None
            self._homing_start_requested = False

    def _require_mode(self, *modes: int) -> None:
        with self._lock:
            m = self._requested_mode
        if m not in modes:
            raise AxisError(f"目前模式是 {describe_mode(m)}，這個動作需要：{' / '.join(describe_mode(x) for x in modes)}。")
        st = self.status
        if st.state != DS.OPERATION_ENABLED:
            raise AxisError("驅動器尚未啟用 (Operation enabled)。")
        if st.mode_display != m:
            raise AxisError(f"驅動器目前顯示的模式是 {describe_mode(st.mode_display)}，尚未切換到 {describe_mode(m)}。")

    def _clear_motion_requests(self) -> None:
        self._pending_move = None
        self._velocity_request = None
        self._homing_start_requested = False
        self._halt_requested = False

    # ================================================================== cycle thread

    def on_cycle(self, outputs, inputs, measured_dt: float, communication_ok: bool) -> None:
        """``measured_dt`` is wall-clock time since the previous cycle; diagnostics only."""
        self._cycle += 1
        cycle = self._cycle
        dt = self.cycle_seconds
        img = self._img

        self.channel.read_inputs(inputs, img, dt)
        state = c.decode(img.statusword)
        mode_display = img.mode_display

        self._bad_comm_cycles = 0 if communication_ok else self._bad_comm_cycles + 1
        comm_ok = self._bad_comm_cycles < COMM_LOSS_CYCLES

        with self._lock:
            if self._velocity_request is not None and cycle > self._jog_deadline:
                self._velocity_request = 0.0
                self._jog_deadline = math.inf
            enable = self._enable_requested
            quick_stop = self._quick_stop_requested
            reset_fault = self._fault_reset_cycles > 0
            reset_high = reset_fault and self._fault_reset_cycles <= FAULT_RESET_WINDOW // 2
            if self._fault_reset_cycles > 0:
                self._fault_reset_cycles -= 1
            mode = self._requested_mode
            halt = self._halt_requested
            homing_start = self._homing_start_requested
            self._homing_start_requested = False
            move = self._pending_move
            self._pending_move = None
            vel_req = self._velocity_request
            vel_acc, vel_dec = self._vel_acc, self._vel_dec
            if state == DS.FAULT and not reset_fault:
                self._enable_requested = enable = False   # re-enable needed after reset (§6.14)

        # ---------------- state machine -> controlword base
        if reset_fault:
            cw = c.CW_FAULT_RESET if reset_high else 0
        elif quick_stop and state == DS.OPERATION_ENABLED:
            cw = c.CMD_QUICK_STOP
        elif enable and comm_ok:
            cw = {
                DS.SWITCH_ON_DISABLED: c.CMD_SHUTDOWN,
                DS.READY_TO_SWITCH_ON: c.CMD_SWITCH_ON,
                DS.SWITCHED_ON: c.CMD_ENABLE_OPERATION,
                DS.OPERATION_ENABLED: c.CMD_ENABLE_OPERATION,
                DS.QUICK_STOP_ACTIVE: c.CMD_DISABLE_VOLTAGE,   # leave QS, re-enable normally
            }.get(state, c.CMD_DISABLE_VOLTAGE)
        else:
            if state in (DS.OPERATION_ENABLED, DS.SWITCHED_ON, DS.READY_TO_SWITCH_ON):
                cw = c.CMD_SHUTDOWN
            elif state == DS.QUICK_STOP_ACTIVE:
                cw = c.CMD_QUICK_STOP if quick_stop else c.CMD_DISABLE_VOLTAGE
            else:
                cw = c.CMD_DISABLE_VOLTAGE

        op_enabled = state == DS.OPERATION_ENABLED
        mode_ready = op_enabled and mode_display == mode          # §6.13
        entering_op = op_enabled and not self._was_operation_enabled
        self._was_operation_enabled = op_enabled

        # Keep cyclic targets on the actual values whenever no motion is commanded, so enabling
        # or switching mode never makes the drive jump (§6.12).
        if not mode_ready or entering_op or mode_display != self._last_mode_display:
            self._traj.reset(img.position_actual)
            self._pp_phase = _PpPhase.IDLE
            self._homing_active = False
        self._last_mode_display = mode_display

        img.mode_of_operation = mode
        img.target_position = img.position_actual
        img.target_velocity = 0
        moving = False

        if mode_ready and not reset_fault and not quick_stop and comm_ok:
            if mode == OM.PROFILE_POSITION:
                cw, moving = self._cycle_pp(cw, move, halt)
            elif mode == OM.PROFILE_VELOCITY:
                if vel_req is not None and not halt:
                    img.target_velocity = clamp_int(vel_req)
                    img.profile_acceleration = clamp_u(vel_acc)
                    img.profile_deceleration = clamp_u(vel_dec)
                    moving = vel_req != 0 or img.velocity_actual != 0
                    self._activity = f"速度 {vel_req:.0f}"
                else:
                    if halt:
                        cw |= c.CW_HALT
                    moving = img.velocity_actual != 0
                    self._activity = "減速停止中" if moving else "待機"
            elif mode == OM.HOMING:
                cw, moving = self._cycle_homing(cw, homing_start, halt)
            elif mode == OM.CYCLIC_SYNC_POSITION:
                t = self._traj
                if move is not None:
                    goal = t.goal_position + move.target if move.relative else move.target
                    t.move_to(goal, move.velocity, move.acc, move.dec)
                elif vel_req is not None:
                    t.run_at(vel_req, vel_acc, vel_dec)
                if halt:
                    t.stop()
                t.step(dt)
                img.target_position = clamp_int(t.position)
                img.target_velocity = clamp_int(t.velocity)
                moving = t.is_moving
                self._activity = ((f"移動到 {t.goal_position:.0f}" if t.position_mode else f"速度 {t.velocity:.0f}")
                                  if moving else "待機")
            elif mode == OM.CYCLIC_SYNC_VELOCITY:
                t = self._traj
                if vel_req is not None:
                    t.run_at(vel_req, vel_acc, vel_dec)
                if halt or vel_req is None:
                    t.stop()
                t.step(dt)
                img.target_velocity = clamp_int(t.velocity)
                moving = t.velocity != 0
                self._activity = f"速度 {t.velocity:.0f}" if moving else "待機"
        else:
            self._activity = (
                "通訊中斷，停止送出命令" if not comm_ok else
                "錯誤重置中" if reset_fault else
                "驅動器錯誤" if state == DS.FAULT else
                "快速停止" if quick_stop else
                "啟用中…" if enable and not op_enabled else
                f"等待驅動器切換到 {describe_mode(mode)}" if op_enabled and mode_display != mode else
                "停用")

        if not comm_ok and op_enabled:
            # hold position / zero velocity; the drive's own watchdog takes over (§6.18)
            img.target_position = img.position_actual
            img.target_velocity = 0

        img.controlword = cw
        self.channel.write_outputs(outputs, img)

        self.status = AxisStatus(
            state, img.statusword, cw, mode, mode_display, img.position_actual, img.velocity_actual,
            img.torque_actual, img.error_code, img.following_error, img.target_position,
            img.target_velocity, enable, moving or img.velocity_actual != 0, self._activity, cycle, comm_ok)

    def _cycle_pp(self, cw: int, move: _PendingMove | None, halt: bool) -> tuple[int, bool]:
        img = self._img
        ack = bool(img.statusword & c.SW_OP_MODE_SPECIFIC_12)
        reached = bool(img.statusword & c.SW_TARGET_REACHED)
        moving = False

        if move is not None:
            self._pp_target = move
            self._pp_phase = _PpPhase.WAIT_ACK
        t = self._pp_target
        if t is not None:
            img.target_position = clamp_int(t.target)
            img.profile_velocity = clamp_u(t.velocity)
            img.profile_acceleration = clamp_u(t.acc)
            img.profile_deceleration = clamp_u(t.dec)
            if t.relative:
                cw |= c.CW_RELATIVE
        cw |= c.CW_CHANGE_SET_IMMEDIATELY

        ph = self._pp_phase
        if ph == _PpPhase.WAIT_ACK:
            cw |= c.CW_NEW_SET_POINT            # rising edge of bit 4 = new set-point
            if ack:
                self._pp_phase = _PpPhase.WAIT_ACK_CLEAR
            self._activity = "送出目標位置"
            moving = True
        elif ph == _PpPhase.WAIT_ACK_CLEAR:
            if not ack:                          # bit 4 released; drive drops ack
                self._pp_phase = _PpPhase.MOVING
            self._activity = "驅動器已接受目標"
            moving = True
        elif ph == _PpPhase.MOVING:
            moving = not reached
            self._activity = "到達目標" if reached else f"移動到 {t.target if t else 0:.0f}"
            if reached:
                self._pp_phase = _PpPhase.IDLE
        else:
            self._activity = "待機"
        if halt:
            cw |= c.CW_HALT
            self._pp_phase = _PpPhase.IDLE
            self._activity = "停止"
        return cw, moving

    def _cycle_homing(self, cw: int, start: bool, halt: bool) -> tuple[int, bool]:
        sw = self._img.statusword
        attained = bool(sw & c.SW_OP_MODE_SPECIFIC_12)
        error = bool(sw & c.SW_OP_MODE_SPECIFIC_13)
        reached = bool(sw & c.SW_TARGET_REACHED)
        moving = False

        if start:
            self._homing_active = True
            self._homing_cycles = 0
        if self._homing_active:
            cw |= c.CW_NEW_SET_POINT             # bit 4 high = homing operation start
            moving = True
            self._activity = "原點復歸中"
            # The statusword shows the previous result until the drive has seen bit 4; judge
            # completion only after the start has been held for a while (§6.16).
            self._homing_cycles += 1
            settled = self._homing_cycles * self.cycle_seconds >= HOMING_START_HOLD_SECONDS - 1e-12
            if settled and error:
                self._homing_active = False
                self._activity = "原點復歸錯誤"
            elif settled and attained and reached:
                self._homing_active = False
                self._activity = "原點復歸完成"
                moving = False
        else:
            self._activity = "原點復歸錯誤" if error else "原點復歸完成" if attained else "待機"
        if halt:
            cw |= c.CW_HALT
            self._homing_active = False
        return cw, moving
