"""EtherCAT master service: owns the pysoem Master, runs the cyclic process-data thread and
exposes thread-safe access to every slave's output / input data.

No tkinter here; the service can be driven from a console script or pytest.

Threads
-------
* cyclic thread ("EtherCAT cyclic"): outputs -> send -> receive -> inputs -> cycle handlers ->
  DC alignment. It does not take ``_api_lock``; SOEM allows process data and mailbox / state
  access in parallel, so an SDO timeout never stalls the cyclic exchange.
* callers (UI worker threads): connect, state changes, SDO, DC. Serialised by ``_api_lock``.

GIL notes (verified with pysoem 1.1.13, see docs/ARCHITECTURE.md):
* ``send_processdata`` / ``receive_processdata`` / ``sdo_read`` / ``sdo_write`` / ``config_init``
  release the GIL only when called with ``release_gil=True`` (keyword only) -> always pass it.
* ``Master.state_check`` / ``CdefSlave.state_check`` never release the GIL, so a 2 s wait would
  freeze the cyclic thread. State changes are therefore confirmed by polling ``read_state()``
  (about 0.1 ms with the GIL held) with short sleeps in between.
* ``config_map`` / ``config_dc`` hold the GIL, but they only run while the cyclic thread is stopped.
"""
from __future__ import annotations

import collections
import gc
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Callable, Protocol, Sequence

from ..npcap import load_pysoem
from . import winrt


class EcState(IntEnum):
    NONE = 0x00
    INIT = 0x01
    PREOP = 0x02
    BOOT = 0x03
    SAFEOP = 0x04
    OP = 0x08

    @property
    def label(self) -> str:
        return {0: "NONE", 1: "INIT", 2: "PRE-OP", 3: "BOOT", 4: "SAFE-OP", 8: "OP"}[int(self)]


STATE_ACK = 0x10   # also the error flag in the AL status register

STATE_TIMEOUT_S = 2.0
SPIKE_FACTOR = 3.0
MAX_QUEUED_EVENTS = 200


def state_label(raw: int) -> str:
    try:
        s = EcState(raw & 0x0F).label
    except ValueError:
        s = f"0x{raw & 0x0F:X}"
    return s + (" + ERR" if raw & STATE_ACK else "")


class EtherCatError(RuntimeError):
    """Operation failed; message is user facing (Traditional Chinese)."""


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    desc: str

    @property
    def display(self) -> str:
        return f"{self.desc}  [{self.name}]"

    def __str__(self) -> str:          # readable combobox text (handoff §6.26)
        return self.display


@dataclass(frozen=True)
class SlaveLayout:
    """Process-data size of one slave. pysoem gives each slave its own data area, so no IOmap
    offsets are needed (bit positions inside a slave start at 0)."""
    position: int
    output_bytes: int
    input_bytes: int
    has_dc: bool

    @property
    def output_bits(self) -> int:
        return self.output_bytes * 8

    @property
    def input_bits(self) -> int:
        return self.input_bytes * 8


@dataclass(frozen=True)
class SlaveInfo:
    position: int
    name: str
    state: int                # raw AL status (state | error flag)
    al_status: int            # AL status code
    al_status_text: str
    vendor: int
    product: int
    revision: int
    config_address: int
    output_bits: int
    input_bits: int
    has_dc: bool

    @property
    def has_error(self) -> bool:
        return bool(self.state & STATE_ACK)


@dataclass(frozen=True)
class CycleStats:
    cycles: int = 0
    last_wkc: int = 0
    expected_wkc: int = 0
    wkc_errors: int = 0
    lost_frames: int = 0
    avg_cycle_us: float = 0.0
    max_cycle_us: float = 0.0
    dc_sync_active: bool = False
    dc_deviation_ns: int = 0


@dataclass(frozen=True)
class CycleEvent:
    """A cycle that lost its frame, missed WKC or overran 3x its period (handoff §6.7)."""
    time: datetime
    cycle: int
    wkc: int
    period_us: float
    rx_us: float
    gc_runs: int


class CycleHandler(Protocol):
    def on_cycle(self, outputs: list[bytearray], inputs: list[bytes], dt: float, comm_ok: bool) -> None:
        """Runs on the cyclic thread after this cycle's inputs arrived (index 0 = slave #1).
        Write into ``outputs``; it goes out with the next frame. Must not block."""


# GC runs are counted through gc.callbacks so abnormal cycles can say "GC happened".
_gc_runs = 0


def _gc_cb(phase: str, _info: dict) -> None:
    global _gc_runs
    if phase == "start":
        _gc_runs += 1


gc.callbacks.append(_gc_cb)


def describe_exception(e: BaseException) -> str:
    """User-facing text for pysoem exceptions (SdoError has the abort code)."""
    ps = load_pysoem()
    if isinstance(e, ps.SdoError):
        return (f"SDO abort 0x{e.abort_code:08X}（{e.desc}），slave #{e.slave_pos} "
                f"0x{e.index:04X}:{e.subindex:02X}")
    if isinstance(e, ps.WkcError):
        return f"slave 沒有回應（WKC {getattr(e, 'wkc', '?')}）"
    if isinstance(e, ps.MailboxError):
        return f"mailbox 錯誤：{getattr(e, 'desc', e)}"
    if isinstance(e, ps.PacketError):
        return f"封包錯誤：{getattr(e, 'desc', e)}"
    return str(e) or type(e).__name__


class EtherCatService:
    def __init__(self) -> None:
        self._ps = None
        self._api_lock = threading.RLock()       # serialises non-cyclic master calls
        self._pd_lock = threading.Lock()         # guards _outputs / _inputs
        self._master = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._pd_active = False                  # all slaves expected in SAFE-OP or OP

        self._outputs: list[bytearray] = []
        self._inputs: list[bytes] = []
        self.layouts: list[SlaveLayout] = []
        self._config_addr: list[int] = []

        self._handlers: tuple[CycleHandler, ...] = ()
        self._handler_lock = threading.Lock()
        self._handler_error: str | None = None

        self._dc_slaves: dict[int, int] = {}     # slave -> SYNC0 shift (ns)
        self._dc_sync_active = False
        self._dc_deviation_ns = 0

        self._events: collections.deque[CycleEvent] = collections.deque(maxlen=MAX_QUEUED_EVENTS)
        self._stats_lock = threading.Lock()
        self._expected_wkc = 0
        self._reset_counters()

        self.cycle_time_ms = 2
        self.layout_changed: list[Callable[[], None]] = []   # called on a worker thread

        self._saved_priority: int | None = None
        self._saved_switch: float | None = None

    # ------------------------------------------------------------------ properties

    @property
    def is_open(self) -> bool:
        return self._master is not None

    @property
    def slave_count(self) -> int:
        return len(self.layouts)

    @property
    def _m(self):
        if self._master is None:
            raise EtherCatError("尚未連線。")
        return self._master

    @staticmethod
    def get_adapters() -> list[AdapterInfo]:
        ps = load_pysoem()
        out = []
        for a in ps.find_adapters():
            desc = a.desc.decode(errors="replace") if isinstance(a.desc, bytes) else str(a.desc)
            out.append(AdapterInfo(a.name, desc))
        return out

    # ------------------------------------------------------------------ open / close

    def open(self, ifname: str, cycle_time_ms: int) -> list[SlaveInfo]:
        """Opens the adapter, configures slaves (they end in SAFE-OP) and starts cycling."""
        if self._master is not None:
            raise EtherCatError("Master 已經開啟。")
        ps = self._ps = load_pysoem()
        m = ps.Master()
        try:
            try:
                m.open(ifname)
            except Exception as e:
                raise EtherCatError(f"無法在 '{ifname}' 上開啟網卡（網卡不存在、Npcap 權限或驅動問題）：{e}") from e
            count = m.config_init(release_gil=True)
            if count <= 0:
                raise EtherCatError("沒有找到任何 EtherCAT slave，請檢查接線與電源。")
            used = self._config_map(m)
            m.config_dc()
            self._master = m
            self._build_layout(m, used)
            self._pd_active = True               # config_map leaves slaves in SAFE-OP
            self.cycle_time_ms = max(1, int(cycle_time_ms))
            self._start_cyclic()
            return self.read_slaves()
        except BaseException:
            self._master = None
            try:
                m.close()
            except Exception:
                pass
            raise

    def close(self) -> None:
        self._stop_cyclic()
        with self._api_lock:
            m = self._master
            if m is None:
                return
            try:
                m.state = int(EcState.INIT)
                m.write_state()
                self._wait_state(m, EcState.INIT, 0.5, raise_on_timeout=False)
                for pos in list(self._dc_slaves):
                    m.slaves[pos - 1].dc_sync(False, self._cycle_ns())
            except Exception:
                pass                              # best effort
            self._dc_slaves.clear()
            self._dc_sync_active = False
            try:
                m.close()
            finally:
                self._master = None
        with self._handler_lock:
            self._handlers = ()
        self.layouts = []

    # ------------------------------------------------------------------ cycle handlers

    def add_cycle_handler(self, h: CycleHandler) -> None:
        with self._handler_lock:
            self._handlers = (*self._handlers, h)

    def remove_cycle_handler(self, h: CycleHandler) -> None:
        with self._handler_lock:
            self._handlers = tuple(x for x in self._handlers if x is not h)

    def take_handler_error(self) -> str | None:
        e, self._handler_error = self._handler_error, None
        return e

    # ------------------------------------------------------------------ DC

    def _cycle_ns(self) -> int:
        return self.cycle_time_ms * 1_000_000

    def is_dc_sync0_active(self, slave: int) -> bool:
        with self._api_lock:
            return slave in self._dc_slaves

    def configure_dc_sync0(self, slave: int, enable: bool, shift_ns: int = 0) -> None:
        """Activates / deactivates SYNC0 (period = cycle time). Only below OP; re-applied after
        every reconfiguration. SOEM's dcsync0 has no return value, so the result is verified by
        reading ESC register 0x0981 (sync activation)."""
        with self._api_lock:
            m = self._m
            if not self.layouts[slave - 1].has_dc:
                raise EtherCatError(f"slave #{slave} 不支援 DC。")
            lowest = m.read_state() & 0x0F
            if lowest == EcState.OP:
                raise EtherCatError("DC SYNC0 只能在 OP 以下設定，請先切到 SAFE-OP。")
            m.slaves[slave - 1].dc_sync(bool(enable), self._cycle_ns(), int(shift_ns))
            act = self.read_register(slave, 0x0981, 1)[0]
            if enable and (act & 0x03) != 0x03:
                raise EtherCatError(f"slave #{slave} 的 0x0981 = 0x{act:02X}，SYNC0 沒有啟動。")
            if enable:
                self._dc_slaves[slave] = int(shift_ns)
            else:
                self._dc_slaves.pop(slave, None)
            self._dc_sync_active = bool(self._dc_slaves)

    def read_register(self, slave: int, address: int, length: int) -> bytes:
        """ESC register read (FPRD), e.g. 0x0981 sync activation, 0x09A0 SYNC0 cycle time."""
        with self._api_lock:
            try:
                return bytes(self._m.slaves[slave - 1]._fprd(address, length))
            except EtherCatError:
                raise
            except Exception as e:
                raise EtherCatError(f"讀取 slave #{slave} 暫存器 0x{address:04X} 失敗：{e}") from e

    # ------------------------------------------------------------------ state

    def change_state(self, target: EcState, allow_layout_change: bool = False) -> list[str]:
        """Moves all slaves to ``target`` following the ESM rules (handoff §6.3-6.5):
        downward transitions are written directly; upward ones step through each state.
        From INIT / PRE-OP to SAFE-OP or higher, config_init / config_map / config_dc run again
        (mailbox and PDO must be configured; PDOs may have been remapped in PRE-OP) and DC SYNC0
        is re-applied. Returns the performed steps."""
        target = EcState(target)
        steps: list[str] = []
        layout_changed = False
        with self._api_lock:
            m = self._m
            acked = self._ack_errors(m)
            if acked:
                steps.append(f"先確認 {acked} 個 slave 的錯誤 (ACK)")
            raw = m.read_state() & 0x0F
            lowest = EcState(raw) if raw in EcState._value2member_map_ else EcState.NONE
            steps.append(f"目前最低狀態 {lowest.label}")

            if target <= lowest:
                if target < EcState.SAFEOP:
                    self._pd_active = False
                m.state = int(target)
                m.write_state()
                self._expect(m, target, steps)
                return steps

            if lowest < EcState.SAFEOP:
                steps.append("重新設定 slave（config_init → PRE-OP、config_map → SAFE-OP、config_dc）")
                layout_changed = self._reconfigure(m, allow_layout_change)
                if layout_changed:
                    steps.append("process data 配置已更新")
                if self._dc_slaves:
                    steps.append(f"重新啟用 {len(self._dc_slaves)} 個 slave 的 DC SYNC0")
                self._expect(m, EcState.SAFEOP, steps)
                if target == EcState.PREOP:
                    self._pd_active = False
                    m.state = int(EcState.PREOP)
                    m.write_state()
                    self._expect(m, EcState.PREOP, steps)

            if target == EcState.OP:
                # process data is already cycling, which slaves need to see before OP
                m.state = int(EcState.OP)
                m.write_state()
                self._expect(m, EcState.OP, steps)
        if layout_changed:
            for cb in list(self.layout_changed):
                cb()
        return steps

    def _wait_state(self, m, target: EcState, timeout_s: float, raise_on_timeout=True) -> list[int]:
        """Polls read_state() until every slave is in ``target`` (replaces state_check, which
        holds the GIL for the whole timeout)."""
        deadline = time.monotonic() + timeout_s
        while True:
            m.read_state()
            states = [s.state for s in m.slaves]
            if all((st & 0x0F) == target for st in states):
                return states
            if time.monotonic() >= deadline:
                return states
            time.sleep(0.01)

    def _expect(self, m, target: EcState, steps: list[str]) -> None:
        states = self._wait_state(m, target, STATE_TIMEOUT_S)
        bad = [(i + 1, st) for i, st in enumerate(states) if (st & 0x0F) != target]
        if bad:
            detail = "、".join(
                f"#{p} {state_label(st)} (AL 0x{m.slaves[p - 1].al_status:04X})" for p, st in bad)
            raise EtherCatError(
                f"要求 {target.label} 但 {int(STATE_TIMEOUT_S * 1000)} ms 內沒有全部到達：{detail}。"
                + (f"（已執行：{'、'.join(steps)}）" if steps else ""))
        steps.append(f"→ {target.label}")
        self._pd_active = target >= EcState.SAFEOP

    def _config_map(self, m) -> int:
        ps = self._ps or load_pysoem()
        try:
            return m.config_map()
        except ps.ConfigMapError as e:
            raise EtherCatError(f"config_map 失敗：{e}") from e

    def _reconfigure(self, m, allow_layout_change: bool) -> bool:
        expected = len(self.layouts)
        with self._pd_lock:
            saved = [bytearray(b) for b in self._outputs]
        self._stop_cyclic()        # config_map reallocates the IOmap the cyclic thread uses
        try:
            count = m.config_init(release_gil=True)
            if count != expected:
                raise EtherCatError(
                    f"重新設定時找到 {count} 個 slave，連線時是 {expected} 個；bus 有變動，請斷線後重新連線。")
            used = self._config_map(m)
            m.config_dc()
            for pos, shift in self._dc_slaves.items():
                m.slaves[pos - 1].dc_sync(True, self._cycle_ns(), shift)
            old = list(self.layouts)
            self._build_layout(m, used)
            changed = old != self.layouts
            if changed and not allow_layout_change:
                raise EtherCatError("重新設定後 IO 配置不同，請斷線後重新連線。")
            if not changed:
                with self._pd_lock:
                    self._outputs = saved
        finally:
            # After a failed reconfiguration keep cycling only if the slave list still matches
            # the layout (otherwise the user has to reconnect, as the error message says).
            if len(m.slaves) == len(self.layouts) and all(
                    len(s.output) == l.output_bytes and len(s.input) == l.input_bytes
                    for s, l in zip(m.slaves, self.layouts)):
                self._start_cyclic()
        return changed

    def acknowledge_errors(self) -> int:
        with self._api_lock:
            return self._ack_errors(self._m)

    @staticmethod
    def _ack_errors(m) -> int:
        m.read_state()
        n = 0
        for s in m.slaves:
            if s.state & STATE_ACK:
                s.state = (s.state & 0x0F) | STATE_ACK
                s.write_state()
                n += 1
        return n

    def read_slaves(self) -> list[SlaveInfo]:
        with self._api_lock:
            m = self._m
            m.read_state()
            ps = self._ps or load_pysoem()
            out = []
            for i, s in enumerate(m.slaves):
                lay = self.layouts[i]
                al = s.al_status
                out.append(SlaveInfo(
                    position=i + 1, name=s.name, state=s.state, al_status=al,
                    al_status_text=ps.al_status_code_to_string(al) if al else "",
                    vendor=s.man, product=s.id, revision=s.rev,
                    config_address=self._config_addr[i] if i < len(self._config_addr) else 0,
                    output_bits=lay.output_bits, input_bits=lay.input_bits, has_dc=lay.has_dc))
            return out

    # ------------------------------------------------------------------ SDO

    def sdo_read(self, slave: int, index: int, sub: int, size: int = 0) -> bytes:
        with self._api_lock:
            s = self._m.slaves[slave - 1]
            try:
                if size:
                    return bytes(s.sdo_read(index, sub, size, release_gil=True))
                return bytes(s.sdo_read(index, sub, release_gil=True))
            except Exception as e:
                raise EtherCatError(describe_exception(e)) from e

    def sdo_write(self, slave: int, index: int, sub: int, data: bytes) -> None:
        with self._api_lock:
            s = self._m.slaves[slave - 1]
            try:
                s.sdo_write(index, sub, bytes(data), release_gil=True)
            except Exception as e:
                raise EtherCatError(describe_exception(e)) from e

    # ------------------------------------------------------------------ process image

    def set_output_bit(self, slave: int, bit: int, value: bool) -> None:
        with self._pd_lock:
            buf = self._outputs[slave - 1]
            if bit // 8 >= len(buf):
                return
            if value:
                buf[bit // 8] |= 1 << (bit % 8)
            else:
                buf[bit // 8] &= ~(1 << (bit % 8)) & 0xFF

    def clear_outputs(self, skip: Sequence[int] = ()) -> None:
        """Zeroes all outputs except the slaves in ``skip`` (e.g. one owned by an axis)."""
        with self._pd_lock:
            for i, buf in enumerate(self._outputs):
                if i + 1 not in skip:
                    buf[:] = bytes(len(buf))

    def snapshot_outputs(self) -> list[bytes]:
        with self._pd_lock:
            return [bytes(b) for b in self._outputs]

    def snapshot_inputs(self) -> list[bytes]:
        with self._pd_lock:
            return list(self._inputs)

    def drain_events(self) -> list[CycleEvent]:
        out = []
        while self._events:
            try:
                out.append(self._events.popleft())
            except IndexError:
                break
        return out

    def get_stats(self) -> CycleStats:
        return CycleStats(self._cycles, self._last_wkc, self._expected_wkc, self._wkc_errors,
                          self._lost_frames, self._avg_us, self._max_us,
                          self._dc_sync_active, self._dc_deviation_ns)

    def reset_stats(self) -> None:
        with self._stats_lock:
            self._wkc_errors = 0
            self._lost_frames = 0
            self._max_us = 0.0

    # ------------------------------------------------------------------ internals

    def _reset_counters(self) -> None:
        self._cycles = 0
        self._last_wkc = 0
        self._wkc_errors = 0
        self._lost_frames = 0
        self._avg_us = 0.0
        self._max_us = 0.0

    def _build_layout(self, m, used_bytes: int) -> None:
        layouts = []
        addrs = []
        for i, s in enumerate(m.slaves):
            ob, ib = len(s.output), len(s.input)
            # ESC feature register 0x0008 bit 2 = Distributed Clocks available
            try:
                feat = struct.unpack("<H", bytes(s._fprd(0x0008, 2)))[0]
                has_dc = bool(feat & 0x0004)
            except Exception:
                has_dc = False
            try:
                addr = struct.unpack("<H", bytes(s._fprd(0x0010, 2)))[0]
            except Exception:
                addr = 0
            layouts.append(SlaveLayout(i + 1, ob, ib, has_dc))
            addrs.append(addr)
        total = sum(l.output_bytes + l.input_bytes for l in layouts)
        if total > used_bytes:
            raise EtherCatError(f"process data ({total} bytes) 超過 config_map 回報的 IOmap 大小 {used_bytes} bytes。")
        self._expected_wkc = m.expected_wkc
        with self._pd_lock:
            self._outputs = [bytearray(l.output_bytes) for l in layouts]
            self._inputs = [bytes(l.input_bytes) for l in layouts]
        self.layouts = layouts
        self._config_addr = addrs

    def _start_cyclic(self) -> None:
        with self._stats_lock:
            self._reset_counters()
        # Reduce (not remove) jitter while connected; restored in _stop_cyclic.
        self._saved_priority = winrt.raise_process_priority()
        # A thread waking from sleep must get the GIL; the default 5 ms switch interval would
        # let a busy UI thread delay the cyclic thread by up to 5 ms.
        self._saved_switch = sys.getswitchinterval()
        sys.setswitchinterval(0.0005)
        gc.collect()
        gc.freeze()                    # long-lived objects leave the GC generations
        self._running = True
        self._thread = threading.Thread(target=self._cyclic_loop, name="EtherCAT cyclic", daemon=True)
        self._thread.start()

    def _stop_cyclic(self) -> None:
        self._running = False
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(2.0)
        if self._saved_switch is not None:
            sys.setswitchinterval(self._saved_switch)
            self._saved_switch = None
        gc.unfreeze()
        winrt.restore_process_priority(self._saved_priority)
        self._saved_priority = None

    def _cyclic_loop(self) -> None:
        m = self._master
        slaves = list(m.slaves)
        n_sl = len(slaves)
        out_idx = [i for i, l in enumerate(self.layouts) if l.output_bytes]
        in_idx = [i for i, l in enumerate(self.layouts) if l.input_bytes]
        expected = self._expected_wkc
        period = self.cycle_time_ms * 1_000_000
        cycle_ns = period
        spike_us = self.cycle_time_ms * 1000 * SPIKE_FACTOR
        pc = time.perf_counter_ns
        sleep = time.sleep
        winrt.time_begin_period(1)
        winrt.set_current_thread_priority(winrt.THREAD_PRIORITY_HIGHEST)
        try:
            last = pc()
            deadline = last
            dc_integral = 0
            dt = self.cycle_time_ms / 1000.0
            last_gc = _gc_runs
            inputs = [bytes(l.input_bytes) for l in self.layouts]
            while self._running:
                # Deadline pacing (handoff §6.8). Python >= 3.11 sleeps on a high-resolution
                # waitable timer on Windows; sleep coarsely, then yield for the last stretch.
                deadline += period
                now = pc()
                if now - deadline > period * 10:
                    deadline = now                      # fell far behind: resynchronise
                while True:
                    rem = deadline - pc()
                    if rem <= 0:
                        break
                    if rem > 700_000:
                        sleep((rem - 500_000) / 1e9)
                    else:
                        sleep(0)

                with self._pd_lock:
                    outs = self._outputs
                    for i in out_idx:
                        slaves[i].output = bytes(outs[i])

                tx = pc()
                m.send_processdata(release_gil=True)
                wkc = m.receive_processdata(2000, release_gil=True)
                rx_us = (pc() - tx) / 1000.0

                # wkc < 0: no frame (EC_NOFRAME); 0..expected-1: some slave did not process
                # its data. Only counted while every slave is expected in SAFE-OP or OP.
                pd_active = self._pd_active
                lost = wkc < 0
                wkc_err = (not lost) and pd_active and wkc < expected
                if lost:
                    self._lost_frames += 1
                elif wkc_err:
                    self._wkc_errors += 1
                self._last_wkc = wkc

                if not lost:
                    for i in in_idx:
                        inputs[i] = slaves[i].input
                handlers = self._handlers
                with self._pd_lock:
                    self._inputs = list(inputs)
                    for h in handlers:
                        try:
                            h.on_cycle(self._outputs, self._inputs, dt, pd_active and not lost and not wkc_err)
                        except Exception as e:  # keep cycling; UI shows the message
                            self._handler_error = f"{type(e).__name__}: {e}"

                # DC: put the frame half a cycle away from SYNC0 (PI as in SOEM ec_sync()).
                if self._dc_sync_active and not lost:
                    dc_time = m.dc_time
                    delta = (dc_time - cycle_ns // 2) % cycle_ns
                    if delta > cycle_ns // 2:
                        delta -= cycle_ns
                    dc_integral += (delta > 0) - (delta < 0)
                    off = int(-(delta / 100) - (dc_integral / 20))
                    off = max(-cycle_ns // 4, min(cycle_ns // 4, off))
                    deadline += off
                    self._dc_deviation_ns = delta

                now = pc()
                us = (now - last) / 1000.0
                last = now
                self._cycles += 1
                n = self._cycles
                g = _gc_runs
                if n > 1:
                    dt = min(max(us / 1e6, 0.0001), 0.05)
                    self._avg_us += (us - self._avg_us) / min(n, 1000)
                    if us > self._max_us:
                        self._max_us = us
                    if lost or wkc_err or us > spike_us:
                        self._events.append(CycleEvent(datetime.now(), n, wkc, us, rx_us, g - last_gc))
                last_gc = g
        finally:
            winrt.time_end_period(1)
