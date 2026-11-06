"""IO / Slaves tab, connection, status bar and log (port of MainViewModel.cs). tk-free."""
from __future__ import annotations

import string
from dataclasses import dataclass

from ..observable import Command, Observable, computed
from ..services.ethercat import (AdapterInfo, EcState, EtherCatService, SlaveInfo, state_label)
from .axis_vm import AxisViewModel
from .base import LogSink, Ticker, Worker


@dataclass(frozen=True)
class SlaveRow:
    position: int
    name: str
    state: str
    al_status: str
    vendor: str
    product: str
    revision: str
    address: str
    out_bits: int
    in_bits: int
    dc: str
    has_error: bool
    operational: bool

    @property
    def title(self) -> str:
        return f"#{self.position} {self.name}"

    @staticmethod
    def from_info(s: SlaveInfo) -> "SlaveRow":
        st = s.state & 0x0F
        al = "-" if s.al_status == 0 else f"0x{s.al_status:04X} {s.al_status_text}".strip()
        return SlaveRow(s.position, s.name, state_label(s.state), al, f"0x{s.vendor:08X}",
                        f"0x{s.product:08X}", f"0x{s.revision:08X}", f"0x{s.config_address:04X}",
                        s.output_bits, s.input_bits, "有" if s.has_dc else "-", s.has_error,
                        st == EcState.OP and not s.has_error)


def parse_sdo_address(index: str, sub: str) -> tuple[int, int]:
    """Index: hex (with or without 0x). Subindex: decimal or 0x-prefixed hex."""
    idx = index.strip().lower().removeprefix("0x")
    s = sub.strip().lower()
    if not idx or any(ch not in string.hexdigits for ch in idx):
        raise ValueError
    i = int(idx, 16)
    n = int(s[2:], 16) if s.startswith("0x") else int(s, 10)
    if not (0 <= i <= 0xFFFF and 0 <= n <= 0xFF):
        raise ValueError
    return i, n


def parse_hex_bytes(text: str) -> bytes:
    t = text.replace(" ", "").replace("0x", "").replace("0X", "")
    if not t or len(t) % 2:
        raise ValueError
    return bytes.fromhex(t)


def describe_bytes(data: bytes) -> str:
    lines = [f"HEX: {data.hex().upper()}  ({len(data)} bytes)"]
    if len(data) == 1:
        lines.append(f"U8: {data[0]}")
    elif len(data) == 2:
        v = int.from_bytes(data, "little")
        lines.append(f"U16: {v}  (0x{v:04X})")
    elif len(data) == 4:
        v = int.from_bytes(data, "little")
        lines.append(f"U32: {v}  (0x{v:08X})")
    if data and all(b == 0 or 0x20 <= b < 0x7F for b in data):
        lines.append(f"String: {data.decode('ascii').rstrip(chr(0))}")
    return "\n".join(lines)


class MainViewModel:
    def __init__(self, service: EtherCatService | None = None, log: LogSink | None = None,
                 worker: Worker | None = None) -> None:
        self.service = service or EtherCatService()
        self.log = log or LogSink()
        self.worker = worker or Worker()

        # connection
        self.adapters: Observable[tuple[AdapterInfo, ...]] = Observable(())
        self.selected_adapter: Observable[AdapterInfo | None] = Observable(None)
        self.cycle_ms: Observable[str] = Observable("2")
        self.is_connected = Observable(False)
        self.is_busy = Observable(False)

        # slaves / IO
        self.slaves: Observable[tuple[SlaveRow, ...]] = Observable(())
        self.selected_slave: Observable[int | None] = Observable(None)
        self.io_title = Observable("（未選擇 slave）")
        self.out_bits: Observable[tuple[bool, ...]] = Observable(())
        self.in_bits: Observable[tuple[bool, ...]] = Observable(())
        self.in_hex = Observable("")
        self.io_note = Observable("")

        # status bar
        self.stats_text = Observable("未連線")
        self.wkc_ok = Observable(True)

        # SDO
        self.sdo_index = Observable("1018")
        self.sdo_sub = Observable("1")
        self.sdo_data = Observable("")
        self.sdo_result = Observable("")

        self.axis = AxisViewModel(self.service, self.log, self.worker)
        self.outputs_editable = computed(
            lambda c, sel, ax: c and sel is not None and sel != ax,
            self.is_connected, self.selected_slave, self.axis.controlled_slave)

        can_connect = computed(lambda c, b, a: not c and not b and a is not None,
                               self.is_connected, self.is_busy, self.selected_adapter)
        can_use = computed(lambda c, b: c and not b, self.is_connected, self.is_busy)
        disconnected = computed(lambda c, b: not c and not b, self.is_connected, self.is_busy)
        self.cmd_refresh_adapters = Command(self.refresh_adapters, disconnected)
        self.cmd_connect = Command(self.connect, can_connect)
        self.cmd_disconnect = Command(self.disconnect, can_use)
        self.cmd_state = {s.name: Command(lambda s=s: self.request_state(s), can_use)
                          for s in (EcState.INIT, EcState.PREOP, EcState.SAFEOP, EcState.OP)}
        self.cmd_ack = Command(self.ack_errors, can_use)
        self.cmd_reset_stats = Command(self.reset_stats, self.is_connected)
        self.cmd_all_off = Command(self.all_outputs_off, self.is_connected)
        self.cmd_sdo_read = Command(self.sdo_read, can_use)
        self.cmd_sdo_write = Command(self.sdo_write, can_use)

        self._io_ticker = Ticker(0.05, self._io_tick, "ui io tick")
        self._state_ticker = Ticker(0.5, self._refresh_states, "ui state tick")
        self._state_refresh_busy = False
        self.selected_slave.subscribe(lambda _: self._on_selection(), immediate=False)
        self.axis.controlled_slave.subscribe(lambda _: self._update_io_note(), immediate=False)
        self.service.layout_changed.append(self._on_layout_changed)

    # ------------------------------------------------------------------ startup / shutdown

    def start(self) -> None:
        self.log(f"程式資料夾：{self.log.folder}；log 檔：{self.log.path.name}")
        self.refresh_adapters()

    def shutdown(self) -> None:
        self._io_ticker.stop()
        self._state_ticker.stop()
        self.axis.on_bus_closing()
        self.axis.shutdown()
        try:
            self.service.close()
        finally:
            self.worker.shutdown()

    # ------------------------------------------------------------------ connection

    def refresh_adapters(self) -> None:
        prev = self.selected_adapter.value

        def done(adapters: list[AdapterInfo]) -> None:
            self.adapters.value = tuple(adapters)
            keep = next((a for a in adapters if prev and a.name == prev.name), None)
            wired = next((a for a in adapters if any(k in a.desc.lower() for k in ("ethernet", "gbe"))
                          and "virtual" not in a.desc.lower()), None)
            self.selected_adapter.value = keep or wired or (adapters[0] if adapters else None)
            self.log(f"找到 {len(adapters)} 張網卡。")

        self.worker.run(EtherCatService.get_adapters, done,
                        lambda e: self.log(f"列舉網卡失敗：{e}"))

    def connect(self) -> None:
        adapter = self.selected_adapter.value
        try:
            cycle = int(self.cycle_ms.value)
            if not 1 <= cycle <= 100:
                raise ValueError
        except ValueError:
            self.log("cyclic 週期請輸入 1–100 的整數 (ms)。")
            return
        if adapter is None:
            return
        self.is_busy.value = True
        self.log(f"連線中：{adapter.desc} …")

        def done(infos: list[SlaveInfo]) -> None:
            self._set_slaves(infos)
            st = self.service.get_stats()
            self.log(f"找到 {len(infos)} 個 slave，預期 WKC = {st.expected_wkc}，cyclic 週期 {cycle} ms。")
            for s in infos:
                self.log(f"  #{s.position} {s.name}  Out {s.output_bits} bits, In {s.input_bits} bits, "
                         f"DC {'有' if s.has_dc else '無'}")
            if cycle < 2:
                self.log("提醒：Python 版 1 ms 週期為「盡力而為」，建議 2 ms 以上（見 docs/HARDWARE.md）。")
            self.is_connected.value = True
            self.is_busy.value = False
            self._io_ticker.start()
            self._state_ticker.start()

        def fail(e: BaseException) -> None:
            self.log(f"連線失敗：{e}")
            self.is_busy.value = False

        self.worker.run(lambda: self.service.open(adapter.name, cycle), done, fail)

    def disconnect(self) -> None:
        self._io_ticker.stop()
        self._state_ticker.stop()
        self.is_busy.value = True
        self.axis.on_bus_closing()

        def finish(_=None, err: BaseException | None = None) -> None:
            self.log(f"斷線時發生錯誤：{err}" if err else "已斷線，slave 已要求回到 INIT。")
            self.slaves.value = ()
            self.selected_slave.value = None
            self.axis.refresh_sources((), bus_open=False)
            self.is_connected.value = False
            self.is_busy.value = False
            self.stats_text.value = "未連線"
            self.wkc_ok.value = True

        self.worker.run(self.service.close, finish, lambda e: finish(err=e))

    # ------------------------------------------------------------------ slaves / state

    def _set_slaves(self, infos: list[SlaveInfo]) -> None:
        rows = tuple(SlaveRow.from_info(s) for s in infos)
        keep = self.selected_slave.value
        self.slaves.value = rows
        self.selected_slave.value = keep if any(r.position == keep for r in rows) else (rows[0].position if rows else None)
        self._on_selection()
        self.axis.refresh_sources(rows, bus_open=True)

    def _on_layout_changed(self) -> None:
        def done(infos):
            self._set_slaves(infos)
            self.log("process data 配置已變更，slave 列表與 IO 面板已更新：")
            for s in infos:
                self.log(f"  #{s.position} {s.name}  Out {s.output_bits} bits, In {s.input_bits} bits")
        self.worker.run(self.service.read_slaves, done, lambda e: self.log(f"更新 slave 列表失敗：{e}"))

    def request_state(self, state: EcState) -> None:
        self.is_busy.value = True
        self.log(f"要求所有 slave 進入 {state.label} …")

        def done(steps):
            self.log("  " + "、".join(steps))
            self.is_busy.value = False
            self._refresh_states()

        def fail(e):
            self.log(f"切換狀態失敗：{e}")
            self.is_busy.value = False
            self._refresh_states()

        self.worker.run(lambda: self.service.change_state(state), done, fail)

    def ack_errors(self) -> None:
        def done(n):
            self.log("沒有處於錯誤狀態的 slave。" if n == 0 else f"已對 {n} 個 slave 送出錯誤確認 (ACK)。")
            self.service.reset_stats()
        self.worker.run(self.service.acknowledge_errors, done, lambda e: self.log(f"ACK 失敗：{e}"))

    def reset_stats(self) -> None:
        self.service.reset_stats()
        self.service.drain_events()
        self.log("統計已重設（最大週期、掉 frame、WKC 不足）。")

    def _refresh_states(self) -> None:
        if not self.is_connected.value or self._state_refresh_busy or not self.service.is_open:
            return
        self._state_refresh_busy = True
        try:
            infos = self.service.read_slaves()
        except Exception as e:  # noqa: BLE001
            self.log(f"讀取狀態失敗：{e}")
            return
        finally:
            self._state_refresh_busy = False
        old = {r.position: r for r in self.slaves.value}
        rows = tuple(SlaveRow.from_info(s) for s in infos)
        for r in rows:
            o = old.get(r.position)
            if r.has_error and (o is None or not o.has_error):
                self.log(f"#{r.position} 進入錯誤狀態：{r.state}，AL status {r.al_status}")
        self.slaves.value = rows

    # ------------------------------------------------------------------ IO

    def _on_selection(self) -> None:
        pos = self.selected_slave.value
        row = next((r for r in self.slaves.value if r.position == pos), None)
        self.io_title.value = row.title if row else "（未選擇 slave）"
        self._update_io_note()
        self._io_tick()

    def _update_io_note(self) -> None:
        pos = self.selected_slave.value
        if pos is not None and pos == self.axis.controlled_slave.value:
            self.io_note.value = "此 slave 由軸分頁控制，輸出唯讀。"
        else:
            self.io_note.value = "點擊輸出切換；只有 OP 狀態下輸出才會真正驅動現場設備。"

    def toggle_output(self, bit: int) -> None:
        pos = self.selected_slave.value
        if not self.outputs_editable.value or pos is None:
            return
        bits = self.out_bits.value
        if bit >= len(bits):
            return
        self.service.set_output_bit(pos, bit, not bits[bit])
        self._io_tick()

    def all_outputs_off(self) -> None:
        ax = self.axis.controlled_slave.value
        self.service.clear_outputs(skip=(ax,) if ax else ())
        self.log("所有輸出已清為 0" + (f"（軸控制中的 #{ax} 除外）。" if ax else "。"))
        self._io_tick()

    def _io_tick(self) -> None:
        if not self.is_connected.value or not self.service.is_open:
            return
        st = self.service.get_stats()
        self.wkc_ok.value = st.last_wkc >= st.expected_wkc
        text = (f"WKC {st.last_wkc}/{st.expected_wkc}  │  週期 avg {st.avg_cycle_us:.0f} / max {st.max_cycle_us:.0f} µs"
                f"  │  cycles {st.cycles:,}  │  掉 frame {st.lost_frames:,}  │  WKC 不足 {st.wkc_errors:,}")
        if st.dc_sync_active:
            text += f"  │  DC 相位偏差 {st.dc_deviation_ns / 1000:.0f} µs"
        self.stats_text.value = text

        events = self.service.drain_events()
        for e in events[:10]:
            self.log(f"[cyclic] cycle {e.cycle:,}: WKC {e.wkc}/{st.expected_wkc}, 週期 {e.period_us:.0f} µs, "
                     f"send→recv {e.rx_us:.0f} µs" + ("，期間發生 GC" if e.gc_runs else ""))
        if len(events) > 10:
            self.log(f"[cyclic] …另外 {len(events) - 10} 筆略過")

        pos = self.selected_slave.value
        if pos is None:
            self.out_bits.value = self.in_bits.value = ()
            self.in_hex.value = ""
            return
        try:
            outs = self.service.snapshot_outputs()[pos - 1]
            ins = self.service.snapshot_inputs()[pos - 1]
        except IndexError:
            return
        self.out_bits.value = tuple(bool(outs[b // 8] >> (b % 8) & 1) for b in range(len(outs) * 8))
        self.in_bits.value = tuple(bool(ins[b // 8] >> (b % 8) & 1) for b in range(len(ins) * 8))
        self.in_hex.value = ins.hex().upper()

    # ------------------------------------------------------------------ SDO

    def _sdo_target(self) -> tuple[int, int, int] | None:
        slave = self.selected_slave.value
        if slave is None:
            self.sdo_result.value = "請先在列表中選擇一個 slave"
            return None
        try:
            i, s = parse_sdo_address(self.sdo_index.value, self.sdo_sub.value)
        except ValueError:
            self.sdo_result.value = "Index 請輸入 hex（例如 1018），SubIndex 請輸入十進位或 0x 開頭的 hex"
            return None
        return slave, i, s

    def sdo_read(self) -> None:
        t = self._sdo_target()
        if t is None:
            return
        slave, i, s = t

        def done(data: bytes):
            self.sdo_result.value = describe_bytes(data)
            self.log(f"SDO 讀取 #{slave} 0x{i:04X}:{s} → {data.hex().upper()}")

        def fail(e):
            self.sdo_result.value = str(e)
            self.log(f"SDO 讀取失敗：{e}")
        self.worker.run(lambda: self.service.sdo_read(slave, i, s), done, fail)

    def sdo_write(self) -> None:
        t = self._sdo_target()
        if t is None:
            return
        slave, i, s = t
        try:
            data = parse_hex_bytes(self.sdo_data.value)
        except ValueError:
            self.sdo_result.value = "寫入資料格式錯誤，請輸入 hex（little-endian），例如 01 00"
            return

        def done(_):
            self.sdo_result.value = f"寫入成功（{len(data)} bytes）"
            self.log(f"SDO 寫入 #{slave} 0x{i:04X}:{s} ← {data.hex().upper()}")

        def fail(e):
            self.sdo_result.value = str(e)
            self.log(f"SDO 寫入失敗：{e}")
        self.worker.run(lambda: self.service.sdo_write(slave, i, s, data), done, fail)
