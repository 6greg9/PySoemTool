"""Axis (CiA 402) tab: source / PDO / DC, drive commands and modes, status and trend."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..mvvm import (Bindings, UiDispatcher, bind_command, bind_enabled, bind_entry, bind_grid_visible,
                    bind_text)
from ..viewmodels.axis_vm import AxisViewModel
from .common import (bind_check, bind_combobox, bind_lines, bind_repeat, bind_style_latest,
                     bind_text_latest)

VISUAL_STYLE = {   # axis visual state -> style (HMI: only abnormal states stand out)
    "inactive": "StateIdle.TLabel",
    "ready": "StateIdle.TLabel",
    "enabled": "StateActive.TLabel",
    "warning": "StateAttention.TLabel",
    "fault": "AlarmHigh.TLabel",
}
TREND_H = 70


def _panel(master, theme, title: str) -> ttk.Frame:
    f = ttk.Frame(master, style="Panel.TFrame", padding=theme.px("space-3"))
    ttk.Label(f, text=title, style="Panel.Section.TLabel").grid(row=0, column=0, columnspan=6, sticky="w",
                                                                pady=(0, theme.px("space-2")))
    return f


def _field(parent, theme, row: int, col: int, text: str, width: int = 8) -> ttk.Entry:
    sp = theme.px
    ttk.Label(parent, text=text, style="Panel.TLabel").grid(row=row, column=col, sticky="w",
                                                            padx=(0, sp("space-1")), pady=sp("space-1"))
    e = ttk.Entry(parent, width=width, style="Setpoint.TEntry")
    e.grid(row=row, column=col + 1, sticky="w", padx=(0, sp("space-3")), pady=sp("space-1"))
    return e


class AxisView(ttk.Frame):
    def __init__(self, master: tk.Misc, theme):
        sp = theme.px
        super().__init__(master, padding=sp("space-2"))
        self.theme = theme
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=0)
        self.columnconfigure(2, weight=1)
        self.rowconfigure(0, weight=1)

        # ---------------- left: source, PDO, DC
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, sp("space-2")))
        left.rowconfigure(1, weight=1)
        src = _panel(left, theme, "軸來源")
        src.grid(row=0, column=0, sticky="ew")
        self.cmb_source = ttk.Combobox(src, width=22)
        self.cmb_source.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, sp("space-2")))
        self.btn_attach = ttk.Button(src, text="連接軸")
        self.btn_attach.grid(row=2, column=0, sticky="ew", padx=(0, sp("space-1")))
        self.btn_detach = ttk.Button(src, text="分離")
        self.btn_detach.grid(row=2, column=1, sticky="ew", padx=(0, sp("space-1")))
        self.btn_pdo = ttk.Button(src, text="套用建議 PDO")
        self.btn_pdo.grid(row=2, column=2, sticky="ew")
        self.lbl_attached = ttk.Label(src, text="", style="Panel.Caption.TLabel")
        self.lbl_attached.grid(row=3, column=0, columnspan=3, sticky="w", pady=(sp("space-1"), 0))

        pdo = _panel(left, theme, "PDO 配置")
        pdo.grid(row=1, column=0, sticky="nsew", pady=(sp("space-2"), 0))
        pdo.columnconfigure(0, weight=1)
        pdo.rowconfigure(2, weight=1)
        pdo.rowconfigure(4, weight=1)
        mono = theme.font("id")
        lb_opts = dict(height=4, width=25, font=mono, background=theme.color("surface-field"),
                       foreground=theme.color("ink"), highlightthickness=0, borderwidth=1, relief="solid",
                       activestyle="none")
        ttk.Label(pdo, text="RxPDO（master → drive）", style="Panel.Caption.TLabel").grid(row=1, column=0, sticky="w")
        self.lb_rx = tk.Listbox(pdo, **lb_opts)
        self.lb_rx.grid(row=2, column=0, sticky="nsew")
        ttk.Label(pdo, text="TxPDO（drive → master）", style="Panel.Caption.TLabel").grid(
            row=3, column=0, sticky="w", pady=(sp("space-1"), 0))
        self.lb_tx = tk.Listbox(pdo, **lb_opts)
        self.lb_tx.grid(row=4, column=0, sticky="nsew")
        self.lbl_note = ttk.Label(pdo, text="", style="Panel.Caption.TLabel", wraplength=250, justify="left")
        self.lbl_note.grid(row=5, column=0, sticky="w", pady=(sp("space-1"), 0))

        dc = _panel(left, theme, "DC SYNC0（OP 以下設定）")
        dc.grid(row=2, column=0, sticky="ew", pady=(sp("space-2"), 0))
        self.chk_dc = ttk.Checkbutton(dc, text="啟用 SYNC0（= cyclic 週期）", style="Panel.TCheckbutton")
        self.chk_dc.grid(row=1, column=0, columnspan=3, sticky="w")
        self.ent_dc_shift = _field(dc, theme, 2, 0, "shift (µs)", 8)
        self.btn_dc = ttk.Button(dc, text="套用")
        self.btn_dc.grid(row=2, column=2, sticky="e")

        # ---------------- middle: drive commands and modes
        mid = ttk.Frame(self)
        mid.grid(row=0, column=1, sticky="nsew", padx=(0, sp("space-2")))
        mid.columnconfigure(0, weight=1)
        drv = _panel(mid, theme, "驅動器")
        drv.grid(row=0, column=0, sticky="ew")
        self.btn_enable = ttk.Button(drv, text="啟用", style="Compact.TButton")
        self.btn_disable = ttk.Button(drv, text="停用", style="Compact.TButton")
        self.btn_quick_stop = ttk.Button(drv, text="快速停止", style="Compact.TButton")
        self.btn_fault_reset = ttk.Button(drv, text="錯誤重置", style="Compact.TButton")
        self.btn_stop = ttk.Button(drv, text="停止", style="Compact.TButton")
        self.btn_inject = ttk.Button(drv, text="注入錯誤", style="Compact.TButton")
        for i, b in enumerate((self.btn_enable, self.btn_disable, self.btn_quick_stop,
                               self.btn_fault_reset, self.btn_stop, self.btn_inject)):
            b.grid(row=1 + i // 3, column=i % 3, sticky="ew", padx=(0, sp("space-1")), pady=(0, sp("space-1")))
        for col in range(3):
            drv.columnconfigure(col, weight=1)

        mode = _panel(mid, theme, "運動模式")
        mode.grid(row=1, column=0, sticky="nsew", pady=(sp("space-2"), 0))
        mode.columnconfigure(0, weight=1)
        top = ttk.Frame(mode, style="PanelBody.TFrame")
        top.grid(row=1, column=0, sticky="ew")
        self.cmb_mode = ttk.Combobox(top, width=16)
        self.cmb_mode.grid(row=0, column=0, sticky="w", padx=(0, sp("space-2")))
        self.btn_apply_mode = ttk.Button(top, text="切換")
        self.btn_apply_mode.grid(row=0, column=1, sticky="w")

        def box(row):
            f = ttk.Frame(mode, style="PanelBody.TFrame")
            f.grid(row=row, column=0, sticky="ew", pady=(sp("space-2"), 0))
            f.columnconfigure(2, weight=1)
            return f

        self.frm_pp = box(2)
        self.ent_pp_target = _field(self.frm_pp, theme, 0, 0, "目標 607A")
        self.ent_pp_vel = _field(self.frm_pp, theme, 1, 0, "速度 6081")
        self.ent_pp_acc = _field(self.frm_pp, theme, 2, 0, "加速 6083")
        self.ent_pp_dec = _field(self.frm_pp, theme, 3, 0, "減速 6084")
        self.chk_pp_rel = ttk.Checkbutton(self.frm_pp, text="相對移動 (bit 6)", style="Panel.TCheckbutton")
        self.chk_pp_rel.grid(row=4, column=0, columnspan=2, sticky="w")
        self.btn_pp = ttk.Button(self.frm_pp, text="移動")
        self.btn_pp.grid(row=3, column=2, rowspan=2, sticky="se")

        self.frm_pv = box(3)
        self.ent_pv_vel = _field(self.frm_pv, theme, 0, 0, "速度 60FF")
        self.ent_pv_acc = _field(self.frm_pv, theme, 1, 0, "加速 6083")
        self.ent_pv_dec = _field(self.frm_pv, theme, 2, 0, "減速 6084")
        self.btn_pv = ttk.Button(self.frm_pv, text="啟動")
        self.btn_pv.grid(row=2, column=2, sticky="se")

        self.frm_hm = box(4)
        self.ent_hm_method = _field(self.frm_hm, theme, 0, 0, "方法 6098", 6)
        self.ent_hm_sw = _field(self.frm_hm, theme, 1, 0, "搜尋 6099:1")
        self.ent_hm_zero = _field(self.frm_hm, theme, 2, 0, "歸零 6099:2")
        self.ent_hm_acc = _field(self.frm_hm, theme, 3, 0, "加速 609A")
        self.ent_hm_offset = _field(self.frm_hm, theme, 4, 0, "偏移 607C")
        self.btn_hm = ttk.Button(self.frm_hm, text="開始復歸")
        self.btn_hm.grid(row=4, column=2, sticky="se")
        ttk.Label(self.frm_hm, text="方法 35 / 37：以目前位置為原點，不會移動。", style="Panel.Caption.TLabel", wraplength=250).grid(
            row=5, column=0, columnspan=3, sticky="w")

        self.frm_csp = box(5)
        self.ent_csp_target = _field(self.frm_csp, theme, 0, 0, "目標位置")
        self.ent_csp_vel = _field(self.frm_csp, theme, 1, 0, "速度")
        self.ent_csp_acc = _field(self.frm_csp, theme, 2, 0, "加減速")
        self.chk_csp_rel = ttk.Checkbutton(self.frm_csp, text="相對移動", style="Panel.TCheckbutton")
        self.chk_csp_rel.grid(row=3, column=0, columnspan=2, sticky="w")
        self.btn_csp = ttk.Button(self.frm_csp, text="移動")
        self.btn_csp.grid(row=2, column=2, rowspan=2, sticky="se")

        self.frm_csv = box(6)
        self.ent_csv_vel = _field(self.frm_csv, theme, 0, 0, "速度")
        self.ent_csv_acc = _field(self.frm_csv, theme, 1, 0, "加減速")
        self.btn_csv = ttk.Button(self.frm_csv, text="啟動")
        self.btn_csv.grid(row=1, column=2, sticky="se")

        self.frm_jog = box(7)
        self.ent_jog_vel = _field(self.frm_jog, theme, 0, 0, "寸動速度")
        self.ent_jog_acc = _field(self.frm_jog, theme, 1, 0, "寸動加速")
        jb = ttk.Frame(self.frm_jog, style="PanelBody.TFrame")
        jb.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(sp("space-1"), 0))
        jb.columnconfigure(0, weight=1)
        jb.columnconfigure(1, weight=1)
        self.btn_jog_minus = ttk.Button(jb, text="◀ JOG −")
        self.btn_jog_minus.grid(row=0, column=0, sticky="ew", padx=(0, sp("space-1")))
        self.btn_jog_plus = ttk.Button(jb, text="JOG + ▶")
        self.btn_jog_plus.grid(row=0, column=1, sticky="ew")
        ttk.Label(self.frm_jog, text="按住移動，放開 0.4 秒內自動停止。", style="Panel.Caption.TLabel", wraplength=250).grid(
            row=3, column=0, columnspan=3, sticky="w")

        self.lbl_cyclic_warn = ttk.Label(mode, text="", style="StateAttention.TLabel", wraplength=260,
                                         justify="left", anchor="w")
        self.lbl_cyclic_warn.grid(row=8, column=0, sticky="ew", pady=(sp("space-2"), 0))

        # ---------------- right: status, bits, trend
        right = _panel(self, theme, "狀態")
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(1, weight=1)
        right.columnconfigure(3, weight=1)
        self.lbl_state = ttk.Label(right, text="未連接", style="StateIdle.TLabel", anchor="w")
        self.lbl_state.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, sp("space-2")))
        self.lbl_activity = ttk.Label(right, text="", style="Panel.TLabel")
        self.lbl_activity.grid(row=2, column=0, columnspan=4, sticky="w", pady=(0, sp("space-2")))
        self.val: dict[str, ttk.Label] = {}
        fields = (("sw", "Statusword"), ("cw", "Controlword"), ("mode", "模式 0x6061"), ("err", "錯誤 0x603F"),
                  ("pos", "實際位置"), ("tpos", "目標位置"), ("vel", "實際速度"), ("trq", "實際轉矩"))
        for i, (key, text) in enumerate(fields):
            r, cc = 3 + i // 2, (i % 2) * 2
            ttk.Label(right, text=text, style="Panel.Caption.TLabel").grid(row=r, column=cc, sticky="w",
                                                                         padx=(0, sp("space-1")))
            lbl = ttk.Label(right, text="-", style="Panel.Live.TLabel")
            lbl.grid(row=r, column=cc + 1, sticky="w", padx=(0, sp("space-3")))
            self.val[key] = lbl
        self.lbl_comm = ttk.Label(right, text="", style="AlarmHigh.TLabel")
        self.lbl_comm.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(sp("space-1"), 0))

        bits = ttk.Frame(right, style="PanelBody.TFrame")
        bits.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(sp("space-2"), 0))
        self.lbl_bits: list[tuple[ttk.Label, ttk.Label]] = []
        for b in range(16):
            r, cc = b % 8, (b // 8) * 2
            lamp = ttk.Label(bits, text=str(b), style="BitOffSmall.TLabel", width=3)
            lamp.grid(row=r, column=cc, sticky="w", padx=(0, sp("space-1")), pady=(0, 1))
            name = ttk.Label(bits, text="", style="Panel.Caption.TLabel")
            name.grid(row=r, column=cc + 1, sticky="w", padx=(0, sp("space-2")))
            self.lbl_bits.append((lamp, name))

        ttk.Label(right, text="趨勢（10 秒）— 位置 / 速度", style="Panel.Caption.TLabel").grid(
            row=9, column=0, columnspan=4, sticky="w", pady=(sp("space-2"), 0))
        self.cnv_trend = tk.Canvas(right, height=TREND_H, width=200, background=theme.color("surface-field"),
                                   highlightthickness=1, highlightbackground=theme.color("line"))
        self.cnv_trend.grid(row=10, column=0, columnspan=4, sticky="ew")
        self.lbl_trend_range = ttk.Label(right, text="", style="Panel.Caption.TLabel")
        self.lbl_trend_range.grid(row=11, column=0, columnspan=4, sticky="w")
        self.trend_colors = (theme.color("live"), theme.color("alarm-advisory"))

    def draw_trend(self, pos, vel) -> None:
        c = self.cnv_trend
        c.delete("all")
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 20)
        for series, color in zip((pos, vel), self.trend_colors):
            if len(series) < 2:
                continue
            lo, hi = min(series), max(series)
            span = hi - lo
            n = 200
            x0 = (n - len(series)) * w / n
            pts = []
            for i, v in enumerate(series):
                y = h / 2 if span <= 0 else (h - 4) - (v - lo) / span * (h - 8)
                pts += [x0 + i * w / n, y]
            c.create_line(*pts, fill=color, width=2)


def bind_axis(view: AxisView, vm: AxisViewModel, ui: UiDispatcher) -> Bindings:
    b = Bindings(view)
    b.add(bind_combobox(view.cmb_source, vm.sources, vm.selected_source, ui))
    b.add(bind_combobox(view.cmb_mode, _const(vm.modes), vm.selected_mode, ui))
    for btn, cmd in ((view.btn_attach, vm.cmd_attach), (view.btn_detach, vm.cmd_detach),
                     (view.btn_pdo, vm.cmd_apply_pdo), (view.btn_dc, vm.cmd_apply_dc),
                     (view.btn_enable, vm.cmd_enable), (view.btn_disable, vm.cmd_disable),
                     (view.btn_quick_stop, vm.cmd_quick_stop), (view.btn_fault_reset, vm.cmd_fault_reset),
                     (view.btn_stop, vm.cmd_stop), (view.btn_inject, vm.cmd_inject_fault),
                     (view.btn_apply_mode, vm.cmd_apply_mode), (view.btn_pp, vm.cmd_pp_move),
                     (view.btn_pv, vm.cmd_pv_start), (view.btn_hm, vm.cmd_hm_start),
                     (view.btn_csp, vm.cmd_csp_move), (view.btn_csv, vm.cmd_csv_start)):
        b.add(bind_command(btn, cmd, ui))
    b.add(bind_repeat(view.btn_jog_plus, vm.cmd_jog_plus, ui))
    b.add(bind_repeat(view.btn_jog_minus, vm.cmd_jog_minus, ui))
    b.add(bind_grid_visible(view.btn_pdo, vm.source_is_real, ui))
    b.add(bind_grid_visible(view.btn_inject, vm.is_simulated, ui))
    b.add(bind_enabled(view.chk_dc, vm.is_real, ui))

    for ent, obs in ((view.ent_dc_shift, vm.dc_shift_us), (view.ent_pp_target, vm.pp_target),
                     (view.ent_pp_vel, vm.pp_velocity), (view.ent_pp_acc, vm.pp_acc), (view.ent_pp_dec, vm.pp_dec),
                     (view.ent_pv_vel, vm.pv_velocity), (view.ent_pv_acc, vm.pv_acc), (view.ent_pv_dec, vm.pv_dec),
                     (view.ent_hm_method, vm.hm_method), (view.ent_hm_offset, vm.hm_offset),
                     (view.ent_hm_sw, vm.hm_speed_switch), (view.ent_hm_zero, vm.hm_speed_zero),
                     (view.ent_hm_acc, vm.hm_acc), (view.ent_csp_target, vm.csp_target),
                     (view.ent_csp_vel, vm.csp_velocity), (view.ent_csp_acc, vm.csp_acc),
                     (view.ent_csv_vel, vm.csv_velocity), (view.ent_csv_acc, vm.csv_acc),
                     (view.ent_jog_vel, vm.jog_velocity), (view.ent_jog_acc, vm.jog_acc)):
        b.add(bind_entry(ent, obs, ui))
    b.add(bind_check(view.chk_dc, vm.dc_enabled, ui))
    b.add(bind_check(view.chk_pp_rel, vm.pp_relative, ui))
    b.add(bind_check(view.chk_csp_rel, vm.csp_relative, ui))
    for frm, obs in ((view.frm_pp, vm.show_pp), (view.frm_pv, vm.show_pv), (view.frm_hm, vm.show_hm),
                     (view.frm_csp, vm.show_csp), (view.frm_csv, vm.show_csv), (view.frm_jog, vm.show_jog)):
        b.add(bind_grid_visible(frm, obs, ui))
    b.add(bind_text(view.lbl_cyclic_warn, vm.cyclic_warning, ui))
    b.add(bind_grid_visible(view.lbl_cyclic_warn, _truthy(vm.cyclic_warning), ui))

    b.add(bind_lines(view.lb_rx, vm.rx_lines, ui))
    b.add(bind_lines(view.lb_tx, vm.tx_lines, ui))
    b.add(bind_text(view.lbl_note, vm.mapping_note, ui))
    b.add(bind_text(view.lbl_attached, vm.attached_name, ui, fmt=lambda n: f"已連接：{n}" if n else "未連接"))

    b.add(bind_text_latest(view.lbl_state, vm.state_text, ui))
    b.add(bind_style_latest(view.lbl_state, vm.visual, ui, lambda v: VISUAL_STYLE.get(v, "StateIdle.TLabel")))
    b.add(bind_text_latest(view.lbl_activity, vm.activity, ui))
    for key, obs in (("sw", vm.statusword), ("cw", vm.controlword), ("mode", vm.mode_display),
                     ("err", vm.error_code), ("pos", vm.position), ("tpos", vm.target_position),
                     ("vel", vm.velocity), ("trq", vm.torque)):
        b.add(bind_text_latest(view.val[key], obs, ui))
    b.add(bind_text(view.lbl_comm, vm.comm_ok, ui, fmt=lambda ok: "" if ok else "通訊異常：停止送出運動命令"))
    b.add(bind_grid_visible(view.lbl_comm, _not(vm.comm_ok), ui))

    def show_bits(bits):
        for (lamp, name), (text, on) in zip(view.lbl_bits, bits):
            lamp.configure(style="BitOnSmall.TLabel" if on else "BitOffSmall.TLabel")
            name.configure(text=text)
    b.add(vm.status_bits.subscribe(lambda v: ui.post_latest("axis bits", show_bits, v)))
    b.add(vm.trend.subscribe(lambda t: ui.post_latest("axis trend", view.draw_trend, *t)))
    b.add(bind_text_latest(view.lbl_trend_range, vm.trend_range, ui))
    return b


def _const(v):
    from ..mvvm import Observable
    return Observable(v)


def _truthy(obs):
    from ..mvvm import computed
    return computed(bool, obs)


def _not(obs):
    from ..mvvm import computed
    return computed(lambda v: not v, obs)


def preview(master, ui):
    from ..viewmodels.axis_vm import MODES
    from ..viewmodels.main_vm import MainViewModel
    import tempfile
    from pathlib import Path
    from ..viewmodels.base import LogSink
    _preview_vm = lambda: MainViewModel(log=LogSink(Path(tempfile.gettempdir())))
    vm = _preview_vm().axis
    view = AxisView(master, master.theme)
    bind_axis(view, vm, ui)

    def sim(mode_idx, *script):
        """Attach the simulated axis, stop its thread and step it deterministically."""
        vm.attach()
        vm._sim_runner.dispose()
        k = vm._controller

        def step(n=1):
            for _ in range(n):
                k.on_cycle([], [], 0.001, True)
        step(2)
        for fn in script:
            fn(k, step)
        vm.selected_mode.value = MODES[mode_idx]
        for _ in range(20):          # fill the trend a little
            step(25)
            vm._tick()

    def enable(k, step):
        k.enable()
        step(10)

    def csp(k, step):
        k.set_mode(MODES[3].mode)
        step(5)
        k.move(30000, False, 20000, 50000, 50000)
        step(200)

    def fault(k, step):
        vm._sim_drive.inject_fault(0x2310)
        step(10)

    def real_rejected():
        row = type("R", (), {"position": 1, "name": "DSNX16Y16L-MW"})()
        vm.refresh_sources([row], bus_open=True)
        vm.selected_source.value = vm.sources.value[1]
        vm.mapping_note.value = "連接失敗：PDO 中沒有 0x6040、0x6041，無法以 CiA 402 控制。可用「套用建議 PDO」設定。"

    def homing():
        vm.selected_mode.value = MODES[2]

    return view, {"empty": lambda: None, "sim_pp": lambda: sim(0, enable),
                  "sim_csp": lambda: sim(3, enable, csp), "sim_fault": lambda: sim(1, enable, fault),
                  "real_rejected": real_rejected, "homing_params": homing}
