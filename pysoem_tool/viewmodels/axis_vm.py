"""Axis (CiA 402) tab (port of AxisViewModel.cs). tk-free."""
from __future__ import annotations

import collections
import struct
import time
from dataclasses import dataclass
from typing import Callable

from ..motion import cia402 as c
from ..motion.channel import SimulatedAxisChannel, SimulationRunner
from ..motion.channel_ethercat import EtherCatAxisChannel
from ..motion.cia402 import DriveState, OperationMode
from ..motion.controller import AxisController
from ..motion.sim_drive import SimulatedCia402Drive
from ..observable import Command, Observable, computed
from ..services.ethercat import EcState, EtherCatService
from .base import Ticker, Worker

DS, OM = DriveState, OperationMode
TREND_LENGTH = 200          # 10 s at 50 ms


@dataclass(frozen=True)
class AxisSource:
    name: str
    slave: int | None

    def __str__(self) -> str:          # readable combobox text (handoff §6.26)
        return self.name


@dataclass(frozen=True)
class ModeItem:
    mode: OperationMode
    name: str

    def __str__(self) -> str:
        return self.name


MODES = (ModeItem(OM.PROFILE_POSITION, "PP 位置模式"), ModeItem(OM.PROFILE_VELOCITY, "PV 速度模式"),
         ModeItem(OM.HOMING, "HM 原點復歸"), ModeItem(OM.CYCLIC_SYNC_POSITION, "CSP 同步位置"),
         ModeItem(OM.CYCLIC_SYNC_VELOCITY, "CSV 同步速度"))

SIM_SOURCE = AxisSource("模擬軸（不需硬體）", None)


def visual_state(state: DriveState, sw: int) -> str:
    """HMI style: only abnormal states stand out."""
    warn = bool(sw & c.SW_WARNING)
    if state in (DS.FAULT, DS.FAULT_REACTION_ACTIVE):
        return "fault"
    if state == DS.QUICK_STOP_ACTIVE:
        return "warning"
    if state == DS.OPERATION_ENABLED:
        return "warning" if warn else "enabled"
    if state in (DS.READY_TO_SWITCH_ON, DS.SWITCHED_ON):
        return "ready"
    return "warning" if warn else "inactive"


class AxisViewModel:
    def __init__(self, service: EtherCatService, log: Callable[[str], None], worker: Worker) -> None:
        self._svc = service
        self._log = log
        self._worker = worker
        self._controller: AxisController | None = None
        self._sim_runner: SimulationRunner | None = None
        self._sim_drive: SimulatedCia402Drive | None = None
        self._real: EtherCatAxisChannel | None = None
        self._pos_trend: collections.deque[float] = collections.deque(maxlen=TREND_LENGTH)
        self._vel_trend: collections.deque[float] = collections.deque(maxlen=TREND_LENGTH)
        self._last_state = DS.UNKNOWN
        self._jog_error_shown = False

        # source / attach
        self.sources: Observable[tuple[AxisSource, ...]] = Observable((SIM_SOURCE,))
        self.selected_source: Observable[AxisSource | None] = Observable(SIM_SOURCE)
        self.bus_open = Observable(False)
        self.is_attached = Observable(False)
        self.is_busy = Observable(False)
        self.is_simulated = Observable(False)
        self.is_real = Observable(False)
        self.controlled_slave: Observable[int | None] = Observable(None)
        self.attached_name = Observable("")
        self.rx_lines: Observable[tuple[str, ...]] = Observable(())
        self.tx_lines: Observable[tuple[str, ...]] = Observable(())
        self.mapping_note = Observable("")

        # status
        self.state_text = Observable("未連接")
        self.visual = Observable("inactive")
        self.statusword = Observable("-")
        self.controlword = Observable("-")
        self.mode_display = Observable("-")
        self.position = Observable("0")
        self.velocity = Observable("0")
        self.torque = Observable("0")
        self.target_position = Observable("0")
        self.error_code = Observable("-")
        self.activity = Observable("")
        self.comm_ok = Observable(True)
        self.status_bits: Observable[tuple[tuple[str, bool], ...]] = Observable(
            tuple((c.bit_name(b, 0), False) for b in range(16)))
        self.trend: Observable[tuple[tuple[float, ...], tuple[float, ...]]] = Observable(((), ()))
        self.trend_range = Observable("")

        # mode and parameters (entries are strings, parsed on use)
        self.modes = MODES
        self.selected_mode: Observable[ModeItem] = Observable(MODES[0])
        self.pp_target, self.pp_velocity = Observable("10000"), Observable("10000")
        self.pp_acc, self.pp_dec, self.pp_relative = Observable("50000"), Observable("50000"), Observable(False)
        self.pv_velocity, self.pv_acc, self.pv_dec = Observable("5000"), Observable("50000"), Observable("50000")
        self.hm_method, self.hm_speed_switch, self.hm_speed_zero = Observable("35"), Observable("5000"), Observable("1000")
        self.hm_acc, self.hm_offset = Observable("50000"), Observable("0")
        self.csp_target, self.csp_velocity = Observable("0"), Observable("10000")
        self.csp_acc, self.csp_relative = Observable("50000"), Observable(False)
        self.csv_velocity, self.csv_acc = Observable("5000"), Observable("50000")
        self.jog_velocity, self.jog_acc = Observable("5000"), Observable("50000")
        self.dc_supported = Observable(False)
        self.dc_enabled = Observable(False)
        self.dc_shift_us = Observable("0")

        sel = self.selected_mode
        self.show_pp = computed(lambda m: m.mode == OM.PROFILE_POSITION, sel)
        self.show_pv = computed(lambda m: m.mode == OM.PROFILE_VELOCITY, sel)
        self.show_hm = computed(lambda m: m.mode == OM.HOMING, sel)
        self.show_csp = computed(lambda m: m.mode == OM.CYCLIC_SYNC_POSITION, sel)
        self.show_csv = computed(lambda m: m.mode == OM.CYCLIC_SYNC_VELOCITY, sel)
        self.show_jog = computed(lambda m: m.mode in (OM.PROFILE_VELOCITY, OM.CYCLIC_SYNC_POSITION,
                                                      OM.CYCLIC_SYNC_VELOCITY), sel)
        self.cyclic_warning = computed(
            lambda m, sim: ("CSP / CSV：建議 cyclic 週期 2 ms 以上並啟用 DC SYNC0；狀態列「掉 frame」持續增加時不要使用。"
                            if m.mode in (OM.CYCLIC_SYNC_POSITION, OM.CYCLIC_SYNC_VELOCITY) and not sim else ""),
            sel, self.is_simulated)
        self.source_is_real = computed(lambda s: s is not None and s.slave is not None, self.selected_source)

        can_attach = computed(lambda a, b, s: not a and not b and s is not None,
                              self.is_attached, self.is_busy, self.selected_source)
        can_detach = computed(lambda a, b: a and not b, self.is_attached, self.is_busy)
        attached = self.is_attached
        can_cfg = computed(lambda r, b: r and not b, self.is_real, self.is_busy)
        # Works on the selected slave whether or not it is attached (handoff §4 / §6): a drive
        # whose PDO lacks 0x6040/0x6041 cannot be attached but can be fixed from here.
        can_remap = computed(
            lambda b, o, s, a, cs: (not b and o and s is not None and s.slave is not None
                                    and (not a or cs == s.slave)),
            self.is_busy, self.bus_open, self.selected_source, self.is_attached, self.controlled_slave)

        self.cmd_attach = Command(self.attach, can_attach)
        self.cmd_detach = Command(self.detach, can_detach)
        self.cmd_enable = Command(lambda: self._do(lambda k: k.enable(), "啟用"), attached)
        self.cmd_disable = Command(lambda: self._do(lambda k: k.disable(), "停用"), attached)
        self.cmd_quick_stop = Command(lambda: self._do(lambda k: k.quick_stop(), "快速停止"), attached)
        self.cmd_fault_reset = Command(lambda: self._do(lambda k: k.fault_reset(), "錯誤重置"), attached)
        self.cmd_stop = Command(lambda: self._do(lambda k: k.stop(), "停止"), attached)
        self.cmd_inject_fault = Command(self.inject_fault, self.is_simulated)
        self.cmd_apply_mode = Command(self.apply_mode, can_detach)
        self.cmd_pp_move = Command(self.pp_move, attached)
        self.cmd_pv_start = Command(self.pv_start, attached)
        self.cmd_hm_start = Command(self.homing_start, can_detach)
        self.cmd_csp_move = Command(self.csp_move, attached)
        self.cmd_csv_start = Command(self.csv_start, attached)
        self.cmd_jog_plus = Command(lambda: self.jog(+1), attached)
        self.cmd_jog_minus = Command(lambda: self.jog(-1), attached)
        self.cmd_apply_dc = Command(self.apply_dc, can_cfg)
        self.cmd_apply_pdo = Command(self.apply_recommended_pdo, can_remap)

        self._ticker = Ticker(0.05, self._tick, "axis ui tick")

    # ================================================================== sources / attach

    def refresh_sources(self, slaves, bus_open: bool) -> None:
        keep = self.selected_source.value
        srcs = (SIM_SOURCE,) + tuple(AxisSource(f"#{s.position} {s.name}", s.position) for s in slaves)
        self.sources.value = srcs
        self.bus_open.value = bus_open
        self.selected_source.value = next((s for s in srcs if keep and s.name == keep.name), SIM_SOURCE)

    def shutdown(self) -> None:
        if self.is_attached.value:
            self._detach_core(None)

    def on_bus_closing(self) -> None:
        if self._real is not None:
            self._detach_core("EtherCAT 斷線")

    def attach(self) -> None:
        src = self.selected_source.value
        if src is None:
            return
        self.is_busy.value = True
        if src.slave is None:
            drive = SimulatedCia402Drive()
            ch = SimulatedAxisChannel(drive)
            self._sim_drive = drive
            self._controller = AxisController(ch, 0.001)
            self._sim_runner = SimulationRunner(self._controller, 1)
            self._show_mapping(ch, "模擬軸的所有 CiA 402 物件都在 PDO 中。")
            self._log("[軸] 已連接模擬軸（1 ms 週期）。")
            self._attached(src)
            return

        def job():
            if not self._svc.is_open:
                raise RuntimeError("請先連線 EtherCAT。")
            ch = EtherCatAxisChannel.create(self._svc, src.slave, src.name)
            if not ch.is_mapped(c.MODES_OF_OPERATION_DISPLAY):
                ch.assumed_mode_display = ch.read_parameter(c.MODES_OF_OPERATION_DISPLAY, 0, 1, True)
            return ch

        def done(ch: EtherCatAxisChannel):
            self._real = ch
            self._controller = AxisController(ch, self._svc.cycle_time_ms / 1000.0)
            self._svc.add_cycle_handler(self._controller)
            self.controlled_slave.value = ch.slave
            notes = []
            if not ch.is_mapped(c.MODES_OF_OPERATION):
                notes.append("0x6060 不在 PDO，切換模式改用 SDO")
            if not ch.is_mapped(c.MODES_OF_OPERATION_DISPLAY):
                notes.append("0x6061 不在 PDO，模式顯示以 SDO 確認")
            if not ch.is_mapped(c.TARGET_POSITION):
                notes.append("沒有 0x607A：無法使用 PP / CSP")
            if not ch.is_mapped(c.TARGET_VELOCITY):
                notes.append("沒有 0x60FF：無法使用 PV / CSV")
            if not ch.is_mapped(c.POSITION_ACTUAL_VALUE):
                notes.append("沒有 0x6064：看不到實際位置")
            self._show_mapping(ch, "；".join(notes) or "PDO 包含 CSP / CSV / PP / PV 需要的物件。")
            self.dc_supported.value = self._svc.layouts[ch.slave - 1].has_dc
            self.dc_enabled.value = self._svc.is_dc_sync0_active(ch.slave)
            self._log(f"[軸] 已連接 {src.name}：RxPDO {len(ch.rx_pdo)} 項、TxPDO {len(ch.tx_pdo)} 項。{self.mapping_note.value}")
            self._attached(src)

        def fail(e):
            self._log(f"[軸] 連接失敗：{e}")
            self._detach_core(None)
            self.is_busy.value = False

        self._worker.run(job, done, fail)

    def _attached(self, src: AxisSource) -> None:
        self.attached_name.value = src.name
        self._pos_trend.clear()
        self._vel_trend.clear()
        self.is_simulated.value = self._sim_drive is not None
        self.is_real.value = self._real is not None
        self.is_attached.value = True
        self.is_busy.value = False
        self._ticker.start()

    def detach(self) -> None:
        k = self._controller
        self.is_busy.value = True

        def job():
            if k is not None and k.status.state == DS.OPERATION_ENABLED:
                k.disable()
                for _ in range(20):
                    if k.status.state != DS.OPERATION_ENABLED:
                        break
                    time.sleep(0.025)

        def done(_):
            self._detach_core("已分離")
            self.is_busy.value = False
        self._worker.run(job, done, lambda e: done(None))

    def _detach_core(self, reason: str | None) -> None:
        self._ticker.stop()
        if self._controller is not None and self._real is not None:
            self._svc.remove_cycle_handler(self._controller)
        if self._sim_runner is not None:
            self._sim_runner.dispose()
        self._sim_runner = self._sim_drive = self._real = self._controller = None
        self.controlled_slave.value = None
        self.rx_lines.value = self.tx_lines.value = ()
        self.mapping_note.value = ""
        self.attached_name.value = ""
        self.is_attached.value = self.is_simulated.value = self.is_real.value = False
        self.state_text.value = "未連接"
        self.visual.value = "inactive"
        self.activity.value = ""
        if reason:
            self._log(f"[軸] {reason}。")

    def _show_mapping(self, ch, note: str) -> None:
        self.rx_lines.value = tuple(e.describe() for e in ch.rx_pdo)
        self.tx_lines.value = tuple(e.describe() for e in ch.tx_pdo)
        self.mapping_note.value = note

    # ================================================================== status (polled)

    def _tick(self) -> None:
        k = self._controller
        if k is None:
            return
        s = k.status
        self.state_text.value = str(s.state)
        self.visual.value = visual_state(s.state, s.statusword)
        self.statusword.value = f"0x{s.statusword:04X}"
        self.controlword.value = f"0x{s.controlword:04X}"
        self.mode_display.value = c.describe_mode(s.mode_display)
        self.position.value = f"{s.position_actual:,}"
        self.velocity.value = f"{s.velocity_actual:,}"
        self.torque.value = f"{s.torque_actual:,}"
        self.target_position.value = f"{s.target_position:,}"
        self.error_code.value = "-" if s.error_code == 0 else f"0x{s.error_code:04X}"
        self.activity.value = s.activity
        self.comm_ok.value = s.communication_ok
        self.status_bits.value = tuple((c.bit_name(b, s.mode_display), bool(s.statusword >> b & 1))
                                       for b in range(16))
        if s.state != self._last_state:
            if s.state == DS.FAULT:
                self._log(f"[軸] 驅動器錯誤，Error code {self.error_code.value}。")
            self._last_state = s.state
        self._pos_trend.append(float(s.position_actual))
        self._vel_trend.append(float(s.velocity_actual))
        self.trend.value = (tuple(self._pos_trend), tuple(self._vel_trend))
        p, v = self._pos_trend, self._vel_trend
        self.trend_range.value = f"位置 {min(p):.0f} … {max(p):.0f}    速度 {min(v):.0f} … {max(v):.0f}"
        if err := self._svc.take_handler_error():
            self._log(f"[軸] cyclic 例外：{err}")

    # ================================================================== commands

    def _do(self, action: Callable[[AxisController], None], what: str, log_it: bool = True) -> bool:
        k = self._controller
        if k is None:
            return False
        try:
            action(k)
            if log_it:
                self._log(f"[軸] {what}")
            return True
        except Exception as e:  # noqa: BLE001
            self._log(f"[軸] {what}失敗：{e}")
            return False

    def inject_fault(self) -> None:
        if self._sim_drive is not None:
            self._sim_drive.inject_fault(0x2310)
            self._log("[軸] 模擬錯誤 0x2310（過電流）")

    @staticmethod
    def _num(obs: Observable[str], what: str) -> float:
        try:
            return float(obs.value.replace(",", "").strip())
        except ValueError:
            raise ValueError(f"「{what}」不是數字：{obs.value!r}") from None

    def apply_mode(self) -> None:
        k, m = self._controller, self.selected_mode.value
        if k is None:
            return
        try:
            k.set_mode(m.mode)
        except Exception as e:  # noqa: BLE001
            self._log(f"[軸] 切換模式失敗：{e}")
            return
        self.is_busy.value = True
        ch = k.channel

        def job():
            if not ch.is_mapped(c.MODES_OF_OPERATION):
                ch.write_parameter(c.MODES_OF_OPERATION, 0, int(m.mode), 1)
            if isinstance(ch, EtherCatAxisChannel) and not ch.is_mapped(c.MODES_OF_OPERATION_DISPLAY):
                shown = 0
                for _ in range(20):
                    shown = ch.read_parameter(c.MODES_OF_OPERATION_DISPLAY, 0, 1, True)
                    if shown == m.mode:
                        break
                    time.sleep(0.05)
                ch.assumed_mode_display = shown
                if shown != m.mode:
                    raise RuntimeError(f"驅動器 0x6061 顯示 {shown}，沒有切換到 {int(m.mode)}。")

        def done(_):
            self._log(f"[軸] 模式 → {m.name}")
            self.is_busy.value = False

        def fail(e):
            self._log(f"[軸] 切換模式失敗：{e}")
            self.is_busy.value = False
        self._worker.run(job, done, fail)

    def _write_if_unmapped(self, ch, index: int, value: float) -> None:
        if not ch.is_mapped(index):
            ch.write_parameter(index, 0, int(abs(value)), 4)

    def pp_move(self) -> None:
        k = self._controller
        if k is None:
            return
        try:
            t, v = self._num(self.pp_target, "目標"), self._num(self.pp_velocity, "速度")
            a, d = self._num(self.pp_acc, "加速度"), self._num(self.pp_dec, "減速度")
        except ValueError as e:
            self._log(f"[軸] PP 移動失敗：{e}")
            return
        rel = self.pp_relative.value

        def job():
            for idx, val in ((c.PROFILE_VELOCITY, v), (c.PROFILE_ACCELERATION, a), (c.PROFILE_DECELERATION, d)):
                self._write_if_unmapped(k.channel, idx, val)
            k.move(t, rel, v, a, d)
        self._worker.run(job, lambda _: self._log(
            f"[軸] PP {'相對' if rel else '絕對'}移動 {t:.0f}（v {v:.0f}, a {a:.0f}, d {d:.0f}）"),
            lambda e: self._log(f"[軸] PP 移動失敗：{e}"))

    def pv_start(self) -> None:
        k = self._controller
        if k is None:
            return
        try:
            v, a, d = self._num(self.pv_velocity, "速度"), self._num(self.pv_acc, "加速度"), self._num(self.pv_dec, "減速度")
        except ValueError as e:
            self._log(f"[軸] PV 啟動失敗：{e}")
            return

        def job():
            self._write_if_unmapped(k.channel, c.PROFILE_ACCELERATION, a)
            self._write_if_unmapped(k.channel, c.PROFILE_DECELERATION, d)
            k.run_velocity(v, a, d)
        self._worker.run(job, lambda _: self._log(f"[軸] PV 速度 {v:.0f}"),
                         lambda e: self._log(f"[軸] PV 啟動失敗：{e}"))

    def homing_start(self) -> None:
        k = self._controller
        if k is None:
            return
        try:
            method = int(self._num(self.hm_method, "方法"))
            sw, sz = self._num(self.hm_speed_switch, "搜尋速度"), self._num(self.hm_speed_zero, "歸零速度")
            acc, off = self._num(self.hm_acc, "加速度"), int(self._num(self.hm_offset, "原點偏移"))
        except ValueError as e:
            self._log(f"[軸] 原點復歸失敗：{e}")
            return
        self.is_busy.value = True
        ch = k.channel

        def job():
            ch.write_parameter(c.HOMING_METHOD, 0, method, 1)
            ch.write_parameter(c.HOMING_SPEEDS, 1, int(sw), 4)
            ch.write_parameter(c.HOMING_SPEEDS, 2, int(sz), 4)
            ch.write_parameter(c.HOMING_ACCELERATION, 0, int(acc), 4)
            ch.write_parameter(c.HOME_OFFSET, 0, off, 4)
            k.start_homing()

        def done(_):
            self._log(f"[軸] 原點復歸 method {method}（搜尋 {sw:.0f}, 歸零 {sz:.0f}, offset {off}）")
            self.is_busy.value = False

        def fail(e):
            self._log(f"[軸] 原點復歸失敗：{e}")
            self.is_busy.value = False
        self._worker.run(job, done, fail)

    def csp_move(self) -> None:
        try:
            t, v, a = self._num(self.csp_target, "目標"), self._num(self.csp_velocity, "速度"), self._num(self.csp_acc, "加速度")
        except ValueError as e:
            self._log(f"[軸] CSP 移動失敗：{e}")
            return
        rel = self.csp_relative.value
        self._do(lambda k: k.move(t, rel, v, a, a),
                 f"CSP {'相對' if rel else '絕對'}移動 {t:.0f}（v {v:.0f}, a {a:.0f}）")

    def csv_start(self) -> None:
        try:
            v, a = self._num(self.csv_velocity, "速度"), self._num(self.csv_acc, "加速度")
        except ValueError as e:
            self._log(f"[軸] CSV 啟動失敗：{e}")
            return
        self._do(lambda k: k.run_velocity(v, a, a), f"CSV 速度 {v:.0f}")

    def jog(self, direction: int) -> None:
        """Called repeatedly (every 0.1 s) while a JOG button is held."""
        k = self._controller
        if k is None:
            return
        try:
            v, a = abs(self._num(self.jog_velocity, "寸動速度")), self._num(self.jog_acc, "寸動加速度")
            k.jog(direction * v, a)
            self._jog_error_shown = False
        except Exception as e:  # noqa: BLE001
            if not self._jog_error_shown:
                self._log(f"[軸] 寸動失敗：{e}")
            self._jog_error_shown = True

    # ================================================================== DC / PDO (real axis)

    def apply_dc(self) -> None:
        ch = self._real
        if ch is None:
            return
        enable = self.dc_enabled.value
        try:
            shift = int(self._num(self.dc_shift_us, "shift"))
        except ValueError as e:
            self._log(f"[軸] DC 設定失敗：{e}")
            return
        self.is_busy.value = True

        def job():
            self._svc.configure_dc_sync0(ch.slave, enable, shift * 1000)
            # 0x1C32:01 sync mode: 0 = free run, 2 = DC SYNC0. Optional object.
            try:
                self._svc.sdo_write(ch.slave, c.SYNC_MANAGER_OUTPUT_PARAMETER, 1, struct.pack("<H", 2 if enable else 0))
            except Exception:  # noqa: BLE001
                pass

        def done(_):
            self._log(f"[軸] DC SYNC0 " + (f"啟用，週期 {self._svc.cycle_time_ms} ms，shift {shift} µs。" if enable else "停用。"))
            self.is_busy.value = False

        def fail(e):
            self.dc_enabled.value = self._svc.is_dc_sync0_active(ch.slave)
            self._log(f"[軸] DC 設定失敗：{e}")
            self.is_busy.value = False
        self._worker.run(job, done, fail)

    def apply_recommended_pdo(self) -> None:
        src = self.selected_source.value
        if src is None or src.slave is None:
            return
        slave = src.slave
        self.is_busy.value = True
        k = self._controller

        def job():
            if k is not None:
                k.disable()
                time.sleep(0.1)
            if self.is_attached.value:
                self._detach_core(None)
            self._log(f"[軸] 套用建議 PDO 到 slave #{slave}：切到 PRE-OP …")
            self._svc.change_state(EcState.PREOP)
            EtherCatAxisChannel.apply_recommended_mapping(self._svc, slave)
            self._log("[軸] PDO 已寫入，切回 SAFE-OP 重新讀取配置 …")
            self._svc.change_state(EcState.SAFEOP, allow_layout_change=True)

        def done(_):
            self._log("[軸] 完成。PDO 設定存在驅動器 RAM，斷電後會恢復原設定。請按「連接軸」。")
            self.is_busy.value = False

        def fail(e):
            self._log(f"[軸] 套用 PDO 失敗：{e}")
            self.is_busy.value = False
        self._worker.run(job, done, fail)
