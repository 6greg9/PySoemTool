"""Main window: connection bar, IO / Slaves and Axis tabs, shared log, status bar."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..mvvm import Bindings, UiDispatcher, bind_command, bind_entry
from ..viewmodels.main_vm import MainViewModel
from .axis import AxisView, bind_axis
from .common import bind_combobox, bind_style_latest, bind_text_latest
from .io_slaves import IoSlavesView, bind_io_slaves

LOG_SCREEN_LINES = 500


class MainView(ttk.Frame):
    def __init__(self, master: tk.Misc, theme):
        sp = theme.px
        super().__init__(master)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=(sp("space-2"), sp("space-2"), sp("space-2"), 0))
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="網卡").grid(row=0, column=0, sticky="w", padx=(0, sp("space-1")))
        self.cmb_adapter = ttk.Combobox(bar, width=40)
        self.cmb_adapter.grid(row=0, column=1, sticky="ew", padx=(0, sp("space-1")))
        self.btn_refresh = ttk.Button(bar, text="重新整理")
        self.btn_refresh.grid(row=0, column=2, padx=(0, sp("space-3")))
        ttk.Label(bar, text="週期 (ms)").grid(row=0, column=3, padx=(0, sp("space-1")))
        self.spn_cycle = ttk.Spinbox(bar, from_=1, to=100, width=4, style="Setpoint.TSpinbox")
        self.spn_cycle.grid(row=0, column=4, padx=(0, sp("space-3")))
        self.btn_connect = ttk.Button(bar, text="連線")
        self.btn_connect.grid(row=0, column=5, padx=(0, sp("space-1")))
        self.btn_disconnect = ttk.Button(bar, text="斷線")
        self.btn_disconnect.grid(row=0, column=6)

        self.nb = ttk.Notebook(self)
        self.nb.grid(row=1, column=0, sticky="nsew", padx=sp("space-2"), pady=(sp("space-2"), 0))
        self.io_view = IoSlavesView(self.nb, theme)
        self.axis_view = AxisView(self.nb, theme)
        self.nb.add(self.io_view, text="IO / Slaves")
        self.nb.add(self.axis_view, text="軸 (CiA 402)")

        logf = ttk.Frame(self, padding=(sp("space-2"), sp("space-1"), sp("space-2"), 0))
        logf.grid(row=2, column=0, sticky="ew")
        logf.columnconfigure(0, weight=1)
        self.lb_log = tk.Listbox(logf, height=4, font=theme.font("caption"), activestyle="none",
                                 background=theme.color("surface-field"), foreground=theme.color("ink"),
                                 highlightthickness=0, borderwidth=1, relief="solid")
        self.lb_log.grid(row=0, column=0, sticky="ew")
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.lb_log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.lb_log.configure(yscrollcommand=sb.set)

        status = ttk.Frame(self, padding=sp("space-2"))
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(1, weight=1)
        self.lbl_wkc = ttk.Label(status, text="  ", style="StateIdle.TLabel", width=8)
        self.lbl_wkc.grid(row=0, column=0, padx=(0, sp("space-2")))
        self.lbl_stats = ttk.Label(status, text="未連線", style="Live.TLabel", font=theme.font("id"))
        self.lbl_stats.grid(row=0, column=1, sticky="w")
        self.btn_reset_stats = ttk.Button(status, text="重設統計")
        self.btn_reset_stats.grid(row=0, column=2)


def bind_main(view: MainView, vm: MainViewModel, ui: UiDispatcher) -> Bindings:
    b = Bindings(view)
    b.add(bind_combobox(view.cmb_adapter, vm.adapters, vm.selected_adapter, ui))
    b.add(bind_command(view.btn_refresh, vm.cmd_refresh_adapters, ui))
    b.add(bind_command(view.btn_connect, vm.cmd_connect, ui))
    b.add(bind_command(view.btn_disconnect, vm.cmd_disconnect, ui))
    b.add(bind_command(view.btn_reset_stats, vm.cmd_reset_stats, ui))
    b.add(bind_entry(view.spn_cycle, vm.cycle_ms, ui))
    b.add(vm.is_connected.subscribe(lambda c: ui.post(view.spn_cycle.state, ["disabled"] if c else ["!disabled"])))
    b.add(vm.is_connected.subscribe(lambda c: ui.post(view.cmb_adapter.configure, state="disabled" if c else "readonly")))
    b.add(bind_text_latest(view.lbl_stats, vm.stats_text, ui))
    b.add(bind_style_latest(view.lbl_wkc, _pair(vm), ui,
                            lambda p: "StateIdle.TLabel" if not p[0] else ("StateDone.TLabel" if p[1] else "AlarmHigh.TLabel")))
    b.add(bind_text_latest(view.lbl_wkc, _pair(vm), ui,
                           lambda p: "—" if not p[0] else ("WKC OK" if p[1] else "WKC !")))

    lb = view.lb_log

    def append(line: str) -> None:
        lb.insert("end", line)
        over = lb.size() - LOG_SCREEN_LINES
        if over > 0:
            lb.delete(0, over - 1)
        # scroll after Tk has processed the insert (handoff §6.24)
        lb.after_idle(lambda: lb.see("end"))

    b.add(vm.log.subscribe(lambda line: ui.post(append, line)))
    bind_io_slaves(view.io_view, vm, ui)
    bind_axis(view.axis_view, vm.axis, ui)
    return b


def _pair(vm: MainViewModel):
    from ..mvvm import computed
    return computed(lambda c, ok: (c, ok), vm.is_connected, vm.wkc_ok)


def preview_vm() -> MainViewModel:
    """VM for previews: no hardware, log file in the temp folder."""
    import tempfile
    from pathlib import Path
    from ..viewmodels.base import LogSink
    return MainViewModel(log=LogSink(Path(tempfile.gettempdir())))


def preview(master, ui):
    vm = preview_vm()
    view = MainView(master, master.theme)
    bind_main(view, vm, ui)

    def connected(ok=True):
        vm.is_connected.value = True
        vm.wkc_ok.value = ok
        vm.stats_text.value = ("WKC 6/6   週期 avg 2000 µs / max 4521 µs   cycles 15,001   掉 frame 0   WKC 不足 0"
                               "   DC 相位偏差 -39 µs") if ok else \
            "WKC 4/6   週期 avg 2003 µs / max 9120 µs   cycles 15,001   掉 frame 12   WKC 不足 3"
        for i in range(12):
            vm.log(f"範例 log 行 {i}")

    def axis_tab():
        connected()
        view.nb.select(1)
        vm.axis.attach()                 # simulated axis
        vm.axis.selected_mode.value = vm.axis.modes[2]   # HM: tallest parameter block
        vm.axis.cmd_enable()

    return view, {"empty": lambda: None, "connected": connected, "wkc_low": lambda: connected(False),
                  "axis_tab": axis_tab}
